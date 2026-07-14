param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8091
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project virtual environment was not found: $Python"
}

$env:CDR_CONVERTER_URL = "http://${HostAddress}:$Port"
Write-Host "CorelDRAW converter: $env:CDR_CONVERTER_URL"
& $Python -m uvicorn workers.coreldraw_converter.app:app --host $HostAddress --port $Port
