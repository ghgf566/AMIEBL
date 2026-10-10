param(
    [Parameter(Mandatory)][string]$PortableDirectory,
    [Parameter(Mandatory)][string]$SetupExe
)
$ErrorActionPreference = 'Stop'
if ($env:CI -ne 'true' -or $env:GITHUB_ACTIONS -ne 'true') {
    throw 'Installer acceptance runs only on disposable GitHub Actions runners.'
}
$portable = [IO.Path]::GetFullPath($PortableDirectory)
$setup = [IO.Path]::GetFullPath($SetupExe)
function Test-DefaultStartup([string]$Executable, [string]$DataDirectory) {
    $process = Start-Process -FilePath $Executable -WindowStyle Hidden -PassThru
    try {
        $ready = $false
        for ($attempt = 0; $attempt -lt 80; $attempt++) {
            if ($process.HasExited) { throw "Packaged GUI exited: $($process.ExitCode)" }
            if (Test-Path -LiteralPath (Join-Path $DataDirectory 'admin-token')) {
                $token = (Get-Content -LiteralPath (Join-Path $DataDirectory 'admin-token') -Raw).Trim()
                try {
                    $config = Invoke-RestMethod 'http://127.0.0.1:8080/manager/config' -Headers @{'X-Manager-Token'=$token} -TimeoutSec 2
                    $ready = $null -ne $config
                    if ($ready) { break }
                } catch {}
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw 'Packaged GUI did not start its authenticated Core with the default data directory.' }
    } finally {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
    }
}
foreach ($name in @('LocalModelManager.exe','amiebl-core.exe','portable.flag','msvcp140.dll','vcruntime140.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $portable $name))) { throw "Missing native asset: $name" }
}
foreach ($name in @('backend','runtime','python','hostfxr.dll','coreclr.dll')) {
    if (Test-Path -LiteralPath (Join-Path $portable $name)) { throw "Legacy payload leaked: $name" }
}
$data = Join-Path $env:LOCALAPPDATA 'LocalModelManager'
if (Test-Path -LiteralPath $data) { throw 'Runner has preexisting product data.' }
New-Item -ItemType Directory -Path $data | Out-Null
$saved = Join-Path $data 'release-sentinel.txt'
Set-Content -LiteralPath $saved -Value 'preserve-settings'
Test-DefaultStartup (Join-Path $portable 'LocalModelManager.exe') (Join-Path $portable 'data')
foreach ($language in @('english','traditionalchinese')) {
    $installation = Join-Path $env:RUNNER_TEMP "AMIEBL 安裝 $language"
    $process = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/LANG=$language","/DIR=`"$installation`"") -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode) { throw "Setup $language failed: $($process.ExitCode)" }
    if (Test-Path -LiteralPath (Join-Path $installation 'portable.flag')) { throw 'Portable marker installed.' }
    if (-not (Test-Path -LiteralPath (Join-Path $installation 'installed.flag'))) { throw 'Installed marker missing.' }
    Test-DefaultStartup (Join-Path $installation 'LocalModelManager.exe') $data
    $unknown = Join-Path $installation 'user-notes.txt'
    $engine = Join-Path $installation 'engines/llama.cpp/versions/user-kept'
    New-Item -ItemType Directory -Path $engine -Force | Out-Null
    Set-Content -LiteralPath $unknown -Value 'keep'
    Set-Content -LiteralPath (Join-Path $engine 'model.gguf') -Value 'keep-model'
    # Upgrade must not remove user-added files or engine inventory.
    $process = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/LANG=$language","/DIR=`"$installation`"") -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode) { throw 'Upgrade failed.' }
    $process = Start-Process -FilePath (Join-Path $installation 'unins000.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode) { throw 'Uninstall failed.' }
    foreach ($path in @($saved,$unknown,(Join-Path $engine 'model.gguf'))) {
        if (-not (Test-Path -LiteralPath $path)) { throw "User file was deleted: $path" }
    }
}
Write-Host 'Both Setup languages, upgrade and conservative uninstall passed.'
