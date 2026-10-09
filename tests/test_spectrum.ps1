$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot '..\audio_reactive.cs')
function Assert-True($condition, $message) {
    if (-not $condition) { throw $message }
}
function Get-Tone([double]$frequency, [double]$amplitude) {
    $samples = [double[]]::new(4096)
    for ($i = 0; $i -lt $samples.Length; $i++) {
        $samples[$i] = $amplitude * [math]::Sin(2 * [math]::PI * $frequency * $i / 48000)
    }
    return [SodaReactive.SpectrumMath]::Analyze($samples, 48000)
}
foreach ($frequency in @(80, 1000, 6000)) {
    $frame = Get-Tone $frequency .2
    $peakIndex = 0
    for ($i = 1; $i -lt 48; $i++) {
        if ($frame.Bands[$i] -gt $frame.Bands[$peakIndex]) { $peakIndex = $i }
    }
    $expected = [math]::Floor([math]::Log($frequency / 40) / [math]::Log(400) * 48)
    Assert-True ([math]::Abs($peakIndex - $expected) -le 2) "Wrong dominant band for $frequency Hz"
    Assert-True ([math]::Abs($frame.Rms - .2 / [math]::Sqrt(2)) -lt .003) 'RMS calibration mismatch'
}
$silence = [SodaReactive.SpectrumMath]::Analyze([double[]]::new(4096), 48000)
Assert-True (($silence.Bands | Measure-Object -Sum).Sum -eq 0) 'Silence produced spectrum bars'
$quiet = Get-Tone 1000 .01
$loud = Get-Tone 1000 .4
Assert-True ($loud.Mid -gt $quiet.Mid) 'Spectrum must react to volume'
Assert-True ([SodaReactive.Pcm]::Decode([byte[]]@(0, 0, 0, 63), 0, 32, $true) -eq .5) 'Float32 decoding'
Assert-True ([SodaReactive.Pcm]::Decode([byte[]]@(0, 192), 0, 16, $false) -eq -.5) 'PCM16 decoding'
Assert-True ([SodaReactive.Pcm]::Decode([byte[]]@(0, 0, 128), 0, 24, $false) -eq -1) 'PCM24 signed decoding'
Assert-True ([SodaReactive.Pcm]::Decode([byte[]]@(0, 0, 192, 127), 0, 32, $true) -eq 0) 'Invalid PCM must be silent'
Write-Output 'Spectrum checks passed: frequency bands, calibrated RMS, silence, volume response, PCM formats.'
