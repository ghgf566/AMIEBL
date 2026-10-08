param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\outputs\LocalModelManager'),
    [ValidateSet("WinUI", "WPF")][string]$Frontend = "WinUI",
    [switch]$SelfContained
)
$ErrorActionPreference = 'Stop'
if ($Frontend -eq 'WinUI') {
    & (Join-Path $PSScriptRoot 'build-winui.ps1') -OutputDirectory $OutputDirectory -SelfContained:$SelfContained
    return
}
$destination = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$project = Join-Path $PSScriptRoot 'desktop\LocalModelManager.csproj'
$buildState = Join-Path $PSScriptRoot 'desktop\obj'
$savedAppData = $env:APPDATA
$savedCliHome = $env:DOTNET_CLI_HOME
try {
    $env:APPDATA = Join-Path $buildState 'appdata'
    $env:DOTNET_CLI_HOME = Join-Path $buildState 'dotnet-home'
    # Keep the normal NuGet cache. A per-project empty cache makes offline
    # builds fail because the Windows desktop runtime pack is not in it.
    New-Item -ItemType Directory -Force -Path $env:APPDATA,$env:DOTNET_CLI_HOME | Out-Null
    $runtimeArguments = $(if ($SelfContained) { @('-r', 'win-x64') } else { @() })
    $restoreConfig = Join-Path $PSScriptRoot 'desktop\NuGet.Config'
    if ($SelfContained) {
        # Runtime packs are often already in the global cache on a developer
        # machine, while the normal project config intentionally has no remote
        # feeds. Add the cache as an offline source and NuGet.org as a fallback.
        $nugetRoot = if ($env:NUGET_PACKAGES) { [IO.Path]::GetFullPath($env:NUGET_PACKAGES) } else { Join-Path $env:USERPROFILE '.nuget\packages' }
        $escapedRoot = [Security.SecurityElement]::Escape($nugetRoot)
        $restoreConfig = Join-Path $buildState 'SelfContained.NuGet.Config'
        $cacheLine = '<add key="local-cache" value="' + $escapedRoot + '" />'
        @('<?xml version="1.0" encoding="utf-8"?>', '<configuration><packageSources><clear />', $cacheLine, '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" />', '</packageSources></configuration>') | Set-Content -LiteralPath $restoreConfig -Encoding UTF8
    }
    & dotnet restore $project @runtimeArguments --configfile $restoreConfig -p:NuGetAudit=false
    if ($LASTEXITCODE -ne 0) { throw '桌面程式相依性檢查失敗。' }
    $selfContainedValue = $(if ($SelfContained) { 'true' } else { 'false' })
    & dotnet publish $project -c Release @runtimeArguments --self-contained $selfContainedValue --no-restore -o $destination
    if ($LASTEXITCODE -ne 0) { throw '桌面程式建置失敗。' }
} finally {
    $env:APPDATA = $savedAppData
    $env:DOTNET_CLI_HOME = $savedCliHome
}
$backendTarget = Join-Path $destination 'backend'
New-Item -ItemType Directory -Force -Path $backendTarget | Out-Null
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'backend') -File | Where-Object { $_.Extension -in '.py','.txt','.json' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $backendTarget -Force }
foreach ($document in @('使用說明.md','驗證紀錄.md')) {
    $documentPath = Join-Path $PSScriptRoot $document
    if (Test-Path -LiteralPath $documentPath) { Copy-Item -LiteralPath $documentPath -Destination $destination -Force }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'assets\manager.ico') -Destination (Join-Path $destination 'manager.ico') -Force
Write-Output "已建置：$destination"
