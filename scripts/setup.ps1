$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectDir
$Python = $null
foreach ($Version in @('3.12', '3.11')) {
    & py "-$Version" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $Python = $Version; break }
}
if (-not $Python) { throw "Python 3.11 or 3.12 is required." }
& py "-$Python" -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
& .\.venv\Scripts\python.exe -m pip install -e .
Write-Host "Ready. Run: .\.venv\Scripts\python.exe -m rag.cli doctor"
