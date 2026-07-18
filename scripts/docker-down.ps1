$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

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

function Invoke-DockerCommand([scriptblock]$Command) {
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $Command
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  if ($exitCode -ne 0) { throw "Docker Compose down failed with exit code $exitCode." }
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$legacyComposeCommand = Get-Command docker-compose -ErrorAction SilentlyContinue
if ($dockerCommand -and (Test-NativeCommand { docker compose version })) {
  Invoke-DockerCommand { docker compose down }
} elseif ($legacyComposeCommand) {
  Invoke-DockerCommand { docker-compose down }
} else {
  throw "Docker Compose was not found."
}
