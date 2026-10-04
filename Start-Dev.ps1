<#
Run from the repository root:
  powershell -ExecutionPolicy Bypass -File .\Start-Dev.ps1
Reinstall dependencies:
  powershell -ExecutionPolicy Bypass -File .\Start-Dev.ps1 -Setup
Start infrastructure and migrate without opening servers:
  powershell -ExecutionPolicy Bypass -File .\Start-Dev.ps1 -NoServers
Start servers without the ngrok tunnel (Square webhooks won't arrive):
  powershell -ExecutionPolicy Bypass -File .\Start-Dev.ps1 -NoTunnel
Requires Docker Desktop, Node.js/npm, and Python 3.12 (for a new venv).
The ngrok tunnel requires ngrok on PATH and SQUARE_WEBHOOK_URL in .env.
Stop each dev server with Ctrl+C in its window.
Stop infrastructure with: docker compose down
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = $PSScriptRoot,
    [switch]$Setup,
    [switch]$NoServers,
    [switch]$NoTunnel
)

$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE."
    }
}

function Wait-Port {
    param([int]$Port, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $attempt = $client.ConnectAsync('127.0.0.1', $Port)
            if ($attempt.Wait(1000) -and $client.Connected) { return }
        } catch {
            # Containers may still be starting.
        } finally {
            $client.Dispose()
        }
        Start-Sleep -Seconds 1
    }
    throw "Port $Port did not become available. Check: docker compose logs"
}

function Get-WebhookDomain {
    $line = Get-Content '.env' | Where-Object { $_ -match '^\s*SQUARE_WEBHOOK_URL\s*=' } | Select-Object -First 1
    if (!$line) { return $null }
    $value = ($line -split '=', 2)[1].Trim().Trim('"', "'")
    try { return ([Uri]$value).Host } catch { return $null }
}

function Open-DevWindow {
    param([string]$Title, [string]$Command)
    $quotedRoot = $script:root.Replace("'", "''")
    $body = "Set-Location '$quotedRoot'; `$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
    ) | Out-Null
}

try {
    $script:root = (Resolve-Path -LiteralPath $RepoRoot).Path
    Push-Location -LiteralPath $script:root
    try {
        if (!(Test-Path 'pyproject.toml') -or !(Test-Path 'frontend/package.json')) {
            throw 'Place this script in the repository root, or pass -RepoRoot C:\path\to\repo.'
        }
        foreach ($command in @('docker', 'npm.cmd')) {
            if (!(Get-Command $command -ErrorAction SilentlyContinue)) {
                throw "Missing $command. Install Docker Desktop and Node.js, then reopen PowerShell."
            }
        }

        if (!(Test-Path '.env')) {
            if (!(Test-Path '.env.example')) { throw 'Missing .env and .env.example.' }
            Copy-Item '.env.example' '.env'
            Write-Host 'Created .env. Fill in the required settings (including SQUARE_TOKEN), then rerun.' -ForegroundColor Yellow
            return
        }

        $python = Join-Path $script:root '.venv\Scripts\python.exe'
        $newVenv = !(Test-Path -LiteralPath $python)
        if ($newVenv) {
            if (!(Get-Command py -ErrorAction SilentlyContinue)) {
                throw 'Install Python 3.12 with the Python launcher (py), then rerun.'
            }
            Invoke-Checked -Program 'py' -Arguments @('-3.12', '-m', 'venv', '.venv')
        }
        if ($Setup -or $newVenv) {
            Invoke-Checked -Program $python -Arguments @('-m', 'pip', 'install', '-e', '.[dev]')
        }
        if ($Setup -or !(Test-Path 'frontend/node_modules')) {
            Invoke-Checked -Program 'npm.cmd' -Arguments @('--prefix', 'frontend', 'install')
        }

        Write-Host 'Starting Docker services...' -ForegroundColor Cyan
        Invoke-Checked -Program 'docker' -Arguments @('info')
        Invoke-Checked -Program 'docker' -Arguments @('compose', 'up', '-d')
        Write-Host 'Waiting for Postgres (5433) and Redis (6380)...'
        Wait-Port -Port 5433
        Wait-Port -Port 6380

        Write-Host 'Running database migrations...' -ForegroundColor Cyan
        Invoke-Checked -Program $python -Arguments @('-m', 'alembic', 'upgrade', 'head')

        Write-Host 'Ensuring the RLS-restricted app login exists...' -ForegroundColor Cyan
        Get-Content -Raw 'scripts/sql/dev_app_login.sql' |
            docker compose exec -T postgres psql -U esl -d esl -v ON_ERROR_STOP=1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Creating the dev app login failed.' }

        $appUrl = Get-Content '.env' | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1
        if ($appUrl -match '//esl:') {
            Write-Warning 'DATABASE_URL uses the superuser, which bypasses row-level security. See .env.example.'
        }

        if (!$NoServers) {
            Open-DevWindow -Title 'ESL Backend - port 8001' -Command "& '.\.venv\Scripts\python.exe' -m uvicorn esl.web.app:app --reload --port 8001"
            Open-DevWindow -Title 'ESL Frontend' -Command 'npm.cmd --prefix frontend run dev'
            Write-Host 'Started both dev server windows. Backend: http://localhost:8001' -ForegroundColor Green
            Write-Host 'Use the frontend URL printed in its window.'

            if (!$NoTunnel) {
                $domain = Get-WebhookDomain
                if (!(Get-Command ngrok -ErrorAction SilentlyContinue)) {
                    Write-Warning 'ngrok not found on PATH; skipping tunnel. Square webhooks will not reach this machine.'
                } elseif (!$domain -or $domain -match '[<>]') {
                    Write-Warning 'SQUARE_WEBHOOK_URL in .env is missing or a placeholder; skipping tunnel.'
                } else {
                    Open-DevWindow -Title "ngrok - $domain" -Command "ngrok http 8001 --url=$domain"
                    Write-Host "Started ngrok tunnel: https://$domain -> http://localhost:8001" -ForegroundColor Green
                }
            }
        } else {
            Write-Host 'Services started and migrations completed.' -ForegroundColor Green
        }
        Write-Host 'Tests: .\.venv\Scripts\python.exe -m pytest'
        Write-Host 'Gateway: .\.venv\Scripts\python.exe scripts/gateway_sim.py --url http://localhost:8001 --interval 5'
    } finally {
        Pop-Location
    }
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
