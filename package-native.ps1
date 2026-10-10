param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][string]$VCRuntimeDirectory,
    [string]$MSBuild = 'msbuild',
    [switch]$BuildInstaller,
    [string]$IsccPath = ''
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw 'Choose a new output directory; existing data is never overwritten.' }
$crt = [IO.Path]::GetFullPath($VCRuntimeDirectory)
foreach ($name in @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $crt $name))) { throw "Missing redistributable CRT: $name" }
}
$portable = Join-Path $output 'portable'
$target = Join-Path $repo ('build/native-release-' + [guid]::NewGuid().ToString('N'))
$coreTarget = Join-Path $repo 'build/rust-release'
& cargo build --locked --release --manifest-path (Join-Path $repo 'native-core/Cargo.toml') --bin amiebl-core --target-dir $coreTarget
if ($LASTEXITCODE) { throw 'Rust release build failed.' }
& $MSBuild (Join-Path $repo 'native-gui/AMIEBL.Native.vcxproj') /restore /p:Configuration=Release /p:Platform=x64 /p:AMIEBLReleaseBuild=true "/p:OutDir=$target\" "/p:IntDir=$target-obj\" /nologo /verbosity:minimal
if ($LASTEXITCODE) { throw 'Native release GUI build failed.' }
New-Item -ItemType Directory -Path $portable -Force | Out-Null
Get-ChildItem -LiteralPath $target | Where-Object { $_.Extension -notin @('.pdb','.lib','.exp','.ilk') } | Copy-Item -Destination $portable -Recurse
if (-not (Test-Path -LiteralPath (Join-Path $portable 'LocalModelManager.exe'))) { throw 'Production GUI executable missing.' }
Copy-Item -LiteralPath (Join-Path $coreTarget 'release/amiebl-core.exe') -Destination $portable
Get-ChildItem -LiteralPath $crt -File -Filter '*.dll' | Copy-Item -Destination $portable
New-Item -ItemType Directory -Path (Join-Path $portable 'assets') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'assets/manager.ico') -Destination (Join-Path $portable 'assets/manager.ico')
Copy-Item -LiteralPath (Join-Path $repo 'assets/manager.ico') -Destination (Join-Path $portable 'manager.ico')
foreach ($name in @('LICENSE','SECURITY.md')) {
    Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $portable
}
Copy-Item -LiteralPath (Join-Path $repo 'installer/NATIVE-NOTICES.md') -Destination (Join-Path $portable 'THIRD-PARTY-NOTICES.md')
Copy-Item -LiteralPath (Join-Path $repo 'migration/v1.1.0/RELEASE-SCOPE.md') -Destination (Join-Path $portable 'README.md')
# Keep redistribution notices with the actual native dependency payload.
$notices = Join-Path $portable 'third-party'
New-Item -ItemType Directory -Path $notices -Force | Out-Null
$nuget = if ($env:NUGET_PACKAGES) { $env:NUGET_PACKAGES } else { Join-Path $env:USERPROFILE '.nuget/packages' }
$assets = Get-Content -LiteralPath (Join-Path $repo 'native-gui/obj/project.assets.json') -Raw -ErrorAction SilentlyContinue
if (-not $assets) {
    $assets = Get-Content -LiteralPath (Join-Path $repo 'build/native-gui-restore/project.assets.json') -Raw -ErrorAction SilentlyContinue
}
if (-not $assets) { throw 'NuGet dependency inventory missing; cannot package license notices.' }
($assets | ConvertFrom-Json).libraries.PSObject.Properties | ForEach-Object {
    $dependency = Join-Path $nuget $_.Name.ToLowerInvariant()
    $licenseFiles = @(Get-ChildItem -LiteralPath $dependency -File -Recurse | Where-Object Name -Match '^(license|notice|third-party-notices)(\.|$)')
    if ($licenseFiles.Count) {
        $folder = Join-Path $notices ($_.Name -replace '/', '-')
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        foreach ($file in $licenseFiles) {
            $relative = [IO.Path]::GetRelativePath($dependency, $file.FullName) -replace '[\\/]', '_'
            Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $folder $relative)
        }
    }
}
$cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE '.cargo' }
$lock = Get-Content -LiteralPath (Join-Path $repo 'native-core/Cargo.lock') -Raw
foreach ($match in [regex]::Matches($lock, '(?m)^name = "([^"]+)"\r?\nversion = "([^"]+)"')) {
    $id = "$($match.Groups[1].Value)-$($match.Groups[2].Value)"
    $source = Get-ChildItem (Join-Path $cargoHome "registry/src/*/$id") -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $source) { continue } # Lockfile also lists dependencies for other target platforms.
    $folder = Join-Path $notices "rust-$id"
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    Get-ChildItem -LiteralPath $source.FullName -File | Where-Object Name -Match '^(license|notice|copyright|authors|Cargo.toml)' | Copy-Item -Destination $folder
}
if ($BuildInstaller) {
    if (-not (Test-Path -LiteralPath $IsccPath)) { throw 'Specify the Inno Setup compiler with -IsccPath.' }
    $innoLicense = Join-Path (Split-Path $IsccPath) 'license.txt'
    if (-not (Test-Path -LiteralPath $innoLicense)) { throw 'Inno Setup license missing.' }
    Copy-Item -LiteralPath $innoLicense -Destination (Join-Path $notices 'Inno-Setup-license.txt')
}
Set-Content -LiteralPath (Join-Path $portable 'portable.flag') -Value 'AMIEBL portable mode' -Encoding utf8
@{
    product='AMIEBL'; version='1.1.0'; source_commit=(& git -C $repo rev-parse HEAD)
    source_dirty=[bool]@(& git -C $repo status --porcelain).Count
    gui='LocalModelManager.exe'; core='amiebl-core.exe'; engine_bundled=$false
    python_runtime_included=$false; dotnet_runtime_included=$false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $portable 'bundle-manifest.json') -Encoding utf8
Get-ChildItem -LiteralPath $portable -Recurse -File | ForEach-Object {
    "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $([IO.Path]::GetRelativePath($portable,$_.FullName))"
} | Set-Content -LiteralPath (Join-Path $portable 'SHA256SUMS.txt') -Encoding utf8
if ($BuildInstaller) {
    if (-not (Test-Path -LiteralPath $IsccPath)) { throw 'Specify the Inno Setup compiler with -IsccPath.' }
    & $IsccPath "/DSourceDir=$portable" "/DOutputDir=$output" /DMyAppVersion=1.1.0 (Join-Path $repo 'installer/LocalModelManager.iss')
    if ($LASTEXITCODE) { throw 'Setup compilation failed.' }
}
Compress-Archive -Path (Join-Path $portable '*') -DestinationPath (Join-Path $output 'AMIEBL-v1.1.0-win-x64-portable.zip')
Get-ChildItem -LiteralPath $output -File | Where-Object Name -ne 'SHA256SUMS.txt' | ForEach-Object {
    "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $($_.Name)"
} | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding utf8
Write-Host "Native release candidate: $output"
