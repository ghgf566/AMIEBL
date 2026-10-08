param(
    [Parameter(Mandatory)][string]$PortableDirectory,
    [Parameter(Mandatory)][string]$SetupExe
)
$ErrorActionPreference = 'Stop'
# This script changes temporary installation/registry/start-menu state.
# Never execute it on a developer's real Windows account.
if ($env:CI -ne 'true' -or $env:GITHUB_ACTIONS -ne 'true') {
    throw 'This end-to-end installer test is restricted to isolated GitHub Actions runners.'
}
$portable = [IO.Path]::GetFullPath($PortableDirectory)
$setup = [IO.Path]::GetFullPath($SetupExe)
if (-not (Test-Path -LiteralPath (Join-Path $portable 'portable.flag'))) { throw 'Portable marker not found' }
if (-not (Test-Path -LiteralPath (Join-Path $portable 'LICENSE'))) { throw 'Apache LICENSE not included in Portable' }
if (-not (Test-Path -LiteralPath (Join-Path $portable 'README.md'))) { throw 'README not included in Portable' }
if (-not (Test-Path -LiteralPath $setup)) { throw 'Inno Setup EXE was not built' }
$root = Join-Path $env:RUNNER_TEMP ('amiebl-packaging-' + [guid]::NewGuid().ToString('N'))
$manual = Join-Path $root 'manual'
$inno = Join-Path $root 'inno'
$existingData = Join-Path $env:LOCALAPPDATA 'LocalModelManager'
if (Test-Path -LiteralPath $existingData) { throw "Runner user data folder unexpectedly exists: $existingData" }
New-Item -ItemType Directory -Path $root | Out-Null

try {
    # Inno install must never enable Windows startup without the user's choice.
    $beforeRunValue = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name LocalModelManager -ErrorAction SilentlyContinue).LocalModelManager
    if ($beforeRunValue) { throw 'Runner unexpectedly has existing AMIEBL autostart' }
    & $setup /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$inno" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Inno setup exit $LASTEXITCODE" }
    if (-not (Test-Path -LiteralPath (Join-Path $inno 'installed.flag'))) { throw 'Installed marker missing' }
    foreach ($name in @('portable.flag','data','models')) {
        if (Test-Path -LiteralPath (Join-Path $inno $name)) { throw "Installer unexpectedly included $name" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $inno 'LICENSE'))) { throw 'Installed Apache LICENSE missing' }
    $afterRunValue = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name LocalModelManager -ErrorAction SilentlyContinue).LocalModelManager
    if ($afterRunValue) { throw 'Installer enabled startup without consent' }

    # All of these are user-created and must survive normal uninstall.
    New-Item -ItemType Directory -Path (Join-Path $inno 'data'),(Join-Path $inno 'models'),$existingData | Out-Null
    Set-Content -LiteralPath (Join-Path $inno 'data\legacy.txt') -Value 'keep-legacy'
    Set-Content -LiteralPath (Join-Path $inno 'models\local.gguf') -Value 'keep-model'
    Set-Content -LiteralPath (Join-Path $existingData 'config.json') -Value 'keep-profile'
    $uninstaller = Join-Path $inno 'unins000.exe'
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw 'Inno uninstaller missing' }
    & $uninstaller /VERYSILENT /SUPPRESSMSGBOXES /NORESTART | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Inno uninstaller exit $LASTEXITCODE" }
    foreach ($item in @((Join-Path $inno 'data\legacy.txt'),(Join-Path $inno 'models\local.gguf'),(Join-Path $existingData 'config.json'))) {
        if (-not (Test-Path -LiteralPath $item)) { throw "Inno uninstaller deleted user file: $item" }
    }

    # Test the alternative installer with data isolation. It may not overwrite
    # the user-supplied model or settings folders on reinstall.
    & (Join-Path $portable 'installer\Install-LocalModelManager.ps1') -PortableDirectory $portable -InstallDirectory $manual
    if (-not (Test-Path -LiteralPath (Join-Path $manual 'installed-files.json'))) { throw 'Manual install manifest missing' }
    if (Test-Path -LiteralPath (Join-Path $manual 'portable.flag')) { throw 'Manual install retained portable marker' }
    New-Item -ItemType Directory -Path (Join-Path $manual 'data'),(Join-Path $manual 'models') | Out-Null
    Set-Content -LiteralPath (Join-Path $manual 'data\settings.json') -Value 'keep-settings'
    Set-Content -LiteralPath (Join-Path $manual 'models\another.gguf') -Value 'keep-model'
    Set-Content -LiteralPath (Join-Path $manual 'my-notes.txt') -Value 'keep-extra'
    & (Join-Path $portable 'installer\Install-LocalModelManager.ps1') -PortableDirectory $portable -InstallDirectory $manual
    foreach ($item in @('data\settings.json','models\another.gguf','my-notes.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $manual $item))) { throw "Manual upgrade deleted user file: $item" }
    }
    # A shipped file modified by the user must survive a selective uninstall.
    Set-Content -LiteralPath (Join-Path $manual 'README.md') -Value 'user-customized-readme'
    & (Join-Path $manual 'installer\Uninstall-LocalModelManager.ps1') -InstallDirectory $manual -Child
    if ((Get-Content -LiteralPath (Join-Path $manual 'README.md') -Raw).Trim() -ne 'user-customized-readme') {
        throw 'Manual uninstall removed a modified tracked file'
    }
    foreach ($item in @('data\settings.json','models\another.gguf','my-notes.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $manual $item))) { throw "Manual uninstall deleted user file: $item" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $existingData 'config.json'))) {
        throw 'Manual uninstall deleted user config'
    }
    Write-Output 'PASS: Portable LICENSE, isolated Setup install/uninstall, no autostart, manual upgrade/uninstall data preservation.'
}
finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $existingData) {
        # This directory did not exist when we entered the CI test.
        Remove-Item -LiteralPath $existingData -Recurse -Force -ErrorAction SilentlyContinue
    }
}
