$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m tests.test_mcp
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m tests.test_api
exit $LASTEXITCODE
