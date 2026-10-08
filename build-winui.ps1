param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\outputs\LocalModelManager'),
    [switch]$SelfContained
)
$ErrorActionPreference = 'Stop'
$destination = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$project = Join-Path $PSScriptRoot 'desktop-winui\AMIEBL.WinUI.csproj'
$selfContainedValue = if ($SelfContained) { 'true' } else { 'false' }
& dotnet publish $project -c Release -r win-x64 -p:Platform=x64 --self-contained $selfContainedValue -p:NuGetAudit=false -o $destination
if ($LASTEXITCODE -ne 0) { throw 'WinUI 3 桌面程式建置失敗。' }
$backendTarget = Join-Path $destination 'backend'
New-Item -ItemType Directory -Force -Path $backendTarget | Out-Null
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'backend') -File | Where-Object { $_.Extension -in '.py','.txt','.json' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $backendTarget -Force }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'assets\manager.ico') -Destination (Join-Path $destination 'manager.ico') -Force
foreach ($document in @('README.md','LICENSE','SECURITY.md','使用說明.md','DESKTOP-ARCHITECTURE.md','PRODUCT-IDENTITY.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $document) -Destination $destination -Force
}
$commit = & git -C $PSScriptRoot rev-parse HEAD
if ($LASTEXITCODE -eq 0) { Set-Content -LiteralPath (Join-Path $destination 'BUILD-COMMIT.txt') -Value $commit -Encoding utf8 }
Write-Output "已建置 WinUI 3：$destination"
