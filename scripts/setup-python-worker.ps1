param(
  [string]$PythonExe = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$WorkerDir = Join-Path $Root "python-worker"
$VenvDir = Join-Path $WorkerDir ".venv"

function Test-PythonExecutable([string]$Candidate) {
  if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
  try {
    & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Invoke-Native([string]$Command, [string[]]$Arguments) {
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed ($LASTEXITCODE): $Command $($Arguments -join ' ')"
  }
}

if (-not $PythonExe) {
  if ($env:PYTHON -and (Test-PythonExecutable $env:PYTHON)) {
    $PythonExe = $env:PYTHON
  } else {
    $candidates = @(
      (Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
      (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
      "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
      "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
      "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    ) | Where-Object { $_ }
    $PythonExe = $candidates | Where-Object { Test-PythonExecutable $_ } | Select-Object -First 1
  }
}
if (-not (Test-PythonExecutable $PythonExe)) {
  throw "Python 3.12+ was not found. Install Python or pass -PythonExe with a working python.exe path."
}

Write-Host "Using Python: $PythonExe"
if ((Test-Path $VenvDir) -and ($Force -or -not (Test-PythonExecutable (Join-Path $VenvDir "Scripts\python.exe")))) {
  Write-Host "Rebuilding Python Worker venv: $VenvDir"
  Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path $VenvDir)) {
  Invoke-Native $PythonExe @("-m", "venv", $VenvDir)
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-PythonExecutable $VenvPython)) { throw "Python Worker virtual environment could not be created." }
Invoke-Native $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Native $VenvPython @("-m", "pip", "install", "-r", (Join-Path $WorkerDir "requirements.txt"))
Invoke-Native $VenvPython @("-m", "playwright", "install", "chromium")
Invoke-Native $VenvPython @("-m", "camoufox", "fetch")

Write-Host "Python Worker venv is ready: $VenvDir"
