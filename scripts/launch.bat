@echo off
REM HCode v2 one-click launcher. Double-click this file.
REM Calls launch.ps1 (ExecutionPolicy Bypass) and pauses on error so a
REM double-click failure shows the message instead of flashing and closing.
powershell -NoLogo -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
if errorlevel 1 (
    echo.
    echo Launcher exited with an error ^(code %errorlevel%^). See the messages above.
    pause
)
