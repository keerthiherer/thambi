param(
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$WhisperZipUrl = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip"
$WhisperZipSha = "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"
$WhisperZipSize = 8928640
$WhisperModelUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
$WhisperModelSha = "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"
$WhisperModelSize = 147964211
$PiperUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
$PiperSha = "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
$PiperSize = 63201294
$PiperJsonUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
$PiperJsonSha = "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0"
$PiperJsonSize = 4885
$KwsArchiveUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2"
$KwsArchiveSha = "68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6"
$KwsArchiveSize = 32885699
$KwsFiles = @(
  @{ Name = "encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx"; Sha = "408bbd740838c42d5bf6d1c5b80b3c88b616c7860b92d980328b5b068c76ae48"; Size = 4599656 },
  @{ Name = "decoder-epoch-13-avg-2-chunk-16-left-64.onnx"; Sha = "63a22dd60f40fff082ac3e09afa507f6787da36df76ded2fbe145fa233e22c21"; Size = 759829 },
  @{ Name = "joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx"; Sha = "190d4067b4cc20b72a42a1916e69d92052000fb7051a427ebb1bc72a69207dc1"; Size = 86629 },
  @{ Name = "tokens.txt"; Sha = "2d3f32311f9b692b964da3c90e830258d3e78e013cb0c992dbfb15cd5a1a71b0"; Size = 1928 },
  @{ Name = "en.phone"; Sha = "f7000ec3a90544c0c7c16090d8951779c2b322e14dad5006290f498567d439ea"; Size = 3330061 }
)

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = (Get-Command python).Source }

function Get-Asset([string]$Url, [string]$Out, [string]$Sha, [int64]$Size) {
  if ((Test-Path $Out) -and -not $Force) {
    $len = (Get-Item -LiteralPath $Out).Length
    if ($len -eq $Size) {
      $actual = (Get-FileHash -LiteralPath $Out -Algorithm SHA256).Hash.ToLower()
      if ($actual -eq $Sha) { Write-Host "  present: $(Split-Path -Leaf $Out)"; return }
    }
  }
  $dir = Split-Path -Parent $Out
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $part = "$Out.part"
  if ($Force -and (Test-Path $part)) { Remove-Item -LiteralPath $part -Force }
  if (-not (Test-Path $part)) {
    Write-Host "  downloading: $(Split-Path -Leaf $Out)"
    & curl.exe -L --fail --retry 4 --retry-delay 2 -o $part $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
  }
  $len = (Get-Item -LiteralPath $part).Length
  if ($len -ne $Size) {
    Remove-Item -LiteralPath $part -Force
    throw "Size mismatch for $(Split-Path -Leaf $Out): got $len, expected $Size"
  }
  $actual = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLower()
  if ($actual -ne $Sha) {
    Remove-Item -LiteralPath $part -Force
    throw "SHA-256 mismatch for $(Split-Path -Leaf $Out): got $actual"
  }
  Move-Item -LiteralPath $part -Destination $Out -Force
}

