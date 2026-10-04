@echo off
rem ============================================================
rem  MuBi ReviewFlow - Windows installer build (Windows 10/11 x64)
rem
rem  Usage: double-click this file.
rem  Output: build\Output\ReviewFlow-Setup-x64.exe
rem
rem  Requires:
rem    1) Internet access (embedded Python 3.12 + get-pip are downloaded)
rem    2) Inno Setup 6/7 or NSIS 3
rem
rem  Note: cmd.exe cannot use a network/UNC path (for example
rem  \\wsl.localhost\...) as its current directory. When this script is
rem  started from such a path it copies the sources to
rem  %LOCALAPPDATA%\ReviewFlow-build first, builds there, and copies the
rem  finished installer back into the Output folder next to this script.
rem
rem  Log: build_windows.log (next to this script; %TEMP% as fallback).
rem  The window stays open on failure so the reason can be read.
rem ============================================================
chcp 65001 >nul
setlocal
title MuBi ReviewFlow - Windows build

set "SRCDIR=%~dp0"
if "%SRCDIR:~-1%"=="\" set "SRCDIR=%SRCDIR:~0,-1%"

set "LOG=%SRCDIR%\build_windows.log"
(>"%LOG%" echo MuBi ReviewFlow Windows build log) 2>nul
if exist "%LOG%" set "LOGOK=1"
if not defined LOGOK set "LOG=%TEMP%\ReviewFlow-build.log"

set "PYEMBED_VER=3.12.10"
set "PYEMBED_ZIP=python-%PYEMBED_VER%-embed-amd64.zip"
set "PYEMBED_URL=https://www.python.org/ftp/python/%PYEMBED_VER%/%PYEMBED_ZIP%"
set "GETPIP_URL=https://bootstrap.pypa.io/get-pip.py"
set "DOWNLOADS=%SRCDIR%\downloads"
set "STAGING=%SRCDIR%\staging\ReviewFlow"
set "INSTALLER=%SRCDIR%\Output\ReviewFlow-Setup-x64.exe"

echo ============================================================
echo   MuBi ReviewFlow - Windows installer build
echo ============================================================
call :log "===== ReviewFlow 2.0 build started ====="
call :log "author: Kang Tairong (bookshelf@ruc.edu.cn)"
call :log "script dir: %SRCDIR%"
call :log "log file  : %LOG%"

if not "%~1"=="__inner__" goto :detect_unc
rem --- running inside the relocated local copy: build in place ---
set "UNC=0"
goto :build

rem ============================================================
rem  Step 0: a UNC path cannot be the current directory, so relocate
rem ============================================================
:detect_unc
set "UNC=0"
if "%SRCDIR:~0,2%"=="\\" set "UNC=1"
if "%UNC%"=="0" goto :build

echo   Network/WSL path detected - building from a local copy.
set "BUILDROOT=%LOCALAPPDATA%\ReviewFlow-build"
call :log "relocating: %BUILDROOT%"
mkdir "%BUILDROOT%\build" 2>nul
if not exist "%BUILDROOT%\build" goto :fail_nolocal
if exist "%BUILDROOT%\build\staging" rmdir /s /q "%BUILDROOT%\build\staging" 2>nul
if exist "%BUILDROOT%\build\Output" rmdir /s /q "%BUILDROOT%\build\Output" 2>nul

echo   Copying sources to %BUILDROOT% ...
call :log "copying build folder ..."
robocopy "%SRCDIR%" "%BUILDROOT%\build" /E /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache staging appdir Output downloads /XF *.AppImage *.tar.gz *.log >>"%LOG%" 2>&1
if errorlevel 8 goto :fail_copy
set "SRCPARENT=%SRCDIR%\.."
set "COPYFAIL="
for %%P in (custom_backend custom_frontend coscreen) do call :copydir "%%P"
if defined COPYFAIL goto :fail_copy
copy /y "%SRCPARENT%\THIRD_PARTY_NOTICES.md" "%BUILDROOT%\THIRD_PARTY_NOTICES.md" >nul
if errorlevel 1 goto :fail_copy

echo   Building inside the local copy ...
call :log "calling local build: %BUILDROOT%\build\build_windows.bat"
call "%BUILDROOT%\build\build_windows.bat" __inner__
if errorlevel 1 goto :fail_inner
if not exist "%BUILDROOT%\build\Output\ReviewFlow-Setup-x64.exe" goto :fail_noexe

