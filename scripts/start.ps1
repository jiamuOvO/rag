[CmdletBinding()]
param(
    [string]$ConfigPath = "config.yaml",
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [switch]$SkipModelCheck
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectDir

$ResolvedConfig = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
$env:RAG_CONFIG_FILE = $ResolvedConfig

if (-not $env:RAG_CHAT_API_KEY) {
    $SecureKey = Read-Host "Enter model API Key (input is hidden)" -AsSecureString
    $PlainKey = [Net.NetworkCredential]::new("", $SecureKey).Password
    if ([string]::IsNullOrWhiteSpace($PlainKey)) { throw "API Key cannot be empty." }
    $env:RAG_CHAT_API_KEY = $PlainKey
    $PlainKey = $null
}

if (-not $env:RAG_SESSION_SECRET) {
    [byte[]]$SessionBytes = [byte[]]::new(32)
    $RandomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $RandomGenerator.GetBytes($SessionBytes)
    }
    finally {
        $RandomGenerator.Dispose()
    }
    $env:RAG_SESSION_SECRET = [BitConverter]::ToString($SessionBytes).Replace("-", "")
}

Write-Host "Checking Chat and Embedding endpoints..."
if (-not $SkipModelCheck) {
    & python .\tests\model_endpoints_check.py --config $ResolvedConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Model endpoint check failed. Fix the chat/embedding configuration shown above."
    }
}

Write-Host "Model endpoint check passed. Starting http://${HostAddress}:$Port/"
& python -m rag.cli serve --host $HostAddress --port $Port
