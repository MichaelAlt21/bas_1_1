$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $projectRoot 'backend'
$frontend = Join-Path $projectRoot 'frontend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host 'Backend .venv not found. First-time setup:' -ForegroundColor Red
    Write-Host '  cd backend'
    Write-Host '  python -m venv .venv'
    Write-Host "  .venv\Scripts\python.exe -m pip install -e '.[dev]'"
    exit 1
}

$indexHtml = Join-Path $frontend 'dist\index.html'
if (-not (Test-Path $indexHtml)) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Write-Host 'Frontend is not built and npm is not on PATH. Build it once with: cd frontend; npm ci; npm run build' -ForegroundColor Red
        exit 1
    }
    Write-Host 'Building frontend (first run only)...'
    Push-Location $frontend
    try {
        if (-not (Test-Path 'node_modules')) { & npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' } }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}

Write-Host 'Starting KUMOsystems on http://127.0.0.1:8000 ...'
Start-Process 'http://127.0.0.1:8000'
Push-Location $backend
try {
    & $venvPython -m uvicorn bas.main:app --host 127.0.0.1 --port 8000
} finally { Pop-Location }
