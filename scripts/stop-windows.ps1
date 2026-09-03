$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root ".runtime"

function Stop-ProcessTree([int]$ProcessId) {
  $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }
}

foreach ($name in @("backend", "python-worker", "link-workbench-worker")) {
  $pidFile = Join-Path $RuntimeDir "$name.pid"
  if (-not (Test-Path -LiteralPath $pidFile)) { continue }
  $value = [System.IO.File]::ReadAllText($pidFile).Trim()
  if ($value -match '^\d+$') {
    Stop-ProcessTree -ProcessId ([int]$value)
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

# The Windows virtual-environment launcher can hand uvicorn off to a child
# interpreter. Clear a stale listener left by an older launcher before a restart.
$workerListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $workerListeners) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
  if ($process -and $process.CommandLine -match '(?i)-m\s+uvicorn\s+worker:app') {
    Stop-ProcessTree -ProcessId ([int]$processId)
  }
}
$workbenchListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $workbenchListeners) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
  if ($process -and $process.CommandLine -match '(?i)-m\s+uvicorn\s+link_workbench_worker:app') {
    Stop-ProcessTree -ProcessId ([int]$processId)
  }
}
Write-Host "SunnyRegister native Windows services stopped."
