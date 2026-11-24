param(
  [ValidateSet("3b","1.5b")]
  [string]$Model = "3b",
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Models = Join-Path $Root "models"
New-Item -ItemType Directory -Force -Path $Models | Out-Null

if ($Model -eq "3b") {
  $Name = "qwen2.5-3b-instruct-q4_k_m.gguf"
  $Url = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/$Name"
  $Size = 2104932768
  $Sha = "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"
} else {
  $Name = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
  $Url = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/$Name"
  $Size = 1117320736
  $Sha = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
}

$Out = Join-Path $Models $Name
if ((Test-Path $Out) -and -not $Force) {
  $len = (Get-Item $Out).Length
  if ($len -eq $Size) {
    $actual = (Get-FileHash -LiteralPath $Out -Algorithm SHA256).Hash.ToLower()
    if ($actual -eq $Sha) { Write-Host "Model already verified: $Out"; exit 0 }
  }
}

$Part = "$Out.part"
if ($Force -or -not (Test-Path $Part)) {
  Write-Host "Downloading $Name (pinned, SHA-256 verified) ..."
  & curl.exe -L --fail --retry 4 --retry-delay 3 -C - -o $Part $Url
  if ($LASTEXITCODE -ne 0) { throw "Model download failed" }
}

$len = (Get-Item $Part).Length
if ($len -ne $Size) { throw "Size mismatch: got $len expected $Size" }
Write-Host "Verifying SHA-256 ..."
$actual = (Get-FileHash -LiteralPath $Part -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $Sha) {
  Remove-Item -LiteralPath $Part -Force
  throw "SHA-256 mismatch. Deleted corrupt download. expected=$Sha actual=$actual"
}
Move-Item -LiteralPath $Part -Destination $Out -Force
Write-Host "Model verified and ready: $Out"
