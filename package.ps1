param(
    [string]$LlamaRoot = (Join-Path $env:USERPROFILE 'llama.cpp'),
    [string]$PythonHome = '',
    [string]$ModelDirectory = '',
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\outputs\LocalModelManager-release'),
    [switch]$SelfContained,
    [switch]$BuildInstaller
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath($PSScriptRoot)
$output = [IO.Path]::GetFullPath($OutputDirectory)
$portable = Join-Path $output 'portable'
$installerOutput = Join-Path $output 'installer'

function Assert-ChildPath([string]$Child, [string]$Parent) {
    $childFull = [IO.Path]::GetFullPath($Child).TrimEnd('\')
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "安全檢查失敗：路徑 '$childFull' 不在 '$parentFull' 之下。"
    }
}

function Copy-Tree([string]$Source, [string]$Destination, [string[]]$ExcludedDirectories = @()) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "找不到來源資料夾：$Source" }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $args = @($Source, $Destination, '/E', '/R:2', '/W:1', '/NFL', '/NDL', '/NP')
    foreach ($excluded in $ExcludedDirectories) { $args += @('/XD', $excluded) }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "複製 '$Source' 失敗（robocopy $LASTEXITCODE）。" }
}

if (Test-Path -LiteralPath $portable) {
    Assert-ChildPath $portable $output
    Remove-Item -LiteralPath $portable -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $portable,$installerOutput | Out-Null
Set-Content -LiteralPath (Join-Path $portable 'portable.flag') -Value 'Local Model Manager portable data lives beside the executable.' -Encoding UTF8

& (Join-Path $repo 'build.ps1') -OutputDirectory $portable -SelfContained:$SelfContained
if ($LASTEXITCODE -ne 0) { throw '桌面程式建置失敗。' }

$engineExe = Join-Path ([IO.Path]::GetFullPath($LlamaRoot)) 'llama-server.exe'
if (-not (Test-Path -LiteralPath $engineExe -PathType Leaf)) {
    throw "找不到 llama-server.exe：$engineExe。請用 -LlamaRoot 指向 llama.cpp 的可執行檔資料夾。"
}
Copy-Tree ([IO.Path]::GetFullPath($LlamaRoot)) (Join-Path $portable 'llama.cpp') @('.git', 'build', 'out', 'models')

if ($ModelDirectory) {
    $modelSource = [IO.Path]::GetFullPath($ModelDirectory)
    Copy-Tree $modelSource (Join-Path $portable 'models') @('.git', '__pycache__')
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $portable 'models') | Out-Null
}

if ($PythonHome) {
    $pythonSource = [IO.Path]::GetFullPath($PythonHome)
    if (-not (Test-Path -LiteralPath (Join-Path $pythonSource 'python.exe') -PathType Leaf)) {
        throw "-PythonHome 沒有 python.exe：$pythonSource"
    }
    Copy-Tree $pythonSource (Join-Path $portable 'runtime\python') @('__pycache__', 'Lib\test', 'Scripts\__pycache__')
}

Copy-Item -LiteralPath (Join-Path $repo 'installer') -Destination (Join-Path $portable 'installer') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repo 'CONTRACT.md') -Destination (Join-Path $portable 'CONTRACT.md') -Force
Copy-Item -LiteralPath (Join-Path $repo '使用說明.md') -Destination (Join-Path $portable '使用說明.md') -Force
@('@echo off', 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install-LocalModelManager.ps1" -PortableDirectory "%~dp0"', 'pause') | Set-Content -LiteralPath (Join-Path $portable 'Install-LocalModelManager.cmd') -Encoding ASCII

@{
    product = 'Local Model Manager'
    version = '1.0.0'
    built_at_utc = [DateTime]::UtcNow.ToString('o')
    engine_source = [IO.Path]::GetFullPath($LlamaRoot)
    model_included = [bool]$ModelDirectory
    python_runtime_included = [bool]$PythonHome
    dotnet_runtime_included = [bool]$SelfContained
    portable_executable = 'LocalModelManager.exe'
    backend = 'backend/manager.py'
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $portable 'bundle-manifest.json') -Encoding UTF8

@"
Local Model Manager portable bundle

Start LocalModelManager.exe. The first run creates its data under the bundle's
data folder and, when needed, creates a private Python environment there and
installs backend/requirements.txt. Put GGUF files in the models folder or
choose another location from System settings.

This bundle contains llama.cpp under llama.cpp. Add -SelfContained when
building if the .NET desktop runtime should be copied into the bundle; the
default framework-dependent build needs the .NET 10 Windows Desktop Runtime.
"@ | Set-Content -LiteralPath (Join-Path $portable 'README-PORTABLE.txt') -Encoding UTF8

if ($BuildInstaller) {
    $iscc = $null
    $candidates = @(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $iscc = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $iscc) { throw '找不到 Inno Setup 6 的 ISCC.exe。請先安裝 Inno Setup 6，或先不加 -BuildInstaller 使用 portable 資料夾。' }
    $iss = Join-Path $repo 'installer\LocalModelManager.iss'
    & $iscc "/DSourceDir=$portable" "/DOutputDir=$installerOutput" $iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 建置失敗（$LASTEXITCODE）。" }
}

Write-Output "可攜版已完成：$portable"
if ($BuildInstaller) { Write-Output "安裝程式已完成：$installerOutput" }
$global:LASTEXITCODE = 0
