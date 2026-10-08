param(
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [switch]$KeepData,          # Backward-compatible; keeping data is now the default.
    [switch]$DeleteUserData,    # Explicit opt-in only, never used by normal uninstall.
    [switch]$Child
)

$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
if ($target.Length -lt 12 -or $target -eq [IO.Path]::GetPathRoot($target)) {
    throw "拒絕移除危險路徑：$target"
}
if ($KeepData -and $DeleteUserData) { throw 'KeepData 與 DeleteUserData 不可同時指定。' }
if (-not $Child) {
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('LocalModelManager-uninstall-' + [guid]::NewGuid().ToString('N') + '.ps1')
    Copy-Item -LiteralPath $PSCommandPath -Destination $temp -Force
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $temp + '"'), '-InstallDirectory', ('"' + $target + '"'), '-Child')
    if ($DeleteUserData) { $arguments += '-DeleteUserData' }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    exit 0
}

Start-Sleep -Milliseconds 700
$managerExe = Join-Path $target 'LocalModelManager.exe'
$engineDir = (Join-Path $target 'llama.cpp') + '\'
$backendScript = Join-Path $target 'backend\manager.py'
# Only stop processes owned by this installation, never any process whose
# unrelated arguments happen to mention the installation directory.
try {
    Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and (
            ([string]::Equals($_.ExecutablePath, $managerExe, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($engineDir, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.CommandLine -and $_.CommandLine.IndexOf($backendScript, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        )
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch { Write-Warning "無法確認所有背景程序是否結束：$($_.Exception.Message)" }
Start-Sleep -Milliseconds 500

# Only remove a startup value, uninstall entry or shortcut belonging to this
# exact installation; never affect a separate AMIEBL installation.
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runValue = (Get-ItemProperty -Path $runKey -Name LocalModelManager -ErrorAction SilentlyContinue).LocalModelManager
if ($runValue -and $runValue.IndexOf($managerExe, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
    Remove-ItemProperty -Path $runKey -Name LocalModelManager -ErrorAction SilentlyContinue
}
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalModelManager'
$entry = Get-ItemProperty -Path $uninstallKey -ErrorAction SilentlyContinue
if ($entry -and [string]::Equals([string]$entry.InstallLocation, $target, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -Path $uninstallKey -Recurse -Force -ErrorAction SilentlyContinue
}
$shortcutPath = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Local Model Manager.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        if ([string]::Equals($shell.CreateShortcut($shortcutPath).TargetPath, $managerExe, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $shortcutPath -Force
        }
    } catch { Write-Warning "無法確認捷徑：$($_.Exception.Message)" }
}

# The manual installer records its own files and their hashes. Uninstall may
# remove only unchanged shipped files, never recursively delete the install
# directory. Unknown files, legacy data, and GGUF models remain untouched.
$manifestPath = Join-Path $target 'installed-files.json'
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.version -ne 1) { throw '不認識此安裝清單格式；已保留所有安裝檔。' }
    $rootPrefix = $target + '\'
    foreach ($item in @($manifest.files)) {
        $relative = [string]$item.path
        if ([string]::IsNullOrWhiteSpace($relative) -or $relative -match '^(data|models)\\') { continue }
        $file = [IO.Path]::GetFullPath((Join-Path $target $relative))
        if (-not $file.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { continue }
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq [string]$item.sha256) {
            Remove-Item -LiteralPath $file -Force
        }
    }
    Remove-Item -LiteralPath $manifestPath -Force
    Remove-Item -LiteralPath (Join-Path $target 'installed.flag') -Force -ErrorAction SilentlyContinue
    # Inspect only directories containing tracked application files. Never
    # recursively enumerate user's data/, models/, or other unknown folders.
    $trackedDirectories = @($manifest.files | ForEach-Object {
        $parent = [IO.Path]::GetDirectoryName(([string]$_.path))
        while ($parent) {
            $full = [IO.Path]::GetFullPath((Join-Path $target $parent))
            if ($full.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { $full }
            $parent = [IO.Path]::GetDirectoryName($parent)
        }
    } | Sort-Object -Unique | Sort-Object Length -Descending)
    foreach ($directory in $trackedDirectories) {
        if ((Test-Path -LiteralPath $directory -PathType Container) -and
            @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction SilentlyContinue).Count -eq 0) {
            Remove-Item -LiteralPath $directory -ErrorAction SilentlyContinue
        }
    }
    if (@(Get-ChildItem -LiteralPath $target -Force -ErrorAction SilentlyContinue).Count -eq 0) {
        Remove-Item -LiteralPath $target -ErrorAction SilentlyContinue
    }
} else {
    Write-Warning '舊版安裝沒有安全的檔案清單；已解除登錄，但保留安裝資料夾，請先備份後手動清理。'
}

if ($DeleteUserData) {
    $dataPath = Join-Path $env:LOCALAPPDATA 'LocalModelManager'
    if ($dataPath -ne [IO.Path]::GetPathRoot($dataPath) -and $dataPath.Length -gt 12) {
        Remove-Item -LiteralPath $dataPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Output '已解除安裝；預設保留設定、日誌、備份、使用者模型及其他資料。'
try {
    if ([IO.Path]::GetFileName($PSCommandPath) -like 'LocalModelManager-uninstall-*.ps1') {
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    }
} catch { }
