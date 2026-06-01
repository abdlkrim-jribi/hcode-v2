#Requires -Version 5.1
<#
.SYNOPSIS
    HCode v2 one-click desktop launcher for Windows.

.DESCRIPTION
    Auto-detects mock vs live mode by inspecting .env:
      - If .env exists AND HCODE_MODEL_API_KEY is set to a real value → LIVE mode
      - Otherwise → MOCK mode (offline demo, no API key needed)

    Starts the full stack via scripts/dev.py, waits for the local server,
    then opens the browser. Keep this window open to see logs; Ctrl+C to stop.

.EXAMPLE
    # Run directly:
    powershell -ExecutionPolicy Bypass -File scripts\launch.ps1

    # Or double-click launch.bat (which calls this script).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Locate repo root (parent of this script's directory) ─────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

Set-Location $RepoRoot

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║         HCode v2 — Desktop Launcher      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Detect live vs mock ───────────────────────────────────────────────────────
$EnvFile   = Join-Path $RepoRoot ".env"
$UseMock   = $true
$MockReason = "No .env found"

if (Test-Path $EnvFile) {
    $EnvContent = Get-Content $EnvFile -Raw

    # Extract HCODE_MODEL_API_KEY value (ignore commented lines)
    $ApiKeyMatch = [regex]::Match($EnvContent, '(?m)^\s*HCODE_MODEL_API_KEY\s*=\s*(.+)$')

    if ($ApiKeyMatch.Success) {
        $ApiKeyValue = $ApiKeyMatch.Groups[1].Value.Trim()
        # Treat placeholder values as "not configured"
        $Placeholders = @('replace-me', 'your-key', 'sk-...', '', 'CHANGE_ME', 'YOUR_API_KEY')
        if ($ApiKeyValue -and $Placeholders -notcontains $ApiKeyValue) {
            $UseMock   = $false
        } else {
            $MockReason = "HCODE_MODEL_API_KEY is a placeholder ('$ApiKeyValue')"
        }
    } else {
        # Fall back to OPENAI_API_KEY
        $OAIMatch = [regex]::Match($EnvContent, '(?m)^\s*OPENAI_API_KEY\s*=\s*(.+)$')
        if ($OAIMatch.Success) {
            $OAIValue = $OAIMatch.Groups[1].Value.Trim()
            $Placeholders = @('replace-me', 'your-key', 'sk-...', '', 'CHANGE_ME', 'YOUR_API_KEY')
            if ($OAIValue -and $Placeholders -notcontains $OAIValue) {
                $UseMock   = $false
            } else {
                $MockReason = "OPENAI_API_KEY is a placeholder"
            }
        } else {
            $MockReason = "No API key found in .env"
        }
    }
}

# ── Print mode ────────────────────────────────────────────────────────────────
if ($UseMock) {
    Write-Host "┌─────────────────────────────────────────────────────────────┐" -ForegroundColor Yellow
    Write-Host "│  MOCK MODE — offline demo, no API key needed                │" -ForegroundColor Yellow
    Write-Host "│                                                             │" -ForegroundColor Yellow
    Write-Host "│  Running in MOCK mode.                                      │" -ForegroundColor Yellow
    Write-Host "│  Add your API key to .env and relaunch to go live.          │" -ForegroundColor Yellow
    Write-Host "│                                                             │" -ForegroundColor Yellow
    Write-Host "│  Reason: $($MockReason.PadRight(51))│" -ForegroundColor Yellow
    Write-Host "└─────────────────────────────────────────────────────────────┘" -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "  Mode: " -NoNewline
    Write-Host "LIVE" -ForegroundColor Green -NoNewline
    Write-Host " (real daemon + real model)"
    Write-Host ""
}

# ── Check Python / uv ────────────────────────────────────────────────────────
$UvCmd = Get-Command "uv" -ErrorAction SilentlyContinue
$PyCmd = Get-Command "python" -ErrorAction SilentlyContinue

if (-not $UvCmd -and -not $PyCmd) {
    Write-Host "ERROR: Neither 'uv' nor 'python' found in PATH." -ForegroundColor Red
    Write-Host "       Install Python from https://python.org or uv from https://docs.astral.sh/uv/" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Prefer uv-managed python; fall back to plain python
$PythonExe = if ($UvCmd) { "uv" } else { "python" }

# ── Build argument list ───────────────────────────────────────────────────────
$DevScript = Join-Path $RepoRoot "scripts\dev.py"

if ($UvCmd) {
    $LaunchArgs = @("run", "python", $DevScript)
} else {
    $LaunchArgs = @($DevScript)
}

if ($UseMock) {
    $LaunchArgs += "--mock"
}

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Host "  Launching: $PythonExe $($LaunchArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  UI will open at http://localhost:1420 once ready." -ForegroundColor Cyan
Write-Host "  Press Ctrl+C in this window to stop all processes." -ForegroundColor DarkGray
Write-Host ""

try {
    & $PythonExe @LaunchArgs
} catch {
    Write-Host ""
    Write-Host "ERROR: Failed to start dev.py: $_" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Keep window open after normal exit so the user can read any final log lines.
Write-Host ""
Write-Host "All processes stopped." -ForegroundColor DarkGray
Read-Host "Press Enter to close this window"
