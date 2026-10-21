param([string]$TesseractDirectory = 'C:\Program Files\Tesseract-OCR')
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $workspace 'instance\tessdata'
$executable = Join-Path $TesseractDirectory 'tesseract.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    $found = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($found) { $executable = $found.Source }
}
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'Install the local Windows Tesseract OCR application, or supply -TesseractDirectory. This script does not install a machine-wide program.'
}
New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
$revision = '87416418657359cb625c412a48b6e1d6d41c29bd'
$hashes = @{
    mon = 'A151A3806D61AC43619CD383896D551BA5C3B07388FFEC6FC83C8C604D677570'
    eng = '7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2'
}
foreach ($language in @('mon','eng')) {
    $destination = Join-Path $dataDirectory "$language.traineddata"
    $temporary = "$destination.download"
    try {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$revision/$language.traineddata" -OutFile $temporary
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash
        if ($actual -ne $hashes[$language]) { throw "Checksum mismatch for $language.traineddata" }
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
Write-Output "Tesseract executable: $executable"
Write-Output "Language data: $dataDirectory"
Write-Output 'Set these local .env values (the data stays under instance/tessdata):'
Write-Output "TESSERACT_CMD=$($executable -replace '\\','/')"
Write-Output "TESSDATA_PREFIX=$($dataDirectory -replace '\\','/')"
Write-Output 'Pinned model source: tesseract-ocr/tessdata_fast at commit 87416418657359cb625c412a48b6e1d6d41c29bd'
foreach ($language in @('mon','eng')) { Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $dataDirectory "$language.traineddata") }
