param(
  [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    if ($script:ComposePlugin) {
      & docker compose @Arguments
    } else {
      & docker-compose @Arguments
    }
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($exitCode -ne 0) { throw "Docker Compose command failed ($exitCode): $($Arguments -join ' ')" }
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

function New-Secret([int]$Bytes = 24) {
  $buffer = New-Object byte[] $Bytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
  return -join ($buffer | ForEach-Object { $_.ToString("x2") })
}

function Set-EnvValue([string]$Key, [string]$Value) {
  $lines = [System.Collections.Generic.List[string]]::new()
  if (Test-Path -LiteralPath $EnvFile) {
    [System.IO.File]::ReadAllLines($EnvFile) | ForEach-Object { [void]$lines.Add($_) }
  }
  $found = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^$([regex]::Escape($Key))=") {
      $lines[$i] = "$Key=$Value"
      $found = $true
      break
    }
  }
  if (-not $found) { [void]$lines.Add("$Key=$Value") }
  [System.IO.File]::WriteAllLines($EnvFile, $lines, [System.Text.UTF8Encoding]::new($false))
}

function Get-EnvValue([string]$Key) {
  if (-not (Test-Path -LiteralPath $EnvFile)) { return "" }
  foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
    if ($line -match "^$([regex]::Escape($Key))=(.*)$") { return $Matches[1].Trim() }
  }
  return ""
}

Set-Location $Root

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$legacyComposeCommand = Get-Command docker-compose -ErrorAction SilentlyContinue
$script:ComposePlugin = $false
if ($dockerCommand -and (Test-NativeCommand { docker compose version })) {
  $script:ComposePlugin = $true
} elseif ($legacyComposeCommand) {
  $script:ComposePlugin = $false
} else {
  throw "Docker Compose was not found. Install Docker Desktop with Compose v2."
}

if (-not $dockerCommand) { throw "Docker CLI was not found. Install and start Docker Desktop first." }
if (-not (Test-NativeCommand { docker info })) { throw "Docker daemon is not running. Start Docker Desktop first." }

if (-not (Test-Path -LiteralPath $EnvFile)) {
  Copy-Item -LiteralPath (Join-Path $Root ".env.production.example") -Destination $EnvFile
}

$adminPassword = Get-EnvValue "ADMIN_PASSWORD"
if ([string]::IsNullOrWhiteSpace($adminPassword) -or $adminPassword -like "change-me-*") {
  $adminPassword = New-Secret 16
  Set-EnvValue "ADMIN_PASSWORD" $adminPassword
}
$workerToken = Get-EnvValue "PYTHON_WORKER_TOKEN"
if ([string]::IsNullOrWhiteSpace($workerToken) -or $workerToken -like "change-me-*") {
  Set-EnvValue "PYTHON_WORKER_TOKEN" (New-Secret 24)
}

$arguments = @("up", "-d", "--remove-orphans")
if (-not $NoBuild) { $arguments += "--build" }
Invoke-Compose @arguments

$port = Get-EnvValue "SUNNYREGISTER_PORT"
if ([string]::IsNullOrWhiteSpace($port)) { $port = "8000" }
$readyUrl = "http://127.0.0.1:$port/api/ready"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $readyUrl -TimeoutSec 2
    if ($response.StatusCode -eq 200) { $ready = $true; break }
  } catch { Start-Sleep -Seconds 2 }
}

Invoke-Compose ps
if (-not $ready) {
  Write-Host "Services started but readiness timed out. Check logs with: docker compose logs -f" -ForegroundColor Yellow
  exit 1
}

Write-Host ""
Write-Host "SunnyRegister is ready: http://127.0.0.1:$port" -ForegroundColor Green
$adminUsername = Get-EnvValue "ADMIN_USERNAME"
if ([string]::IsNullOrWhiteSpace($adminUsername)) { $adminUsername = "admin" }
$novncPort = Get-EnvValue "NOVNC_PORT"
if ([string]::IsNullOrWhiteSpace($novncPort)) { $novncPort = "6080" }
Write-Host "Username: $adminUsername"
Write-Host "Password: stored in .env (ADMIN_PASSWORD); it is not printed for security"
Write-Host "noVNC: http://127.0.0.1:$novncPort/vnc.html"
