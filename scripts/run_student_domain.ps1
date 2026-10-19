param([int]$Port = 8081)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Project Python is missing. Set up .venv before starting the portal.'
}
Set-Location -LiteralPath $projectRoot
& $pythonPath (Join-Path $projectRoot 'web_portal.py') --host 127.0.0.1 --port $Port --database (Join-Path $projectRoot 'data\cbvms.db') --public-origin https://studentportal.bond
exit $LASTEXITCODE
