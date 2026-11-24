$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot "download_llama.ps1")
& (Join-Path $PSScriptRoot "download_model.ps1") -Model 3b
Write-Host "PHASE1_DOWNLOAD_OK" | Tee-Object -FilePath (Join-Path $Root "data\logs\download_phase1.log") -Append
