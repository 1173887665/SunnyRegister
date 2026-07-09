param(
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$WorkerDir = Join-Path $Root "python-worker"
$VenvDir = Join-Path $WorkerDir ".venv"

if (-not $PythonExe) {
  if ($env:PYTHON -and (Test-Path $env:PYTHON)) {
    $PythonExe = $env:PYTHON
  } elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = (Get-Command python).Source
  } elseif (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe") {
    $PythonExe = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
  } else {
    throw "Python was not found. Install Python 3.12+ or pass -PythonExe with a python.exe path."
  }
}

Write-Host "Using Python: $PythonExe"
if (-not (Test-Path $VenvDir)) {
  & $PythonExe -m venv $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $WorkerDir "requirements.txt")
& $VenvPython -m playwright install chromium

Write-Host "Python Worker venv is ready: $VenvDir"
