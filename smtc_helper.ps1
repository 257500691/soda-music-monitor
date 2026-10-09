# 汽水音乐监测面板 - 系统信息采集器
# 通过 Windows 媒体会话(SMTC)读取正在播放的歌曲，并采集进程/音频电平，
# 结果以 JSON 原子写入文件，供 panel.py 读取（避免管道限制）。
param(
    [Parameter(Mandatory = $true)][string]$OutPath,
    [string]$CoverPath = "",
    [int]$IntervalMs = 700,
    [switch]$Once
)

$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $t = $m.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

# ---------- 音频峰值电平（Core Audio，主输出端点） ----------
$meterReady = $false
try {
    $code = @'
using System;
using System.Runtime.InteropServices;

[Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
[ComImport]
public class MMDeviceEnumeratorComObject { }

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator
{
    int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
    int GetDevice(string id, out IMMDevice device);
    int RegisterEndpointNotificationCallback(IntPtr client);
    int UnregisterEndpointNotificationCallback(IntPtr client);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice
{
    int Activate(ref Guid iid, int clsCtx, IntPtr activationParams,
                 [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    int OpenPropertyStore(int access, out IntPtr properties);
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    int GetState(out int state);
}

[Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioMeterInformation
{
    int GetPeakValue(out float peak);
    int GetMeteringChannelCount(out int channelCount);
    int GetChannelsPeakValues(int channelCount, [Out] float[] peakValues);
    int QueryHardwareSupport(out int hardwareSupportMask);
}

public static class AudioPeak
{
    public static float Get()
    {
        IMMDeviceEnumerator enumerator =
            (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice device;
        // eRender = 0, eMultimedia = 1
        if (enumerator.GetDefaultAudioEndpoint(0, 1, out device) != 0) return -1f;
        Guid iid = typeof(IAudioMeterInformation).GUID;
        object iface;
        // CLSCTX_ALL = 23
        if (device.Activate(ref iid, 23, IntPtr.Zero, out iface) != 0) return -1f;
        IAudioMeterInformation meter = (IAudioMeterInformation)iface;
        float peak;
        if (meter.GetPeakValue(out peak) != 0) return -1f;
        return peak;
    }
}
'@
    Add-Type -TypeDefinition $code -Language CSharp | Out-Null
    [void][AudioPeak]::Get()
    $meterReady = $true
}
catch {
    $meterReady = $false
}

# ---------- SMTC 会话管理器 ----------
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$smtcType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
$rasType = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]

try {
    $manager = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
}
catch {
    $manager = $null
}

function Get-SodaSession {
    if (-not $manager) { return $null }
    $sessions = $manager.GetSessions()
    foreach ($s in $sessions) {
        $id = $s.SourceAppUserModelId
        if ($id -match '汽水' -or $id -match '(?i)soda') { return $s }
    }
    return $null
}

function Get-SodaProcess {
    $names = @('Soda Music', 'SodaMusic', 'SodaMusicLauncher')
    $procs = Get-Process | Where-Object { $names -contains $_.ProcessName }
    if (-not $procs) { return $null }
    $main = $procs | Sort-Object WorkingSet64 -Descending | Select-Object -First 1
    $ioBytes = 0
    foreach ($p in $procs) {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)"
        if ($cim) {
            $ioBytes += [int64]($cim.ReadTransferCount + $cim.WriteTransferCount + $cim.OtherTransferCount)
        }
    }
    return [pscustomobject]@{
        pid         = $main.Id
        count       = @($procs).Count
        cpu_seconds = [math]::Round((($procs | Measure-Object CPU -Sum).Sum), 1)
        mem_mb      = [math]::Round((($procs | Measure-Object WorkingSet64 -Sum).Sum / 1MB), 1)
        io_bytes    = $ioBytes
        path        = $main.Path
    }
}

function Write-State($state) {
    $json = $state | ConvertTo-Json -Depth 6 -Compress
    $tmp = "$OutPath.tmp"
    try {
        [System.IO.File]::WriteAllText($tmp, $json, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tmp -Destination $OutPath -Force
    }
    catch { }
}

$lastTitle = ''
$lastProcess = $null
$lastIoBytes = 0
$lastIoTime = Get-Date

while ($true) {
    $state = [ordered]@{
        ok             = [bool]$manager
        timestamp      = (Get-Date).ToString('o')
        playing        = $false
        title          = ''
        artist         = ''
        album          = ''
        status         = 'None'
        position       = 0.0
        duration       = 0.0
        can_next       = $false
        can_prev       = $false
        app_id         = ''
        audio_peak     = $null
        process        = $null
        io_rate_kbps   = $null
        cover_updated  = $false
    }

    if ($meterReady) {
        try {
            $peak = [AudioPeak]::Get()
            if ($peak -ge 0) { $state.audio_peak = [math]::Round($peak, 4) }
        }
        catch { }
    }

    $session = Get-SodaSession
    if ($session) {
        try {
            $props = Await ($session.TryGetMediaPropertiesAsync()) $smtcType
            $timeline = $session.GetTimelineProperties()
            $info = $session.GetPlaybackInfo()
            $state.title = [string]$props.Title
            $state.artist = [string]$props.Artist
            $state.album = [string]$props.AlbumTitle
            $state.status = [string]$info.PlaybackStatus
            $state.playing = ($state.status -eq 'Playing')
            $state.position = [math]::Round($timeline.Position.TotalSeconds, 2)
            $state.duration = [math]::Round($timeline.EndTime.TotalSeconds, 2)
            $state.can_next = [bool]$info.Controls.IsNextEnabled
            $state.can_prev = [bool]$info.Controls.IsPreviousEnabled
            $state.app_id = [string]$session.SourceAppUserModelId

            if ($CoverPath -and $state.title -and $state.title -ne $lastTitle -and $props.Thumbnail) {
                try {
                    $ras = Await ($props.Thumbnail.OpenReadAsync()) $rasType
                    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($ras)
                    $out = [System.IO.File]::Create($CoverPath)
                    $stream.CopyTo($out)
                    $out.Close(); $stream.Close()
                    $state.cover_updated = $true
                }
                catch { }
            }
            $lastTitle = $state.title
        }
        catch { }
    }

    $proc = Get-SodaProcess
    if ($proc) {
        if ($lastProcess -and $lastIoBytes -gt 0) {
            $now = Get-Date
            $dt = ($now - $lastIoTime).TotalSeconds
            if ($dt -gt 0.05) {
                $delta = $proc.io_bytes - $lastIoBytes
                if ($delta -ge 0) {
                    $state.io_rate_kbps = [math]::Round($delta / $dt / 1024, 1)
                }
            }
        }
        $lastIoBytes = $proc.io_bytes
        $lastIoTime = Get-Date
        $lastProcess = $proc
        $state.process = [ordered]@{
            pid         = $proc.pid
            count       = $proc.count
            cpu_seconds = $proc.cpu_seconds
            mem_mb      = $proc.mem_mb
            path        = $proc.path
        }
    }
    elseif ($lastProcess) {
        $state.process = [ordered]@{
            pid = $lastProcess.pid; count = $lastProcess.count
            cpu_seconds = $lastProcess.cpu_seconds; mem_mb = $lastProcess.mem_mb
            path = $lastProcess.path; stale = $true
        }
    }

    Write-State $state
    if ($Once) { break }
    Start-Sleep -Milliseconds $IntervalMs
}
