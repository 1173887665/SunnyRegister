param(
  [switch]$NoBuild,
  [switch]$SkipUnitTests,
  [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
$WorkerPython = Join-Path $Root "python-worker\.venv\Scripts\python.exe"
Set-Location $Root
$env:GOCACHE = Join-Path $Root ".gocache"

function Invoke-Native([string]$Command, [string[]]$Arguments) {
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $Command @Arguments
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($exitCode -ne 0) {
    throw "Command failed ($exitCode): $Command $($Arguments -join ' ')"
  }
}

function Test-NativeCommand([scriptblock]$Command) {
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "SilentlyContinue"
    & $Command *> $null
    return $LASTEXITCODE -eq 0
  } finally {
    $ErrorActionPreference = $previousPreference
  }
}

function Invoke-Compose([string[]]$Arguments) {
  if ($script:ComposePlugin) {
    Invoke-Native "docker" (@("compose") + $Arguments)
  } else {
    Invoke-Native "docker-compose" $Arguments
  }
}

function Get-EnvValue([string]$Key, [string]$DefaultValue) {
  if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
      if ($line -match "^$([regex]::Escape($Key))=(.*)$") {
        $value = $Matches[1].Trim()
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
      }
    }
  }
  return $DefaultValue
}

function Test-PythonExecutable([string]$Candidate) {
  if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
  try {
    & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Find-Python {
  $candidates = @(
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
  ) | Where-Object { $_ }
  return $candidates | Where-Object { Test-PythonExecutable $_ } | Select-Object -First 1
}

function Show-ComposeDiagnostics {
  Write-Host ""
  Write-Host "Docker Compose diagnostics:" -ForegroundColor Yellow
  try { Invoke-Compose @("ps", "-a") } catch { Write-Warning $_ }
  try { Invoke-Compose @("logs", "--tail=120", "postgres", "python-worker", "sunnyregister") } catch { Write-Warning $_ }
}

try {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install and start Docker Desktop first."
  }
  $script:ComposePlugin = Test-NativeCommand { docker compose version }
  if (-not $script:ComposePlugin -and -not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    throw "Docker Compose was not found. Install Docker Desktop with Compose v2."
  }
  if (-not (Test-NativeCommand { docker info })) {
    throw "Docker daemon is not available. Start Docker Desktop and retry."
  }

  Write-Host "[1/4] Validating Docker Compose configuration" -ForegroundColor Cyan
  if ($script:ComposePlugin) {
    Invoke-Native "docker" @("compose", "--env-file", ".env.production.example", "config", "--quiet")
  } else {
    Invoke-Native "docker-compose" @("--env-file", ".env.production.example", "config", "--quiet")
  }

  if (-not $SkipUnitTests) {
    Write-Host "[2/4] Running frontend, backend, and Worker checks" -ForegroundColor Cyan
    foreach ($command in @("go", "npm.cmd")) {
      if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command was not found. Install the required local development toolchain."
      }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $Root "frontend\node_modules"))) {
      if ($SkipDependencyInstall) { throw "frontend/node_modules is missing and -SkipDependencyInstall was specified." }
      Push-Location (Join-Path $Root "frontend")
      try { Invoke-Native "npm.cmd" @("ci") } finally { Pop-Location }
    }
    Push-Location (Join-Path $Root "frontend")
    try {
      Invoke-Native "npm.cmd" @("run", "lint")
      Invoke-Native "npm.cmd" @("run", "build")
    } finally { Pop-Location }

    Push-Location (Join-Path $Root "backend")
    try {
      Invoke-Native "go" @("test", "-count=1", "./...")
      Invoke-Native "go" @("vet", "./...")
    } finally { Pop-Location }

    if (-not (Test-PythonExecutable $WorkerPython)) {
      if ($SkipDependencyInstall) { throw "Python Worker venv is missing and -SkipDependencyInstall was specified." }
      $python = Find-Python
      if (-not $python) { throw "Python 3.12+ was not found." }
      Invoke-Native $python @("-m", "venv", (Join-Path $Root "python-worker\.venv"))
    }
    if (-not $SkipDependencyInstall) {
      Invoke-Native $WorkerPython @("-m", "pip", "install", "--disable-pip-version-check", "--requirement", (Join-Path $Root "python-worker\requirements-test.txt"))
    }
    Invoke-Native $WorkerPython @("-m", "pip", "check")
    Invoke-Native $WorkerPython @("-m", "compileall", "-q", (Join-Path $Root "python-worker"))
    $previousPythonPath = $env:PYTHONPATH
    try {
      $env:PYTHONPATH = Join-Path $Root "python-worker"
      Invoke-Native $WorkerPython @("-m", "pytest", "-q", (Join-Path $Root "python-worker\tests"))
    } finally {
      $env:PYTHONPATH = $previousPythonPath
    }
  } else {
    Write-Host "[2/4] Unit checks skipped" -ForegroundColor DarkYellow
  }

  Write-Host "[3/4] Starting the complete Docker environment" -ForegroundColor Cyan
  $upArguments = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "docker-up.ps1"))
  if ($NoBuild) { $upArguments += "-NoBuild" }
  Invoke-Native "powershell" $upArguments

  Write-Host "[4/4] Verifying PostgreSQL, Worker, and Web readiness" -ForegroundColor Cyan
  $postgresUser = Get-EnvValue "POSTGRES_USER" "sunnyregister"
  $postgresDatabase = Get-EnvValue "POSTGRES_DB" "sunnyregister"
  Invoke-Compose @("exec", "-T", "postgres", "pg_isready", "-U", $postgresUser, "-d", $postgresDatabase)
  Invoke-Compose @("exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", $postgresUser, "-d", $postgresDatabase, "-tAc", "SELECT 1")
  Invoke-Compose @("exec", "-T", "python-worker", "curl", "-fsS", "http://127.0.0.1:8765/health")

  $port = Get-EnvValue "SUNNYREGISTER_PORT" "8000"
  foreach ($path in @("/api/ready", "/api/health")) {
    $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port$path" -TimeoutSec 10
    if ($response.StatusCode -ne 200) { throw "$path returned HTTP $($response.StatusCode)." }
  }

  Invoke-Compose @("ps")
  Write-Host ""
  Write-Host "Local test environment passed all checks: http://127.0.0.1:$port" -ForegroundColor Green
  Write-Host "The environment remains running. Stop it with .\scripts\docker-down.ps1"
} catch {
  Write-Error $_ -ErrorAction Continue
  if ($script:ComposePlugin -or (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    Show-ComposeDiagnostics
  }
  exit 1
}
