@echo off
setlocal
set "APPDIR=%~dp0"
if not exist "%APPDIR%runtime\pythonw.exe" goto missing_runtime
if not exist "%APPDIR%runtime\python.exe" goto missing_runtime
start "" "%APPDIR%runtime\pythonw.exe" "%APPDIR%windows_start.py" --repair
exit /b 0

:missing_runtime
powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('ReviewFlow bundled Python is missing. Reinstall ReviewFlow, then try repair again.','ReviewFlow Repair',[System.Windows.Forms.MessageBoxButtons]::OK,[System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null"
if errorlevel 1 echo ReviewFlow bundled Python is missing. Reinstall ReviewFlow, then try repair again.
exit /b 1
