#Requires -Version 5.1
<#
.SYNOPSIS
    Creates a "HCode v2" shortcut on the Windows Desktop.

.DESCRIPTION
    Run this script ONCE after cloning the repo. It places a desktop shortcut
    named "HCode v2" that launches the full stack with a double-click.

    The shortcut points to scripts\launch.bat and uses the project icon from
    desktop-app\assets\hcode-icon.ico (if present).

.EXAMPLE
    # From the repo root:
    powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir

$LaunchBat  = Join-Path $RepoRoot "scripts\launch.bat"
$IconPath   = Join-Path $RepoRoot "desktop-app\assets\hcode-icon.ico"
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopDir "HCode v2.lnk"

# ── Validate launch.bat exists ────────────────────────────────────────────────
if (-not (Test-Path $LaunchBat)) {
    Write-Host "ERROR: launch.bat not found at: $LaunchBat" -ForegroundColor Red
    Write-Host "       Run this script from within the hcode-v2 repo." -ForegroundColor Red
    exit 1
}

# ── Create shortcut ───────────────────────────────────────────────────────────
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)

$Shortcut.TargetPath       = $LaunchBat
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description      = "HCode v2 — AI coding agent (one-click launcher)"
$Shortcut.WindowStyle      = 1   # Normal window

if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
    Write-Host "  Icon: $IconPath" -ForegroundColor DarkGray
} else {
    Write-Host "  NOTE: Icon file not found at desktop-app\assets\hcode-icon.ico" -ForegroundColor Yellow
    Write-Host "        Drop hcode-icon.ico there and re-run to add the icon." -ForegroundColor Yellow
}

$Shortcut.Save()

Write-Host ""
Write-Host "Shortcut created: $ShortcutPath" -ForegroundColor Green
Write-Host ""
Write-Host "Double-click 'HCode v2' on your Desktop to launch the app." -ForegroundColor Cyan
Write-Host ""