echo   Copying the installer back ...
call :log "copying installer back into %SRCDIR%\Output"
mkdir "%SRCDIR%\Output" 2>nul
copy /y "%BUILDROOT%\build\Output\ReviewFlow-Setup-x64.exe" "%INSTALLER%" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_copyback
if exist "%BUILDROOT%\build\build_windows.log" copy /y "%BUILDROOT%\build\build_windows.log" "%SRCDIR%\build_windows_local.log" >nul 2>&1
if not exist "%INSTALLER%" goto :fail_copyback

echo.
echo ============================================================
echo   BUILD OK
echo   installer : %INSTALLER%
echo   build log : %SRCDIR%\build_windows_local.log
echo ============================================================
echo.
echo   Double-click the installer above to install ReviewFlow.
echo.
pause
exit /b 0

rem ============================================================
rem  Real build (always runs from a local directory)
rem ============================================================
:build
cd /d "%SRCDIR%"
if not exist "installer.nsi" if not exist "installer.iss" goto :fail_cd
if not exist "launcher.py" goto :fail_cd
call :log "build dir: %CD%"

call :log "[0/6] prerequisites"
set "MISSING="
for %%P in (custom_backend custom_frontend coscreen) do call :checksrc "%%P"
if defined MISSING goto :fail_src

where curl.exe >nul 2>nul
if errorlevel 1 goto :fail_nocurl
where powershell.exe >nul 2>nul
if errorlevel 1 goto :fail_nops

set "PF86=%ProgramFiles(x86)%"
set "PF64=%ProgramFiles%"
set "ISCC="
for %%P in ("%PF86%\Inno Setup 6\ISCC.exe" "%PF64%\Inno Setup 6\ISCC.exe" "%PF86%\Inno Setup 7\ISCC.exe" "%PF64%\Inno Setup 7\ISCC.exe" "%PF86%\Inno Setup\ISCC.exe" "%PF64%\Inno Setup\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe" "%LOCALAPPDATA%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Inno Setup 7\ISCC.exe") do if not defined ISCC if exist "%%~P" set "ISCC=%%~P"
if defined ISCC set "INSTALLER_ENGINE=Inno Setup"
if defined ISCC goto :compiler_ready
where ISCC.exe >nul 2>nul
if not errorlevel 1 set "ISCC=ISCC.exe"
if defined ISCC set "INSTALLER_ENGINE=Inno Setup"
if defined ISCC goto :compiler_ready
set "MAKENSIS="
if exist "%PF86%\NSIS\makensis.exe" set "MAKENSIS=%PF86%\NSIS\makensis.exe"
if not defined MAKENSIS if exist "%PF64%\NSIS\makensis.exe" set "MAKENSIS=%PF64%\NSIS\makensis.exe"
if not defined MAKENSIS where makensis.exe >nul 2>nul
if not defined MAKENSIS if not errorlevel 1 set "MAKENSIS=makensis.exe"
if not defined MAKENSIS goto :fail_nocompiler
set "INSTALLER_ENGINE=NSIS"
:compiler_ready
if /I "%INSTALLER_ENGINE%"=="Inno Setup" if not exist "installer.iss" goto :fail_cd
if /I "%INSTALLER_ENGINE%"=="NSIS" if not exist "installer.nsi" goto :fail_cd
call :log "installer compiler: %INSTALLER_ENGINE%"

call :log "[1/6] downloading embedded Python %PYEMBED_VER%"
echo [1/6] downloading embedded Python %PYEMBED_VER% ...
if exist "%DOWNLOADS%\%PYEMBED_ZIP%" goto :zip_ready
if not exist "%DOWNLOADS%" mkdir "%DOWNLOADS%" 2>nul
curl -L --fail --retry 2 -o "%DOWNLOADS%\%PYEMBED_ZIP%" "%PYEMBED_URL%" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_dlpy
:zip_ready
if exist "%DOWNLOADS%\get-pip.py" goto :pip_ready
curl -L --fail --retry 2 -o "%DOWNLOADS%\get-pip.py" "%GETPIP_URL%" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_dlgetpip
:pip_ready

call :log "[2/6] unpacking runtime and installing pip"
echo [2/6] unpacking runtime and installing pip ...
if exist "%SRCDIR%\staging" rmdir /s /q "%SRCDIR%\staging" 2>nul
mkdir "%STAGING%\runtime" 2>nul
if not exist "%STAGING%\runtime" goto :fail_staging
powershell -NoProfile -Command "Expand-Archive -Force '%DOWNLOADS%\%PYEMBED_ZIP%' '%STAGING%\runtime'" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_expand
rem the embedded distribution disables site-packages by default
>"%STAGING%\runtime\python312._pth" echo python312.zip
>>"%STAGING%\runtime\python312._pth" echo .
>>"%STAGING%\runtime\python312._pth" echo ..
>>"%STAGING%\runtime\python312._pth" echo Lib\site-packages
>>"%STAGING%\runtime\python312._pth" echo import site
if not exist "%STAGING%\runtime\python.exe" goto :fail_expand
"%STAGING%\runtime\python.exe" "%DOWNLOADS%\get-pip.py" pip setuptools wheel --no-warn-script-location >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_getpip

