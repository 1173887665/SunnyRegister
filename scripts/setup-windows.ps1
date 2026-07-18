param(
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BinDir = Join-Path $Root "bin"

foreach ($command in @("node", "npm", "go")) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "$command was not found. Install Node.js 22+, Go 1.23+ and Python 3.12+."
  }
}

& (Join-Path $PSScriptRoot "setup-python-worker.ps1") -PythonExe $PythonExe

Push-Location (Join-Path $Root "frontend")
try {
  npm ci
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally {
  Pop-Location
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Push-Location (Join-Path $Root "backend")
try {
  go build -trimpath -ldflags="-s -w" -o (Join-Path $BinDir "SunnyRegister.exe") .
  if ($LASTEXITCODE -ne 0) { throw "Go build failed" }
} finally {
  Pop-Location
}

Write-Host "SunnyRegister native Windows runtime is ready." -ForegroundColor Green
