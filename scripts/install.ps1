param(
  [switch]$SkipLlama,
  [switch]$SkipModel,
  [switch]$SkipVoice,
  [switch]$RecreateVenv
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Step([string]$Text) {
  Write-Host ""
  Write-Host "==> $Text" -ForegroundColor Cyan
}

$systemPython = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $systemPython) { throw "Python 3.10+ was not found on PATH." }
$version = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "Using Python $version from $($systemPython.Source)"

$venv = Join-Path $Root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if ($RecreateVenv -and (Test-Path $venv)) {
  Step "Removing old virtual environment"
  Remove-Item -LiteralPath $venv -Recurse -Force
}
if (-not (Test-Path $venvPython)) {
  Step "Creating .venv"
  & python -m venv $venv
  if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

Step "Installing Python dependencies"
& $venvPython -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

if (-not $SkipLlama) {
  $server = Join-Path $Root "runtime\llama\llama-server.exe"
  if (-not (Test-Path $server)) {
    Step "Downloading llama.cpp b12000 (CUDA 12.4)"
    & (Join-Path $PSScriptRoot "download_llama.ps1")
    if ($LASTEXITCODE -ne 0) { throw "llama.cpp download failed." }
  } else {
    Write-Host "llama.cpp already present."
  }
}

if (-not $SkipModel) {
  $model = Join-Path $Root "models\qwen2.5-3b-instruct-q4_k_m.gguf"
  if (-not (Test-Path $model)) {
    Step "Downloading and verifying Qwen2.5-3B-Instruct Q4_K_M"
    & (Join-Path $PSScriptRoot "download_model.ps1") -Model 3b
    if ($LASTEXITCODE -ne 0) { throw "Model download failed." }
  } else {
    Write-Host "Model already present."
  }
}

$native = Join-Path $Root "aether\native\aether_native.exe"
if (-not (Test-Path $native)) {
  Step "Building native Windows helper"
  $gpp = Get-Command g++ -ErrorAction SilentlyContinue
  if (-not $gpp) { throw "g++ (MinGW-w64) is required to build aether_native.exe." }
  & (Join-Path $Root "aether\native\build.ps1")
  if ($LASTEXITCODE -ne 0) { throw "Native helper build failed." }
} else {
  Write-Host "Native helper already present."
}

if (-not $SkipVoice) {
  Step "Installing offline voice components"
  & (Join-Path $PSScriptRoot "setup_voice.ps1")
  if ($LASTEXITCODE -ne 0) { throw "Voice setup failed." }
} else {
  Write-Host "Voice setup skipped."
}

Step "Verifying imports and tools"
& $venvPython -c "import requests, numpy, sounddevice, soundfile, piper, sherpa_onnx, docx, openpyxl, pptx, pypdf, reportlab, icalendar, pywinauto, psutil; import aether.tools; print('imports OK, tools:', len(__import__('aether.tools', fromlist=['REGISTRY']).REGISTRY))"
if ($LASTEXITCODE -ne 0) { throw "Dependency verification failed." }

Step "Hardware report"
& $venvPython -m aether --probe

Write-Host ""
Write-Host "THAMBI is installed." -ForegroundColor Green
Write-Host "Start it with:  .\run_aether.bat"
Write-Host "Text mode:      .\.venv\Scripts\python.exe -m aether --cli"
