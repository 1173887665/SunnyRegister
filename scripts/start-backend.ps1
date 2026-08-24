param(
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root "backend"

$env:PORT = "$Port"
if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is required. Configure PostgreSQL before starting the backend."
}
$env:PYTHON_WORKER_URL = "http://127.0.0.1:8765"
$env:PYTHON_TASK_TYPES = "sunny_register,sunny_login,sunny_refresh_session,sunny_acquire_rt,sunny_rebind"
$env:TZ = "Asia/Shanghai"
$env:SUNNY_TIMEZONE = "Asia/Shanghai"
$env:SUNNY_HEALTHCHECK_ENABLED = if ($env:SUNNY_HEALTHCHECK_ENABLED) { $env:SUNNY_HEALTHCHECK_ENABLED } else { "true" }
$env:SUNNY_HEALTHCHECK_TIME = if ($env:SUNNY_HEALTHCHECK_TIME) { $env:SUNNY_HEALTHCHECK_TIME } else { "06:00" }
$env:SUNNY_HEALTHCHECK_CONCURRENCY = if ($env:SUNNY_HEALTHCHECK_CONCURRENCY) { $env:SUNNY_HEALTHCHECK_CONCURRENCY } else { "2" }

Set-Location $BackendDir
go run .