Write-Host ""
Write-Host "==> whisper.cpp STT" -ForegroundColor Cyan
$whisperDir = Join-Path $Root "runtime\whisper"
$whisperCli = Join-Path $whisperDir "whisper-cli.exe"
if ($Force -and (Test-Path $whisperDir)) { Remove-Item -LiteralPath $whisperDir -Recurse -Force }
if (-not (Test-Path $whisperCli)) {
  $tmp = Join-Path $Root "runtime\_whisper_extract"
  if (Test-Path $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  $zip = Join-Path $Root "runtime\_whisper.zip"
  Get-Asset $WhisperZipUrl $zip $WhisperZipSha $WhisperZipSize
  Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
  $found = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
  if (-not $found) { throw "whisper-cli.exe was not found in the release archive." }
  New-Item -ItemType Directory -Force -Path $whisperDir | Out-Null
  Get-ChildItem -LiteralPath $found.DirectoryName -Force | Copy-Item -Destination $whisperDir -Recurse -Force
  Remove-Item -LiteralPath $tmp -Recurse -Force
  Remove-Item -LiteralPath $zip -Force
  if (-not (Test-Path $whisperCli)) { throw "whisper-cli.exe installation failed." }
}
Write-Host "  ready: $whisperCli"
Get-Asset $WhisperModelUrl (Join-Path $Root "models\ggml-base.en.bin") $WhisperModelSha $WhisperModelSize

Write-Host ""
Write-Host "==> Piper TTS" -ForegroundColor Cyan
$piperDir = Join-Path $Root "models\piper"
Get-Asset $PiperUrl (Join-Path $piperDir "en_US-lessac-medium.onnx") $PiperSha $PiperSize
Get-Asset $PiperJsonUrl (Join-Path $piperDir "en_US-lessac-medium.onnx.json") $PiperJsonSha $PiperJsonSize

Write-Host ""
Write-Host "==> sherpa-onnx wake word" -ForegroundColor Cyan
$kwsDir = Join-Path $Root "models\kws"
$needArchive = $false
foreach ($f in $KwsFiles) {
  $out = Join-Path $kwsDir $f.Name
  if (-not (Test-Path $out) -or (Get-Item $out).Length -ne $f.Size) { $needArchive = $true; break }
}
if ($needArchive) {
  $tmp = Join-Path $Root "runtime\_kws_extract"
  if (Test-Path $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  $archive = Join-Path $Root "runtime\_kws.tar.bz2"
  Get-Asset $KwsArchiveUrl $archive $KwsArchiveSha $KwsArchiveSize
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  & tar.exe -xjf $archive -C $tmp
  if ($LASTEXITCODE -ne 0) { throw "Could not extract the wake-word archive." }
  $src = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "tokens.txt" | Select-Object -First 1
  if (-not $src) { throw "Wake-word model files were not found in the archive." }
  New-Item -ItemType Directory -Force -Path $kwsDir | Out-Null
  foreach ($f in $KwsFiles) {
    $from = Join-Path $src.DirectoryName $f.Name
    if (-not (Test-Path $from)) { throw "Missing $($f.Name) in the wake-word archive." }
    Copy-Item -LiteralPath $from -Destination (Join-Path $kwsDir $f.Name) -Force
  }
  Remove-Item -LiteralPath $tmp -Recurse -Force
  Remove-Item -LiteralPath $archive -Force
}
foreach ($f in $KwsFiles) {
  $out = Join-Path $kwsDir $f.Name
  $len = (Get-Item -LiteralPath $out).Length
  $actual = (Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash.ToLower()
  if ($len -ne $f.Size -or $actual -ne $f.Sha) { throw "Verification failed for $($f.Name)." }
  Write-Host "  ready: $($f.Name)"
}
$lexicon = Join-Path $kwsDir "lexicon.txt"
Get-Content -LiteralPath (Join-Path $kwsDir "en.phone") | Set-Content -LiteralPath $lexicon -Encoding UTF8
Add-Content -LiteralPath $lexicon -Encoding UTF8 -Value @(
  "THAMBI TH AE1 M B IY0",
  "THAMBEE TH AH1 M B IY0",
  "THUMBI T AH1 M B IY0"
)
$rawKeywords = Join-Path $kwsDir "keywords_raw.txt"
@(
  "HEY THAMBI @HEY_THAMBI",
  "HEY THAMBEE @HEY_THAMBI",
  "HEY THUMBI @HEY_THAMBI"
) | Set-Content -LiteralPath $rawKeywords -Encoding ASCII
& $venvPython (Join-Path $PSScriptRoot "make_keywords.py") `
  --tokens (Join-Path $kwsDir "tokens.txt") `
  --lexicon $lexicon `
  --input $rawKeywords `
  --output (Join-Path $kwsDir "keywords.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not generate the wake-word tokens." }

Write-Host ""
Write-Host "==> Voice self-check" -ForegroundColor Cyan
& $venvPython -m aether.voice --check
if ($LASTEXITCODE -ne 0) { throw "Voice self-check failed." }

Write-Host ""
Write-Host "Voice is ready." -ForegroundColor Green
Write-Host "Say: Hey Thambi    (wake word threshold may need tuning in config/aether.json)"
Write-Host "Microphone test:   .\.venv\Scripts\python.exe -m aether.voice --listen"
