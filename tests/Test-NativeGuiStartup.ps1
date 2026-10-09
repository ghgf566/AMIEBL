param([string]$FrontendDirectory = (Join-Path $PSScriptRoot '..\build\native-gui\Release'))
$ErrorActionPreference = 'Stop'
$result = Join-Path ([IO.Path]::GetTempPath()) ('amiebl-native-startup-' + [guid]::NewGuid().ToString('N') + '.json')
$previous = $env:AMIEBL_NATIVE_GUI_SMOKE_RESULT
try {
    $env:AMIEBL_NATIVE_GUI_SMOKE_RESULT = $result
    $exe = Join-Path ([IO.Path]::GetFullPath($FrontendDirectory)) 'AMIEBL.Native.exe'
    $process = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(20000)) { $process.Kill(); throw 'Native GUI startup timed out.' }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Native GUI exited with $($process.ExitCode)." }
    $observed = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
    if (-not $observed.native_window_started -or $observed.ui_parity_verified) { throw 'Invalid native startup evidence.' }
    Write-Host 'PASS: native WinUI window/resources start and close. Five-page UI/UX parity is NOT verified.'
} finally {
    $env:AMIEBL_NATIVE_GUI_SMOKE_RESULT = $previous
    if (Test-Path -LiteralPath $result) { Remove-Item -LiteralPath $result }
}
