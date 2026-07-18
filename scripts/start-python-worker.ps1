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

try {
  & $VenvPython --version | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Python Worker venv python exited with code $LASTEXITCODE"
  }
} catch {
  Write-Host "Python Worker venv is broken. Rebuilding..."
  & (Join-Path $PSScriptRoot "setup-python-worker.ps1") -Force
}

$env:PYTHONUTF8 = "1"
$env:ACCOUNT_MANAGER_DATABASE_URL = "sqlite:///$((Join-Path $Root 'data\account_manager.db').Replace('\','/'))"

Set-Location $WorkerDir
& $VenvPython -m uvicorn worker:app --host $HostName --port $Port
