param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version
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
& dotnet @DotNetArguments
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }

$Executable = Join-Path $Root 'RvcLauncher.exe'
Copy-Item -LiteralPath (Join-Path $Publish 'RvcLauncher.exe') -Destination $Executable -Force
Write-Host "Launcher ready at $Executable"
