$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root ".runtime"

foreach ($name in @("backend", "python-worker")) {
  $pidFile = Join-Path $RuntimeDir "$name.pid"
  if (-not (Test-Path -LiteralPath $pidFile)) { continue }
  $value = [System.IO.File]::ReadAllText($pidFile).Trim()
  if ($value -match '^\d+$') {
    $process = Get-Process -Id ([int]$value) -ErrorAction SilentlyContinue
    if ($process) { Stop-Process -Id $process.Id -Force }
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
Write-Host "SunnyRegister native Windows services stopped."