call :log "[3/6] installing runtime dependencies"
echo [3/6] installing runtime dependencies (a few minutes, please wait) ...
"%STAGING%\runtime\python.exe" -m pip install --no-build-isolation --prefer-binary --no-warn-script-location --disable-pip-version-check -r "%SRCDIR%\requirements-app.txt" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_pip
"%STAGING%\runtime\python.exe" -c "import fastapi, uvicorn, jwt, bcrypt, multipart, pandas, rispy, rapidfuzz, pypdf; print('dependency check OK')" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_depcheck

call :log "[4/6] copying application files"
echo [4/6] copying application files ...
set "COPYFAIL="
for %%P in (custom_backend custom_frontend coscreen) do call :copyapp "%%P"
if defined COPYFAIL goto :fail_copy
copy /y "%SRCDIR%\launcher.py" "%STAGING%\launcher.py" >nul
copy /y "%SRCDIR%\requirements-app.txt" "%STAGING%\requirements-app.txt" >nul
copy /y "%SRCDIR%\..\THIRD_PARTY_NOTICES.md" "%STAGING%\THIRD_PARTY_NOTICES.md" >nul
copy /y "%SRCDIR%\windows_start.py" "%STAGING%\windows_start.py" >nul
copy /y "%SRCDIR%\repair_reviewflow.bat" "%STAGING%\repair_reviewflow.bat" >nul
copy /y "%SRCDIR%\windows_smoke.py" "%STAGING%\windows_smoke.py" >nul
copy /y "%SRCDIR%\icon.ico" "%STAGING%\icon.ico" >nul
copy /y "%SRCDIR%\icon_256.png" "%STAGING%\icon_256.png" >nul
if not exist "%STAGING%\launcher.py" goto :fail_copy
if not exist "%STAGING%\windows_start.py" goto :fail_copy
if not exist "%STAGING%\repair_reviewflow.bat" goto :fail_copy

rem Embedded Python ignores the temporary paths used by pip build isolation.
rem setuptools and wheel are installed above; build pure-Python svglib in place.
rem Keep the exact release dependencies and offline repair wheels.
"%STAGING%\runtime\python.exe" -m pip check >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_depcheck
"%STAGING%\runtime\python.exe" -m pip freeze >"%STAGING%\runtime-lock.txt" 2>>"%LOG%"
if errorlevel 1 goto :fail_depcheck
"%STAGING%\runtime\python.exe" -m pip wheel --no-build-isolation --prefer-binary -r "%STAGING%\runtime-lock.txt" -w "%STAGING%\wheelhouse" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_pip
rem Keep corresponding source for LGPL SVG components alongside their wheels.
mkdir "%STAGING%\third_party_sources" 2>nul
findstr /I /B /C:"CairoSVG==" /C:"svglib==" "%STAGING%\runtime-lock.txt" >"%STAGING%\third_party_sources\source-requirements.txt"
"%STAGING%\runtime\python.exe" -m pip download --no-build-isolation --no-deps --no-binary=:all: -r "%STAGING%\third_party_sources\source-requirements.txt" -d "%STAGING%\third_party_sources" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_pip
"%STAGING%\runtime\python.exe" "%STAGING%\windows_start.py" --check >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_depcheck

call :log "[5/6] smoke test"
echo [5/6] smoke test ...
pushd "%STAGING%"
"%STAGING%\runtime\python.exe" windows_smoke.py >"%SRCDIR%\build_windows_smoke.log" 2>&1
set "SMOKE=%ERRORLEVEL%"
popd
type "%SRCDIR%\build_windows_smoke.log"
if not "%SMOKE%"=="0" goto :fail_smoke

call :log "[6/6] compiling the installer with %INSTALLER_ENGINE%"
echo [6/6] compiling the installer (%INSTALLER_ENGINE%) ...
if exist "%INSTALLER%" del /q "%INSTALLER%"
if not exist "%SRCDIR%\Output" mkdir "%SRCDIR%\Output"
if /I "%INSTALLER_ENGINE%"=="Inno Setup" goto :compile_inno
"%MAKENSIS%" /DAPP_VERSION=2.0 "installer.nsi" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_compiler
goto :compile_done
:compile_inno
"%ISCC%" /DAppVersion=2.0 "installer.iss" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_compiler
:compile_done
if not exist "%INSTALLER%" goto :fail_noexe

