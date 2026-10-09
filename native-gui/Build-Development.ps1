param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$MSBuild = 'msbuild',
    [string]$VCTargetsDirectory,
    [string]$XamlCppTargets,
    [string]$RustTargetDirectory = $env:CARGO_TARGET_DIR,
    [string]$NativeOutputDirectory = $env:AMIEBL_NATIVE_OUTPUT_DIR,
    [string]$NativeIntermediateDirectory = $env:AMIEBL_NATIVE_INTERMEDIATE_DIR,
    [string]$NativeRestoreDirectory = $env:AMIEBL_NATIVE_RESTORE_DIR,
    [switch]$UseExistingCore
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination = [IO.Path]::GetFullPath($OutputDirectory, (Get-Location).Path)
if (Test-Path -LiteralPath $destination) { throw 'Choose a new output directory; existing files are never overwritten.' }
Push-Location $repo
try {
    $rustTarget = if ($RustTargetDirectory) { [IO.Path]::GetFullPath($RustTargetDirectory, $repo) } else { Join-Path $repo 'native-core/target' }
    $nativeOutput = if ($NativeOutputDirectory) { [IO.Path]::GetFullPath($NativeOutputDirectory, $repo) } else { Join-Path $repo 'build/native-gui/Release' }
    $core = Join-Path $rustTarget 'release/amiebl-core.exe'
    if ($UseExistingCore) { $core = Join-Path $rustTarget 'debug/amiebl-core.exe' }
    else {
        & cargo build --locked --release --manifest-path native-core/Cargo.toml --bin amiebl-core --target-dir $rustTarget
        if ($LASTEXITCODE -ne 0) { throw 'Rust build failed.' }
    }
    if (-not (Test-Path -LiteralPath $core)) { throw 'Native Core executable is missing.' }
    $buildArgs = @('native-gui/AMIEBL.Native.vcxproj','/restore','/p:Configuration=Release','/p:Platform=x64','/nologo','/verbosity:minimal')
    if ($VCTargetsDirectory) { $buildArgs += "/p:VCTargetsPath=$([IO.Path]::GetFullPath($VCTargetsDirectory))\" }
    if ($XamlCppTargets) { $buildArgs += "/p:_CppCommonExtensionTargets=$([IO.Path]::GetFullPath($XamlCppTargets))" }
    if ($NativeOutputDirectory) { $buildArgs += "/p:OutDir=$nativeOutput\" }
    if ($NativeIntermediateDirectory) { $buildArgs += "/p:IntDir=$([IO.Path]::GetFullPath($NativeIntermediateDirectory, $repo))\" }
    if ($NativeRestoreDirectory) { $buildArgs += "/p:MSBuildProjectExtensionsPath=$([IO.Path]::GetFullPath($NativeRestoreDirectory, $repo))\" }
    & $MSBuild @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Native GUI build failed.' }
    New-Item -ItemType Directory -Path $destination | Out-Null
    Get-ChildItem -LiteralPath $nativeOutput |
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
build. Use System > Engine management to select a channel and install a managed
engine, or specify your existing external engine_dir. Select your own GGUF files.
The package does not bundle an engine. Do not point this development build at
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
    $sourceFiles = @(git -c core.quotepath=false ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inventory source files.' }
    $sourceFiles | ForEach-Object {
        $sourcePath = Join-Path $repo $_
        if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
            "$((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant())  $_"
        }
    } | Set-Content -LiteralPath (Join-Path $destination 'SOURCE-FILES.sha256') -Encoding utf8
    @{
        base_commit = (git rev-parse HEAD)
        dirty = [bool]@(git status --porcelain).Count
        source_inventory = 'SOURCE-FILES.sha256'
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'SOURCE-STATE.json') -Encoding utf8
    Get-ChildItem -LiteralPath $destination -File -Recurse | Where-Object Name -ne 'SHA256SUMS' |
        ForEach-Object { $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); "$hash  $([IO.Path]::GetRelativePath($destination,$_.FullName))" } |
        Set-Content -LiteralPath (Join-Path $destination 'SHA256SUMS') -Encoding utf8
    Write-Host "Native development build: $destination"
} finally { Pop-Location }
