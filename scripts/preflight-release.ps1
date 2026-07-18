param([switch]$RunBuild)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Failures = [System.Collections.Generic.List[string]]::new()

$trackedSensitive = git ls-files | Select-String -Pattern '(^|/)(\.env|data|secrets|backups)(/|$)|\.(db|sqlite|pem|key|p12|log)$'
if ($trackedSensitive) { $Failures.Add("Tracked sensitive/runtime files:`n$($trackedSensitive -join "`n")") }

$historySensitive = git log --all --format=oneline -- data backend/data .env secrets 2>$null
if ($historySensitive) { $Failures.Add("Sensitive paths exist in Git history; purge history before making the repository public.") }

$patterns = 'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'
$matches = git grep -n -I -E $patterns -- . ':!frontend/package-lock.json' 2>$null
if ($matches) { $Failures.Add("Possible committed secrets:`n$($matches -join "`n")") }

$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
docker compose version *> $null
$composeAvailable = $LASTEXITCODE -eq 0
$ErrorActionPreference = $previousErrorAction
if (-not $composeAvailable) {
  $Failures.Add("Docker Compose v2 is unavailable; production compose configuration was not validated.")
} else {
  $ErrorActionPreference = "Continue"
  docker compose --env-file .env.production.example -f docker-compose.production.yml config --quiet
  $composeValid = $LASTEXITCODE -eq 0
  $ErrorActionPreference = $previousErrorAction
  if (-not $composeValid) {
    $Failures.Add("docker-compose.production.yml failed validation.")
  }
}

if ($RunBuild) {
  Push-Location frontend
  try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { $Failures.Add("Frontend build failed.") }
  } finally { Pop-Location }
  Push-Location backend
  try {
    go test ./...
    if ($LASTEXITCODE -ne 0) { $Failures.Add("Go tests failed.") }
  } finally { Pop-Location }
  $workerPython = Join-Path $Root "python-worker\.venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $workerPython) {
    & $workerPython -m compileall -q python-worker
    if ($LASTEXITCODE -ne 0) { $Failures.Add("Python Worker compile check failed.") }
  } else {
    $Failures.Add("Python Worker virtual environment is missing; run scripts\setup-python-worker.ps1 first.")
  }
}

if ($Failures.Count -gt 0) {
  $Failures | ForEach-Object { Write-Error $_ }
  exit 1
}
Write-Host "SunnyRegister release preflight passed." -ForegroundColor Green
