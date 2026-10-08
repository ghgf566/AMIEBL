param(
    [Parameter(Mandatory = $true)][string]$PortableDirectory,
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\LocalModelManager')
)

$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath($PortableDirectory).TrimEnd('\')
$target = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $source 'LocalModelManager.exe') -PathType Leaf)) {
    throw "找不到 portable\LocalModelManager.exe：$source"
}
if ($target -eq [IO.Path]::GetPathRoot($target) -or $target.Length -lt 12) {
    throw "拒絕使用過於危險的安裝路徑：$target"
}
if ($source.Equals($target, [StringComparison]::OrdinalIgnoreCase) -or
    $target.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $source.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw '來源及安裝位置不能相同，也不能互相包含。'
}
if (Test-Path -LiteralPath (Join-Path $target 'unins000.exe')) {
    throw '此位置已有 Inno Setup 安裝版本，請使用 Setup.exe 更新，避免混用兩套解除安裝機制。'
}
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalModelManager'
$existingInstall = Get-ItemProperty -Path $uninstallKey -ErrorAction SilentlyContinue
$knownInstall = $existingInstall -and
    [string]::Equals([string]$existingInstall.InstallLocation, $target, [StringComparison]::OrdinalIgnoreCase)
if ((Test-Path -LiteralPath (Join-Path $target 'portable.flag')) -and -not $knownInstall) {
    throw '目標位置是既有 Portable 資料夾；請另外選擇安裝位置，避免影響原本的資料。'
}

# An in-place update never deletes the old directory. Keep user data, GGUFs,
# unexpected files, and legacy <install>\data intact.
New-Item -ItemType Directory -Force -Path $target | Out-Null
& robocopy $source $target /E /R:2 /W:1 /NFL /NDL /NP /XD (Join-Path $source 'data') (Join-Path $source 'models') /XF 'portable.flag' 'installed.flag' 'installed-files.json' | Out-Null
if ($LASTEXITCODE -gt 7) { throw "複製安裝檔案失敗（robocopy $LASTEXITCODE）。" }

# Installed mode always resolves its settings under %LOCALAPPDATA%.
# BackendHost migrates legacy <install>\data on first launch if no new data exists.
Set-Content -LiteralPath (Join-Path $target 'installed.flag') -Value 'AMIEBL installed mode' -Encoding UTF8

# Record only files shipped by the bundle. On uninstall, remove an installed
# file only when it still has its original SHA-256; user-modified files survive.
$sourceFiles = @(Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object {
    $relative = $_.FullName.Substring($source.Length + 1)
    $relative -notmatch '^(data|models)\\' -and
    $relative -notin @('portable.flag', 'installed.flag', 'installed-files.json')
})
$manifestFiles = @($sourceFiles | ForEach-Object {
    $relative = $_.FullName.Substring($source.Length + 1)
    $installed = Join-Path $target $relative
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) {
        throw "複製結果缺少必要檔案：$relative"
    }
    [ordered]@{ path = $relative; sha256 = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash }
})
@{ version = 1; files = $manifestFiles } |
    ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $target 'installed-files.json') -Encoding UTF8

$uninstallScript = Join-Path $target 'installer\Uninstall-LocalModelManager.ps1'
$uninstallCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " + '"' + $uninstallScript + '"' + " -InstallDirectory " + '"' + $target + '"'
New-Item -Path $uninstallKey -Force | Out-Null
Set-ItemProperty -Path $uninstallKey -Name DisplayName -Value 'AMIEBL'
Set-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value '1.0.0'
Set-ItemProperty -Path $uninstallKey -Name Publisher -Value 'Mr. Chen'
Set-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $target
Set-ItemProperty -Path $uninstallKey -Name UninstallString -Value $uninstallCommand
Set-ItemProperty -Path $uninstallKey -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $uninstallKey -Name NoRepair -Value 1 -Type DWord

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Local Model Manager.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($startMenu)
$shortcut.TargetPath = Join-Path $target 'LocalModelManager.exe'
$shortcut.WorkingDirectory = $target
$shortcut.IconLocation = Join-Path $target 'manager.ico'
$shortcut.Save()

Write-Output "已安裝到：$target"
Write-Output '未新增開機自動啟動；程式設定、紀錄與模型不會因更新被清除。'
$global:LASTEXITCODE = 0
