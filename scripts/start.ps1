[CmdletBinding()]
param(
    [string]$ConfigPath = "config.yaml",
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [switch]$UseSameApiKey,
    [switch]$SkipModelCheck,
    [switch]$Benchmark
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectDir

$ResolvedConfig = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
$env:RAG_CONFIG_FILE = $ResolvedConfig

if (-not $env:RAG_CHAT_API_KEY) {
    $SecureKey = Read-Host "Enter Chat API Key (input is hidden)" -AsSecureString
    $PlainKey = [Net.NetworkCredential]::new("", $SecureKey).Password
    if ([string]::IsNullOrWhiteSpace($PlainKey)) { throw "API Key cannot be empty." }
    $env:RAG_CHAT_API_KEY = $PlainKey
    $PlainKey = $null
}

if (-not $env:RAG_EMBEDDING_API_KEY) {
    if ($UseSameApiKey) {
        $env:RAG_EMBEDDING_API_KEY = $env:RAG_CHAT_API_KEY
    }
    else {
        $SecureEmbeddingKey = Read-Host "Enter Embedding API Key (input is hidden)" -AsSecureString
        $PlainEmbeddingKey = [Net.NetworkCredential]::new("", $SecureEmbeddingKey).Password
        if ([string]::IsNullOrWhiteSpace($PlainEmbeddingKey)) { throw "Embedding API Key cannot be empty." }
        $env:RAG_EMBEDDING_API_KEY = $PlainEmbeddingKey
        $PlainEmbeddingKey = $null
    }
}

if (-not $env:RAG_ADMIN_PASSWORD_HASH) {
    $VarDir = Join-Path $ProjectDir "var"
    if (-not (Test-Path -LiteralPath $VarDir)) {
        New-Item -ItemType Directory -Path $VarDir | Out-Null
    }
    $AdminHashFile = Join-Path $VarDir "admin_password_hash.txt"

    if ((Test-Path -LiteralPath $AdminHashFile) -and ((Get-Content -LiteralPath $AdminHashFile -Raw) -match '\S')) {
        $env:RAG_ADMIN_PASSWORD_HASH = (Get-Content -LiteralPath $AdminHashFile -Raw).Trim()
        Write-Host "Admin password hash loaded from var\admin_password_hash.txt"
    }
    else {
        $SecureAdmin = Read-Host "Enter Admin password (input is hidden)" -AsSecureString
        $PlainAdmin = [Net.NetworkCredential]::new("", $SecureAdmin).Password
        $SecureRepeat = Read-Host "Repeat Admin password (input is hidden)" -AsSecureString
        $PlainRepeat = [Net.NetworkCredential]::new("", $SecureRepeat).Password
        if ([string]::IsNullOrWhiteSpace($PlainAdmin) -or $PlainAdmin -ne $PlainRepeat) {
            $PlainAdmin = $null
            $PlainRepeat = $null
            throw "Admin passwords do not match or are empty."
        }

        $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        $PendingFile = Join-Path $VarDir ".admin_password_pending"
        [System.IO.File]::WriteAllText($PendingFile, $PlainAdmin, $Utf8NoBom)
        $PlainAdmin = $null
        $PlainRepeat = $null

        $env:PYTHONPATH = Join-Path $ProjectDir "src"
        $HashPython = "python"
        $VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $VenvPython) {
            $HashPython = $VenvPython
        }
        $HashCode = "import pathlib, sys; from rag.security import hash_password; print(hash_password(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')))"
        try {
            $HashValue = & $HashPython -c $HashCode $PendingFile
        }
        finally {
            Remove-Item -LiteralPath $PendingFile -Force -ErrorAction SilentlyContinue
        }

        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($HashValue)) {
            throw "Failed to build the admin password hash. Check that 'python' can import the rag package."
        }
        $env:RAG_ADMIN_PASSWORD_HASH = $HashValue.Trim()
        [System.IO.File]::WriteAllText($AdminHashFile, $env:RAG_ADMIN_PASSWORD_HASH, $Utf8NoBom)
        Write-Host "Admin password hash saved to var\admin_password_hash.txt (git-ignored); it will be reused on future starts."
    }
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
    & python .\tests\model_connection_demo.py --config $ResolvedConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Model endpoint check failed. Fix the chat/embedding configuration shown above."
    }
}

Write-Host "Model endpoint check passed. Starting http://${HostAddress}:$Port/"
if ($Benchmark) {
    if ($HostAddress -notin @('127.0.0.1', 'localhost', '::1')) {
        throw 'Benchmark mode must listen on loopback only.'
    }
    $BenchmarkRuntime = Join-Path $ProjectDir 'tests\biomass_furan\runtime'
    New-Item -ItemType Directory -Path $BenchmarkRuntime -Force | Out-Null
    $BenchmarkTokenFile = Join-Path $BenchmarkRuntime 'bridge_token.txt'
    $env:RAG_BENCHMARK_TOKEN = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
    [IO.File]::WriteAllText($BenchmarkTokenFile, $env:RAG_BENCHMARK_TOKEN)
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Write-Host 'Local frozen-dataset annotation bridge enabled. Model API keys remain inside this process.'
}
else {
    Remove-Item Env:RAG_BENCHMARK_TOKEN -ErrorAction SilentlyContinue
}
try {
    & python -m rag.cli serve --host $HostAddress --port $Port
}
finally {
    if ($Benchmark) {
        Remove-Item Env:RAG_BENCHMARK_TOKEN -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BenchmarkTokenFile -Force -ErrorAction SilentlyContinue
    }
}
