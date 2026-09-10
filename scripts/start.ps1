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
    $SecureKey = Read-Host "请输入模型 API Key（输入内容不会显示）" -AsSecureString
    $PlainKey = [Net.NetworkCredential]::new("", $SecureKey).Password
    if ([string]::IsNullOrWhiteSpace($PlainKey)) { throw "API Key 不能为空。" }
    $env:RAG_CHAT_API_KEY = $PlainKey
    $PlainKey = $null
}

if (-not $env:RAG_SESSION_SECRET) {
    [byte[]]$SessionBytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($SessionBytes)
    $env:RAG_SESSION_SECRET = [Convert]::ToHexString($SessionBytes)
}

Write-Host "正在检查 Chat 与 Embedding 接口..."
if (-not $SkipModelCheck) {
    & python .\tests\model_endpoints_check.py --config $ResolvedConfig
    if ($LASTEXITCODE -ne 0) {
        throw "模型接口检查未通过，网站未启动。请根据上方 chat/embedding 结果修正配置。"
    }
}

Write-Host "模型接口检查通过，正在启动 http://${HostAddress}:$Port/"
& python -m rag.cli serve --host $HostAddress --port $Port
