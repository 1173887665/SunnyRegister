param(
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root "backend"
$DbPath = (Join-Path $Root "data\account_manager.db").Replace('\', '/')

$env:PORT = "$Port"
$env:ACCOUNT_MANAGER_DATABASE_URL = "sqlite:///$DbPath"
$env:PYTHON_WORKER_URL = "http://127.0.0.1:8765"
$env:PYTHON_TASK_TYPES = "sunny_register,sunny_login,sunny_refresh_session"
$env:TZ = "Asia/Shanghai"

Set-Location $BackendDir
go run .
