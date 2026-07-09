param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$WorkerDir = Join-Path $Root "python-worker"
$VenvPython = Join-Path $WorkerDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
  Write-Host "Python Worker venv was not found. Initializing..."
  & (Join-Path $PSScriptRoot "setup-python-worker.ps1")
}

$env:PYTHONUTF8 = "1"
$env:ACCOUNT_MANAGER_DATABASE_URL = "sqlite:///$((Join-Path $Root 'data\account_manager.db').Replace('\','/'))"
$env:ORIGINAL_APP_PATH = (Join-Path $Root "original_runtime")

Set-Location $WorkerDir
& $VenvPython -m uvicorn worker:app --host $HostName --port $Port
