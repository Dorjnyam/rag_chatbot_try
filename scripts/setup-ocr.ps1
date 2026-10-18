param([string]$TesseractDirectory = 'C:\Program Files\Tesseract-OCR')
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $workspace 'instance\tessdata'
if (-not (Test-Path -LiteralPath (Join-Path $TesseractDirectory 'tesseract.exe'))) {
    throw 'Install UB-Mannheim.TesseractOCR first, or supply -TesseractDirectory.'
}
New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
foreach ($language in @('eng','osd')) {
    Copy-Item -LiteralPath (Join-Path $TesseractDirectory "tessdata\$language.traineddata") -Destination (Join-Path $dataDirectory "$language.traineddata")
}
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/mon.traineddata' -OutFile (Join-Path $dataDirectory 'mon.traineddata')
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $dataDirectory 'mon.traineddata')
Write-Output 'Application instance/tessdata contains eng, mon, and osd. Record these hashes when freezing OCR experiments.'
