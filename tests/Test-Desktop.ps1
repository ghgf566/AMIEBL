param(
    [Parameter(Mandatory)][string]$FrontendDirectory,
    [ValidateSet('WinUI','WPF')][string]$Frontend = 'WinUI',
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
function Get-TestPort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}
$testData = Join-Path ([IO.Path]::GetTempPath()) ('amiebl-desktop-regression-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testData | Out-Null
$port = Get-TestPort
$enginePort = Get-TestPort
while ($port -eq $enginePort) { $enginePort = Get-TestPort }
$fixture = Join-Path $testData 'fixture.gguf'
[IO.File]::WriteAllBytes($fixture,[Text.Encoding]::ASCII.GetBytes('GGUFdesktop-test-fixture'))
$models = @('a','b') | ForEach-Object { @{id="model-$_";name="Model $_";path=$fixture;context=8192;gpu_layers=0;auto_fit=$false;cache_type='f16';mtp=$false;default_profile_id='coding'} }
$profiles = @('coding','quick-chat') | ForEach-Object { @{id=$_;name=$_;thinking_mode='on';reasoning_level='balanced';budget_mode='custom';thinking_budget=1536;max_tokens=4096} }
$config = @{schema_version=1;models=@($models);profiles=@($profiles);model_dirs=@($testData);engine_dir=$testData;api_port=$port;engine_port=$enginePort;default_model_id='model-a';default_profile_id='coding';preload=$false;auto_start=$false}
$config | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $testData 'config.json') -Encoding utf8
$executable = Join-Path ([IO.Path]::GetFullPath($FrontendDirectory)) 'LocalModelManager.exe'
$arguments = @('--smoke-test','--data-dir',('"'+$testData+'"'),'--port',$port.ToString())
if ($Frontend -eq 'WPF') { $arguments += '--editor-refresh-test' }
$process = Start-Process -FilePath $executable -ArgumentList $arguments -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(45000)) {
    $process.Kill($true)
    throw "Desktop test timed out. Test artifacts: $testData"
}
$process.Refresh()
if ($process.ExitCode -ne 0) { throw "Desktop exited with $($process.ExitCode). Test artifacts: $testData" }
$resultName = if ($Frontend -eq 'WinUI') { 'winui-smoke-test.json' } else { 'desktop-smoke-test.json' }
$resultFile = Join-Path $testData $resultName
if (-not (Test-Path -LiteralPath $resultFile)) { throw "Desktop produced no result. Test artifacts: $testData" }
$result = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
if (-not $result.ok -or -not $result.editor_refresh_verified) { throw "$Frontend regression failed: $($result.error)" }
if ($result.model_loaded -or $result.autostart_changed) { throw 'Desktop test changed forbidden runtime state.' }
$remaining = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($testData) })
if ($remaining.Count -gt 0) { throw 'Owned test processes did not stop.' }
if ($OutputDirectory) {
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    Copy-Item -LiteralPath $resultFile -Destination $OutputDirectory -Force
    $screenshots = Join-Path $testData $(if ($Frontend -eq 'WinUI') { 'winui-screenshots' } else { 'screenshots' })
    if (Test-Path -LiteralPath $screenshots) { Copy-Item -LiteralPath $screenshots -Destination $OutputDirectory -Recurse -Force }
}
Write-Output "$Frontend regression PASS: save/switch/restore, five pages, owned shutdown. Artifacts: $testData"