:done
echo.
echo ============================================================
echo   BUILD OK
echo   installer: %INSTALLER%
echo   log      : %LOG%
echo ============================================================
if "%~1"=="__inner__" exit /b 0
echo.
echo   Double-click the installer above to install ReviewFlow.
echo.
pause
exit /b 0

rem ============================================================
rem  Failure exits: always report a reason and keep the window open
rem ============================================================
:fail_nolocal
call :err "cannot create the local build folder: %BUILDROOT%"
goto :end_fail
:fail_copy
call :err "copying the sources failed (robocopy reported an error). Source: %SRCDIR%"
goto :end_fail
:fail_inner
if exist "%BUILDROOT%\build\build_windows.log" copy /y "%BUILDROOT%\build\build_windows.log" "%SRCDIR%\build_windows_local.log" >nul 2>&1
call :err "the build inside the local copy failed - see %SRCDIR%\build_windows_local.log"
goto :end_fail
:fail_noexe
call :err "the installer file was not produced: %INSTALLER%"
goto :end_fail
:fail_copyback
call :err "could not copy the installer back into %SRCDIR%\Output"
goto :end_fail
:fail_cd
call :err "cannot enter the build folder or it is incomplete: %SRCDIR%"
goto :end_fail
:fail_src
call :err "source folders are missing next to %SRCDIR%"
goto :end_fail
:fail_nocurl
call :err "curl.exe not found (needed to download the Python runtime)"
goto :end_fail
:fail_nops
call :err "powershell.exe not found (needed to unpack the Python runtime)"
goto :end_fail
:fail_nocompiler
call :err "No installer compiler found. Install Inno Setup 6/7 or NSIS 3, or add ISCC.exe/makensis.exe to PATH."
goto :end_fail
:fail_dlpy
call :err "downloading the embedded Python failed - check the network or proxy"
goto :end_fail
:fail_dlgetpip
call :err "downloading get-pip.py failed - check the network or proxy"
goto :end_fail
:fail_staging
call :err "cannot create the staging folder: %STAGING%"
goto :end_fail
:fail_expand
call :err "unpacking the embedded Python failed"
goto :end_fail
:fail_getpip
call :err "installing pip into the bundled runtime failed"
goto :end_fail
:fail_pip
call :err "dependency installation or download failed - see the detailed output above in the log"
goto :end_fail
:fail_depcheck
call :err "the bundled runtime cannot import the required packages"
goto :end_fail
:fail_smoke
call :err "smoke test failed - see %SRCDIR%\build_windows_smoke.log"
type "%SRCDIR%\build_windows_smoke.log" >>"%LOG%"
goto :end_fail
:fail_compiler
call :err "installer compilation failed: %INSTALLER_ENGINE%"
goto :end_fail

:end_fail
rem Show the captured tool output as well as the failed build step.
if exist "%LOG%" type "%LOG%"
echo.
echo ============================================================
echo   BUILD FAILED
echo   log: %LOG%
echo   Please send this log file back for diagnosis.
echo ============================================================
echo.
if defined GITHUB_ACTIONS exit /b 1
pause
exit /b 1

rem ============================================================
rem  Subroutines
rem ============================================================
:checksrc
rem %~1 = source folder name expected in the parent of the build folder
if exist "%SRCDIR%\..\%~1" goto :eof
call :err "missing source folder: %~1"
set "MISSING=1"
goto :eof

:copydir
rem %~1 = folder name, copied into the relocated build root
if not exist "%SRCPARENT%\%~1" goto :copydir_missing
robocopy "%SRCPARENT%\%~1" "%BUILDROOT%\%~1" /E /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache /XF SPEC.md *Zone.Identifier >>"%LOG%" 2>&1
if errorlevel 8 set "COPYFAIL=1"
goto :eof
:copydir_missing
call :log "note: %~1 not found in the source tree (skipped)"
goto :eof

:copyapp
rem %~1 = folder name, copied into the staging tree
if not exist "%SRCDIR%\..\%~1" goto :copyapp_missing
robocopy "%SRCDIR%\..\%~1" "%STAGING%\%~1" /E /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache /XF SPEC.md *Zone.Identifier >>"%LOG%" 2>&1
if errorlevel 8 set "COPYFAIL=1"
goto :eof
:copyapp_missing
call :log "note: %~1 not found in the source tree (skipped)"
goto :eof

:log
echo [%TIME%] %~1
if not defined LOGOK goto :eof
>>"%LOG%" echo [%TIME%] %~1
goto :eof

:err
echo.
echo [ERROR] %~1
if not defined LOGOK goto :eof
>>"%LOG%" echo [ERROR] %~1
goto :eof
