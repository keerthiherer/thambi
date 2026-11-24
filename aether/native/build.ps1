$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $here "aether_native.exe"
& g++ -O2 -std=c++17 -static -municode -o $out (Join-Path $here "aether_native.cpp") -lbcrypt
if ($LASTEXITCODE -ne 0) { throw "native build failed" }
Write-Host "Built $out"
& $out disk
