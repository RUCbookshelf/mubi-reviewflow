@echo off
setlocal
set "APPDIR=%~dp0"
if not exist "%APPDIR%runtime\pythonw.exe" goto missing_runtime
if not exist "%APPDIR%runtime\python.exe" goto missing_runtime
if not exist "%APPDIR%windows_start.py" goto missing_start
start "" "%APPDIR%runtime\pythonw.exe" "%APPDIR%windows_start.py" --repair
set "REPAIR_RC=%ERRORLEVEL%"
if not "%REPAIR_RC%"=="0" goto launch_failed
exit /b 0

:missing_runtime
call :explain "ReviewFlow bundled Python is missing. Reinstall ReviewFlow, then try repair again."
exit /b 1

:missing_start
call :explain "ReviewFlow repair script windows_start.py is missing. Reinstall ReviewFlow, then try repair again."
exit /b 1

:launch_failed
call :explain "ReviewFlow repair could not start (exit code %REPAIR_RC%). Reinstall ReviewFlow, then try repair again."
exit /b %REPAIR_RC%

rem Show one message box. When PowerShell is unavailable the echo fallback is the
rem ONLY channel left, and a double-clicked script closes its console at once, so
rem the fallback waits for a key before returning.
:explain
powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('%~1','ReviewFlow Repair',[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null"
if errorlevel 1 goto explain_text
exit /b 0

:explain_text
echo %~1
echo.
pause
exit /b 0
