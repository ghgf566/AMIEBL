param(
    [Parameter(Mandatory = $true)][string]$PortableDirectory,
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\LocalModelManager')
)

$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath($PortableDirectory)
$target = [IO.Path]::GetFullPath($InstallDirectory)
if (-not (Test-Path -LiteralPath (Join-Path $source 'LocalModelManager.exe') -PathType Leaf)) { throw "找不到 portable\LocalModelManager.exe：$source" }
if ($target -eq [IO.Path]::GetPathRoot($target) -or $target.Length -lt 12) { throw "拒絕使用過於危險的安裝路徑：$target" }

if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
New-Item -ItemType Directory -Force -Path $target | Out-Null
& robocopy $source $target /E /R:2 /W:1 /NFL /NDL /NP | Out-Null
if ($LASTEXITCODE -gt 7) { throw "複製安裝檔案失敗（robocopy $LASTEXITCODE）。" }

$uninstallScript = Join-Path $target 'installer\Uninstall-LocalModelManager.ps1'
$uninstallCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstallScript`" -InstallDirectory `"$target`""
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalModelManager'
New-Item -Path $uninstallKey -Force | Out-Null
Set-ItemProperty -Path $uninstallKey -Name DisplayName -Value 'Local Model Manager'
Set-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value '1.0.0'
Set-ItemProperty -Path $uninstallKey -Name Publisher -Value 'Local Model Manager'
Set-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $target
Set-ItemProperty -Path $uninstallKey -Name UninstallString -Value $uninstallCommand
Set-ItemProperty -Path $uninstallKey -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $uninstallKey -Name NoRepair -Value 1 -Type DWord

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Local Model Manager.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($startMenu)
$shortcut.TargetPath = Join-Path $target 'LocalModelManager.exe'
$shortcut.WorkingDirectory = $target
$shortcut.IconLocation = (Join-Path $target 'manager.ico')
$shortcut.Save()

Write-Output "已安裝到：$target"
Write-Output "可從開始功能表啟動 Local Model Manager。"
$global:LASTEXITCODE = 0
