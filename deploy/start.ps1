# Serve the app at https://voice.softsuaveai.space through Cloudflare Tunnel.
#
#   powershell -ExecutionPolicy Bypass -File deploy\start.ps1            # build, start, tunnel
#   powershell -ExecutionPolicy Bypass -File deploy\start.ps1 -SkipBuild # reuse the last build
#
# Builds the web page, starts the backend in its own window (it serves the page
# too), then runs the tunnel in this window. Ctrl+C stops the tunnel; close the
# backend's window to stop the backend.
#
# One-time setup first: see deploy\README.md.

param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# A terminal opened before cloudflared was installed (VS Code's, typically)
# still has the old PATH, so also look where the installers put it.
$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cloudflared) {
    $cloudflared = @(
        "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
        "$env:ProgramFiles\cloudflared\cloudflared.exe",
        "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $cloudflared) {
    throw 'cloudflared is not installed: winget install --id Cloudflare.cloudflared'
}

if (-not $SkipBuild) {
    Write-Host '==> Building the web page'
    Push-Location (Join-Path $root 'frontend')
    try {
        if (-not (Test-Path node_modules)) { npm ci; if ($LASTEXITCODE) { throw 'npm ci failed' } }
        npm run build
        if ($LASTEXITCODE) { throw 'npm run build failed' }
    } finally { Pop-Location }
}

if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8000 is already in use. Stop the backend that is running (Ctrl+C in its window) and run this again.'
}

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

Write-Host '==> Starting the backend on 127.0.0.1:8000 (new window)'
Start-Process powershell -WorkingDirectory (Join-Path $root 'backend') -ArgumentList @(
    '-NoExit', '-Command', "& '$python' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)

Write-Host '==> Waiting for the backend'
$up = $false
foreach ($i in 1..60) {
    try { Invoke-RestMethod http://127.0.0.1:8000/health -TimeoutSec 2 | Out-Null; $up = $true; break }
    catch { Start-Sleep -Seconds 1 }
}
if (-not $up) { throw 'The backend did not answer on :8000 within 60 s; check its window.' }

Write-Host '==> Tunnel up: https://voice.softsuaveai.space  (Ctrl+C to stop)'
& $cloudflared tunnel --config (Join-Path $PSScriptRoot 'cloudflared.yml') run
