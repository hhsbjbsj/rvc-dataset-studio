# Install the offline RVC project workflow into the drive that holds this repository.
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Backend = Join-Path $Root 'voiceforge-backend'
$Frontend = Join-Path $Root 'frontend'
$VPy = Join-Path $Backend 'venv\Scripts\python.exe'

foreach ($tool in @('py.exe', 'npm.cmd', 'ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Required tool not found on PATH: $tool. See README.zh-CN.md."
    }
}

$InstallTemp = Join-Path $Backend 'tmp\install'
New-Item -ItemType Directory -Force -Path $InstallTemp | Out-Null
$env:TEMP = $InstallTemp
$env:TMP = $InstallTemp
$env:npm_config_cache = Join-Path $Frontend '.npm'

if (-not (Test-Path -LiteralPath $VPy)) {
    $PythonVersion = $null
    foreach ($version in @('-3.11', '-3.12')) {
        & py.exe $version -c 'import sys' *> $null
        if ($LASTEXITCODE -eq 0) { $PythonVersion = $version; break }
    }
    if (-not $PythonVersion) { throw 'Install Python 3.11 or 3.12 and the Windows py launcher.' }
    & py.exe $PythonVersion -m venv (Join-Path $Backend 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}

& $VPy -m pip install --disable-pip-version-check --no-cache-dir -r (Join-Path $Backend 'requirements-rvc.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed.' }
Push-Location $Frontend
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend package installation failed.' }
} finally {
    Pop-Location
}
Write-Host 'Setup complete. Run start-rvc.ps1, then open http://127.0.0.1:5173/rvc.'
