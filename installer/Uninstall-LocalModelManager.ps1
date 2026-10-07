param(
    [Parameter(Mandatory = $true)][string]$InstallDirectory,
    [switch]$KeepData,
    [switch]$Child
)

$ErrorActionPreference = 'SilentlyContinue'
$target = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
if ($target.Length -lt 12 -or $target -eq [IO.Path]::GetPathRoot($target)) { throw "拒絕移除危險路徑：$target" }

if (-not $Child) {
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('LocalModelManager-uninstall-' + [guid]::NewGuid().ToString('N') + '.ps1')
    Copy-Item -LiteralPath $PSCommandPath -Destination $temp -Force
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $temp, '-InstallDirectory', $target, '-Child')
    if ($KeepData) { $args += '-KeepData' }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WindowStyle Hidden
    exit 0
}

Start-Sleep -Milliseconds 700
$targetPattern = $target.Replace('\', '\\')
try {
    Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -like "*$target*" } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
    }
} catch { }
Get-Process -Name LocalModelManager -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        if ($_.Path -and $_.Path.StartsWith($target, [StringComparison]::OrdinalIgnoreCase)) { Stop-Process -Id $_.Id -Force }
    } catch { }
}
Start-Sleep -Milliseconds 500

$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Remove-ItemProperty -Path $runKey -Name LocalModelManager -ErrorAction SilentlyContinue
Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalModelManager' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Local Model Manager.lnk') -Force -ErrorAction SilentlyContinue
if (-not $KeepData) { Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'LocalModelManager') -Recurse -Force -ErrorAction SilentlyContinue }
Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
