param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$')][string]$Repository
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Project = Join-Path $Root 'launcher\RvcLauncher.csproj'
$Publish = Join-Path $Root 'launcher\bin\portable'
$DotNetArguments = @(
    'publish', $Project, '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true',
    '-p:PublishSingleFile=true', '-p:IncludeNativeLibrariesForSelfExtract=true',
    '-p:EnableCompressionInSingleFile=true', '-p:DebugType=None', '-o', $Publish
)
if ($Version) { $DotNetArguments += "-p:Version=$Version" }
if ($Repository) { $DotNetArguments += "-p:UpdateRepository=$Repository" }
& dotnet @DotNetArguments
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }

$Executable = Join-Path $Root 'RvcLauncher.exe'
Copy-Item -LiteralPath (Join-Path $Publish 'RvcLauncher.exe') -Destination $Executable -Force
Write-Host "Launcher ready at $Executable"
