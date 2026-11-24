param(
  [string]$Tag = "b12000",
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "runtime"
$Dest = Join-Path $Runtime "llama"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

if ((Test-Path (Join-Path $Dest "llama-server.exe")) -and -not $Force) {
  Write-Host "llama.cpp already present at $Dest"
  exit 0
}

$Base = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag"
$Files = @(
  @{ Name = "llama-$Tag-bin-win-cuda-12.4-x64.zip"; Out = "llama.zip" },
  @{ Name = "cudart-llama-bin-win-cuda-12.4-x64.zip"; Out = "cudart.zip" }
)

$Tmp = Join-Path $Runtime "_download"
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
foreach ($f in $Files) {
  $out = Join-Path $Tmp $f.Out
  if (-not (Test-Path $out) -or $Force) {
    Write-Host "Downloading $($f.Name) ..."
    & curl.exe -L --fail --retry 4 --retry-delay 2 -o $out "$Base/$($f.Name)"
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($f.Name)" }
  }
}

Write-Host "Extracting llama.cpp ..."
if (Test-Path $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Expand-Archive -LiteralPath (Join-Path $Tmp "llama.zip") -DestinationPath $Dest -Force
Expand-Archive -LiteralPath (Join-Path $Tmp "cudart.zip") -DestinationPath $Dest -Force

# Some zips contain one nested folder. Flatten if so.
$server = Get-ChildItem -LiteralPath $Dest -Recurse -Filter "llama-server.exe" | Select-Object -First 1
if (-not $server) { throw "llama-server.exe not found after extraction" }
if ($server.DirectoryName -ne $Dest) {
  Get-ChildItem -LiteralPath $server.DirectoryName -Force | Move-Item -Destination $Dest -Force
}
$server = Join-Path $Dest "llama-server.exe"
if (-not (Test-Path $server)) { throw "llama-server.exe not found at expected path" }
Write-Host "llama.cpp ready: $server"
& $server --version 2>&1 | Select-Object -First 3
