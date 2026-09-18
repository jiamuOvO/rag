# API key is read by Python getpass; it is never saved to disk or echoed.
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$benchmarkProject = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$benchmarkPython = Join-Path $benchmarkProject '.venv\Scripts\python.exe'
& $benchmarkPython (Join-Path $PSScriptRoot 'annotate.py') run --prompt-key
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $benchmarkPython (Join-Path $PSScriptRoot 'validate.py')
