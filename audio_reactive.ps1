param(
    [Parameter(Mandatory = $true)][string]$OutPath,
    [int]$ParentPid = 0
)
$ErrorActionPreference = 'Stop'
try {
    Add-Type -Path (Join-Path $PSScriptRoot 'audio_reactive.cs')
    [SodaReactive.Loopback]::Run($OutPath, $ParentPid)
}
catch {
    $state = @{ ready = $false; timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(); error = $_.Exception.Message }
    [IO.File]::WriteAllText($OutPath, ($state | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    exit 1
}
