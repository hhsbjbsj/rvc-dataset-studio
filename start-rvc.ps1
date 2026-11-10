# Run the local RVC UI and API. Ctrl+C stops the services launched by this script.
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Backend = Join-Path $Root 'voiceforge-backend'
$Frontend = Join-Path $Root 'frontend'
$VPy = Join-Path $Backend 'venv\Scripts\python.exe'
$Port = if ($env:VOICEFORGE_PORT) { [int]$env:VOICEFORGE_PORT } else { 8001 }

if ($Port -lt 1 -or $Port -gt 65535 -or $Port -eq 5173) { throw 'Invalid VOICEFORGE_PORT.' }
if (-not (Test-Path -LiteralPath $VPy) -or -not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) {
    throw 'Run setup-rvc.ps1 first.'
}
$LogDir = Join-Path $Backend 'tmp\run'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:TEMP = $LogDir
$env:TMP = $LogDir
$env:VOICEFORGE_PORT = [string]$Port
$Api = Start-Process -FilePath $VPy -WorkingDirectory $Backend -PassThru -WindowStyle Hidden -ArgumentList @(
    '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', [string]$Port
) -RedirectStandardOutput (Join-Path $LogDir 'api.log') -RedirectStandardError (Join-Path $LogDir 'api-errors.log')

try {
    $Ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($Api.HasExited) { throw "API startup failed. See $LogDir\api-errors.log." }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1
            if ($health.service -eq 'voiceforge' -and -not $Api.HasExited) { $Ready = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $Ready) { throw "API did not start. See $LogDir\api-errors.log." }

    Write-Host 'Open http://127.0.0.1:5173/rvc. Press Ctrl+C in this console to stop.'
    Push-Location $Frontend
    try {
        & npm.cmd run dev -- --strictPort
        if ($LASTEXITCODE -ne 0) { throw 'Frontend startup failed.' }
    } finally {
        Pop-Location
    }
} finally {
    if (-not $Api.HasExited) {
        Stop-Process -InputObject $Api -ErrorAction SilentlyContinue
    }
}
