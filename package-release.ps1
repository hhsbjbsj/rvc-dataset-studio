$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Push-Location $Root
try {
    if (git status --porcelain) { throw 'Commit the release sources before packaging.' }
    [xml]$Project = Get-Content -LiteralPath (Join-Path $Root 'launcher\RvcLauncher.csproj')
    $Version = [string]$Project.Project.PropertyGroup.Version
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Expected a stable launcher version.' }
    $Stage = Join-Path $Root "launcher\bin\releases\v$Version"
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    Push-Location (Join-Path $Root 'frontend')
    try {
        & npm.cmd run build
        if ($LASTEXITCODE) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
    $env:NUGET_PACKAGES = Join-Path $Root 'launcher\obj\packages'
    & dotnet publish (Join-Path $Root 'launcher\RvcLauncher.csproj') -c Release -r win-x64 --self-contained true '-p:PublishSingleFile=true' '-p:IncludeNativeLibrariesForSelfExtract=true' '-p:EnableCompressionInSingleFile=true' '-p:DebugType=None' -o (Join-Path $Stage 'launcher')
    if ($LASTEXITCODE) { throw 'Launcher build failed.' }
    $Zip = Join-Path $Stage "RVC-Dataset-Studio-v$Version-windows-x64.zip"
    & git archive --format=zip --prefix=RVC-Dataset-Studio/ "--output=$Zip" HEAD -- README.md README.zh-CN.md CHANGELOG.md LICENSE setup-rvc.ps1 start-rvc.ps1 build-launcher.ps1 package-release.ps1 frontend launcher voiceforge-backend
    if ($LASTEXITCODE) { throw 'Source archive failed.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [IO.Compression.ZipFile]::Open($Zip, [IO.Compression.ZipArchiveMode]::Update)
    try {
        $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive, (Join-Path $Stage 'launcher\RvcLauncher.exe'), 'RVC-Dataset-Studio/RvcLauncher.exe', [IO.Compression.CompressionLevel]::NoCompression)
        $Dist = (Resolve-Path -LiteralPath (Join-Path $Root 'frontend\dist')).Path
        foreach ($File in Get-ChildItem -LiteralPath $Dist -File -Recurse) {
            $Relative = $File.FullName.Substring($Dist.Length + 1).Replace('\', '/')
            $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive, $File.FullName, "RVC-Dataset-Studio/frontend/dist/$Relative", [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $Archive.Dispose() }
    $Hash = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $Stage 'SHA256SUMS.txt'), "$Hash  $([IO.Path]::GetFileName($Zip))" + [char]10)
    Write-Host "Release package ready: $Zip"
} finally { Pop-Location }
