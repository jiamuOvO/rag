# Ask for the model API keys the same way scripts\start.ps1 does, then run one command with them
# set.  config.yaml is forbidden from holding a literal api_key (config.py rejects it), and the
# keys are never written to disk - so any non-serve process that needs to call a model has to
# collect them here.
#
# Usage:
#   .\scripts\with_model_keys.ps1 "-m rag.cli evaluate"
#   .\scripts\with_model_keys.ps1 "tests\retrieval_metrics.py --dense --json var\metrics.json"
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Command,
    [switch]$UseSameApiKey
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectDir

function Read-Secret([string]$Prompt) {
    $Secure = Read-Host $Prompt -AsSecureString
    $Plain = [Net.NetworkCredential]::new("", $Secure).Password
    if ([string]::IsNullOrWhiteSpace($Plain)) { throw "Key cannot be empty." }
    return $Plain
}

if (-not $env:RAG_CHAT_API_KEY) {
    $env:RAG_CHAT_API_KEY = Read-Secret "Enter Chat API Key (input is hidden)"
}
if (-not $env:RAG_EMBEDDING_API_KEY) {
    if ($UseSameApiKey) {
        $env:RAG_EMBEDDING_API_KEY = $env:RAG_CHAT_API_KEY
    }
    else {
        $env:RAG_EMBEDDING_API_KEY = Read-Secret "Enter Embedding API Key (input is hidden)"
    }
}

$env:PYTHONPATH = Join-Path $ProjectDir "src"
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { $Python = "python" }

Write-Host "Running: $Python $Command"
Invoke-Expression "& '$Python' $Command"
exit $LASTEXITCODE
