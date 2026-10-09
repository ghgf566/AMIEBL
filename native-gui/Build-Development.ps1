param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$MSBuild = 'msbuild',
    [string]$VCTargetsDirectory,
    [string]$XamlCppTargets,
    [switch]$UseExistingCore
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $destination) { throw 'Choose a new output directory; existing files are never overwritten.' }
Push-Location $repo
try {
    $core = Join-Path $repo 'native-core/target/release/amiebl-core.exe'
    if ($UseExistingCore) { $core = Join-Path $repo 'native-core/target/debug/amiebl-core.exe' }
    else {
        & cargo build --locked --release --manifest-path native-core/Cargo.toml --bin amiebl-core
        if ($LASTEXITCODE -ne 0) { throw 'Rust build failed.' }
    }
    if (-not (Test-Path -LiteralPath $core)) { throw 'Native Core executable is missing.' }
    $buildArgs = @('native-gui/AMIEBL.Native.vcxproj','/restore','/p:Configuration=Release','/p:Platform=x64','/nologo','/verbosity:minimal')
    if ($VCTargetsDirectory) { $buildArgs += "/p:VCTargetsPath=$([IO.Path]::GetFullPath($VCTargetsDirectory))\" }
    if ($XamlCppTargets) { $buildArgs += "/p:_CppCommonExtensionTargets=$([IO.Path]::GetFullPath($XamlCppTargets))" }
    & $MSBuild @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Native GUI build failed.' }
    New-Item -ItemType Directory -Path $destination | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $repo 'build/native-gui/Release') |
        Where-Object { $_.Extension -notin @('.pdb','.lib','.exp','.ilk') } |
        Copy-Item -Destination $destination -Recurse
    Copy-Item -LiteralPath $core -Destination (Join-Path $destination 'amiebl-core.exe')
    New-Item -ItemType Directory -Path (Join-Path $destination 'assets') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'assets/manager.ico') -Destination (Join-Path $destination 'assets/manager.ico')
    Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $destination
    Copy-Item -LiteralPath (Join-Path $repo 'migration/v1.1.0/STATUS.md') -Destination $destination
    @'
@echo off
"%~dp0AMIEBL.Native.exe" --data-dir "%~dp0development-data"
'@ | Set-Content -LiteralPath (Join-Path $destination 'Start-Development.cmd') -Encoding ascii
    @'
# AMIEBL native development build

Run Start-Development.cmd. Data is isolated in development-data next to this
build. Specify your existing llama.cpp engine_dir and GGUF files in the GUI.
No engine is downloaded or installed. Do not point this development build at
the supported product's live data without a separate backup.

This contains a C++/WinRT GUI and Rust service, with app-local Windows App SDK.
It is not a release or a clean-machine installer acceptance result. A supported
Microsoft Visual C++ Redistributable may still be required on another machine.
Python and .NET are used only by reference/development tests, not these two
executables. Full visual, accessibility, real CPU/CUDA and installation gates
are still pending; see STATUS.md. Startup registration uses an isolated
AMIEBL.Native.Development Run value and does not replace v1.0.0 registration.
'@ | Set-Content -LiteralPath (Join-Path $destination 'README.md') -Encoding utf8
    git rev-parse HEAD | Set-Content -LiteralPath (Join-Path $destination 'SOURCE-COMMIT.txt') -Encoding ascii
    Get-ChildItem -LiteralPath $destination -File -Recurse | Where-Object Name -ne 'SHA256SUMS' |
        ForEach-Object { $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); "$hash  $([IO.Path]::GetRelativePath($destination,$_.FullName))" } |
        Set-Content -LiteralPath (Join-Path $destination 'SHA256SUMS') -Encoding utf8
    Write-Host "Native development build: $destination"
} finally { Pop-Location }
