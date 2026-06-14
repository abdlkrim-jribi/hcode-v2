#Requires -Version 5.1
<#
.SYNOPSIS
    HCode v2 one-click desktop launcher for Windows (PowerShell 5.1+).

.DESCRIPTION
    Decides MOCK vs LIVE mode from .env, then starts the full stack via
    scripts/dev.py and opens the browser. Mode decision:
      1. HCODE_MOCK = 1/true/yes/on   -> MOCK (keyless offline demo)
      2. HCODE_MOCK = 0/false/no/off  -> LIVE if a real API key is set, else MOCK
      3. HCODE_MOCK unset             -> LIVE if a real API key is set, else MOCK
    A key counts as "set" only when it is non-empty, not whitespace, does not
    start with '#' (a commented/placeholder line), and is not a known placeholder.

    ASCII-ONLY ON PURPOSE: PowerShell 5.1 reads a BOM-less script as ANSI, so a
    non-ASCII character (em dash, box-drawing, arrow) corrupts parsing and the
    launcher dies with a TerminatorExpectedAtEndOfString error. Do not add
    non-ASCII characters to this file.

.PARAMETER DryRun
    Print the resolved mode and exit WITHOUT launching anything.

.PARAMETER EnvPath
    Path to the .env file to read. Defaults to <repo root>\.env.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\launch.ps1

.EXAMPLE
    # Double-click scripts\launch.bat (which calls this script).
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$EnvPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Helpers ------------------------------------------------------------------

function Get-EnvValue {
    # Return the trimmed value of NAME from .env content, or $null if absent.
    param([string]$Content, [string]$Name)
    $pattern = '(?m)^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)$'
    $m = [regex]::Match($Content, $pattern)
    if (-not $m.Success) { return $null }
    return $m.Groups[1].Value.Trim()
}

function Test-RealKey {
    # A value is a real key only if non-empty, not whitespace, not a '#'-comment,
    # and not a known placeholder.
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value.StartsWith('#'))               { return $false }
    $placeholders = @(
        'replace-me', 'your-key', 'sk-...', 'CHANGE_ME', 'YOUR_API_KEY',
        'your-gpt-oss-key', 'paste-your-key-here'
    )
    if ($placeholders -contains $Value) { return $false }
    return $true
}

# -- Locate repo root (parent of this script's directory) ---------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

if (-not $EnvPath) { $EnvPath = Join-Path $RepoRoot ".env" }

# -- Banner -------------------------------------------------------------------

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "        HCode v2 - Desktop Launcher" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- Decide MOCK vs LIVE ------------------------------------------------------

$UseMock    = $true
$MockReason = "No .env found"

if (Test-Path $EnvPath) {
    $EnvContent = Get-Content $EnvPath -Raw
    if ($null -eq $EnvContent) { $EnvContent = "" }

    $MockFlag = Get-EnvValue $EnvContent 'HCODE_MOCK'
    $ApiKey   = Get-EnvValue $EnvContent 'HCODE_MODEL_API_KEY'
    if (-not (Test-RealKey $ApiKey)) { $ApiKey = Get-EnvValue $EnvContent 'OPENAI_API_KEY' }
    $HasKey   = Test-RealKey $ApiKey

    $MockTrue  = @('1', 'true', 'yes', 'on')
    $MockFalse = @('0', 'false', 'no', 'off')
    $FlagLower = if ($MockFlag) { $MockFlag.ToLower() } else { '' }

    if ($MockTrue -contains $FlagLower) {
        # Explicit mock wins, even if a key is present.
        $UseMock = $true
        $MockReason = "HCODE_MOCK=$MockFlag"
    } elseif ($MockFalse -contains $FlagLower) {
        # Live requested: only go live if a real key backs it.
        if ($HasKey) {
            $UseMock = $false
        } else {
            $UseMock = $true
            $MockReason = "HCODE_MOCK=$MockFlag but no API key is set"
        }
    } else {
        # HCODE_MOCK unset/blank: infer from the key.
        if ($HasKey) {
            $UseMock = $false
        } else {
            $UseMock = $true
            $MockReason = "No API key set in .env"
        }
    }
}

# -- Print mode ---------------------------------------------------------------

if ($UseMock) {
    Write-Host "+-------------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host "|  MOCK MODE - offline demo, no API key needed                |" -ForegroundColor Yellow
    Write-Host "|  Set HCODE_MOCK=0 and a real HCODE_MODEL_API_KEY in .env,    |" -ForegroundColor Yellow
    Write-Host "|  then relaunch to go LIVE.                                   |" -ForegroundColor Yellow
    Write-Host "+-------------------------------------------------------------+" -ForegroundColor Yellow
    Write-Host "  Reason: $MockReason" -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "  Mode: " -NoNewline
    Write-Host "LIVE" -ForegroundColor Green -NoNewline
    Write-Host " (real daemon + real model)"
    Write-Host ""
}

# -- Dry run: report the decision and exit without launching -------------------

if ($DryRun) {
    $ModeName = if ($UseMock) { 'MOCK' } else { 'LIVE' }
    Write-Host "DRYRUN: mode=$ModeName reason=$MockReason"
    exit 0
}

# -- Check Python / uv --------------------------------------------------------

$UvCmd = Get-Command "uv" -ErrorAction SilentlyContinue
$PyCmd = Get-Command "python" -ErrorAction SilentlyContinue

if (-not $UvCmd -and -not $PyCmd) {
    Write-Host "ERROR: Neither 'uv' nor 'python' found in PATH." -ForegroundColor Red
    Write-Host "       Install Python from https://python.org or uv from https://docs.astral.sh/uv/" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Prefer uv-managed python; fall back to plain python.
$PythonExe = if ($UvCmd) { "uv" } else { "python" }

# -- Build argument list ------------------------------------------------------

$DevScript = Join-Path $RepoRoot "scripts\dev.py"

if ($UvCmd) {
    $LaunchArgs = @("run", "python", $DevScript)
} else {
    $LaunchArgs = @($DevScript)
}

if ($UseMock) {
    $LaunchArgs += "--mock"
}

# -- Launch -------------------------------------------------------------------

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

# Keep the window open after a normal exit so final log lines stay visible.
Write-Host ""
Write-Host "All processes stopped." -ForegroundColor DarkGray
Read-Host "Press Enter to close this window"
