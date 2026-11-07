@echo off
rem ============================================================
rem  MuBi ReviewFlow - Windows installer build (Windows 10/11 x64)
rem
rem  Usage: double-click this file.
rem  Output: build\Output\ReviewFlow-Setup-x64.exe
rem
rem  Requires:
rem    1) Internet access (embedded Python 3.12 + the pinned pip wheels are
rem       downloaded)
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
setlocal EnableExtensions
title MuBi ReviewFlow - Windows build

set "SRCDIR=%~dp0"
if "%SRCDIR:~-1%"=="\" set "SRCDIR=%SRCDIR:~0,-1%"
rem F1-19: bind each external tool to the exact path its `where` lookup reports
rem (see :resolve_where). This runs before the lock because the UNC wrapper
rem takes the lock before the :build stage; a tool that is genuinely missing is
rem still diagnosed by the explicit checks in :build, so this pass is silent.
call :resolve_where curl.exe CURL_EXE
call :resolve_where powershell.exe POWERSHELL_EXE
call :resolve_where node.exe NODE_EXE
rem F1-34/F1-40: the version comes from the root VERSION file or the build
rem stops here. The old "0.0.0" fallback (and installer.iss/nsi's stale
rem "2.0.1") silently produced installers that claim a version nobody chose -
rem exactly what the N-01 UNC fix had to undo for the app itself.
if not exist "%SRCDIR%\..\VERSION" goto :fail_noversion
for /f "usebackq delims=" %%V in ("%SRCDIR%\..\VERSION") do set "APP_VERSION=%%V"
if not defined APP_VERSION goto :fail_noversion

set "LOG=%SRCDIR%\build_windows.log"
(>"%LOG%" echo MuBi ReviewFlow Windows build log) 2>nul
if exist "%LOG%" set "LOGOK=1"
if not defined LOGOK set "LOG=%TEMP%\ReviewFlow-build.log"

set "PYEMBED_VER=3.12.10"
set "PYEMBED_ZIP=python-%PYEMBED_VER%-embed-amd64.zip"
set "PYEMBED_URL=https://www.python.org/ftp/python/%PYEMBED_VER%/%PYEMBED_ZIP%"
rem r13 build-1: pip is bootstrapped from FIXED-VERSION wheels on
rem files.pythonhosted.org (a published release asset is immutable). The old
rem pin was the PyPA get-pip bootstrap endpoint, a ROLLING file that upstream
rem rewrites whenever pip releases: by 2026-11-07 the live file hashed
rem 356e0d0d... while GETPIP_SHA256 still held f4448a01..., so every machine
rem without the old cached copy hard-stopped at [1/6] and was told to "check
rem the network". A wheel is a zip, so the embedded runtime bootstraps pip from
rem it directly (see [2/6]) - no rolling file in the build path.
rem GETPIP_* keeps its name (it is still "the pip bootstrapper"); setuptools,
rem wheel and packaging replace the `get-pip.py pip setuptools wheel` install
rem that used to run (wheel needs packaging at run time).
set "PIP_WHEEL_VER=26.3"
set "GETPIP_FILE=pip-%PIP_WHEEL_VER%-py3-none-any.whl"
set "GETPIP_URL=https://files.pythonhosted.org/packages/07/25/ee8c38ae72d66b844ce8eb9711ba905dba21ca1a8a0c960057c41cd23337/pip-26.3-py3-none-any.whl"
set "SETUPTOOLS_FILE=setuptools-84.2.1-py3-none-any.whl"
set "SETUPTOOLS_URL=https://files.pythonhosted.org/packages/ef/55/6dc6d6bb953bdfc52519a0059592c52b6e3b8ad51cf8256dffceb55b0610/setuptools-84.2.1-py3-none-any.whl"
set "WHEEL_FILE=wheel-0.48.0-py3-none-any.whl"
set "WHEEL_URL=https://files.pythonhosted.org/packages/2e/29/69cfbb602cd91690c55d38ba9fe53e6a7e76a6fa647bf38f19c138d25449/wheel-0.48.0-py3-none-any.whl"
set "PACKAGING_FILE=packaging-26.3-py3-none-any.whl"
set "PACKAGING_URL=https://files.pythonhosted.org/packages/63/34/ba1c580383c9eada3711951fef0795c80b829a078d72188184bcab9dd527/packaging-26.3-py3-none-any.whl"
rem MM9-13: every bootstrap download is verified with certutil against these
rem pinned digests BEFORE anything executes it - including a file already
rem sitting in the downloads cache. A fresh download that arrives but does not
rem match is reported separately from a download that never arrived
rem (:fetch_verified returns 2 vs 1), so the message names the right fix.
rem Never delete the check: update a pin in the same change as the version,
rem after verifying the new digest.
set "PYEMBED_SHA256=4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
set "GETPIP_SHA256=f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13"
set "SETUPTOOLS_SHA256=ca140b45d5325a9478f880e351484f62fef15f336462e449ca0c03c873d589c2"
set "WHEEL_SHA256=3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab"
set "PACKAGING_SHA256=d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"
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
rem F1-3: hold the build lock for the whole relocation. The relocated build
rem below inherits the ownership (RF_LOCK_HANDED_DOWN) and does not release
rem it, because this wrapper still has to copy the installer back afterwards.
set "LOCKDIR=%BUILDROOT%\build\.build-windows.lock"
call :acquire_lock
if errorlevel 1 goto :end_fail
set "RF_BUILD_LOCK_HELD=1"
mkdir "%BUILDROOT%\build" 2>nul
if not exist "%BUILDROOT%\build" goto :fail_nolocal
if exist "%BUILDROOT%\build\staging" rmdir /s /q "%BUILDROOT%\build\staging" 2>nul
if exist "%BUILDROOT%\build\Output" rmdir /s /q "%BUILDROOT%\build\Output" 2>nul

echo   Copying sources to %BUILDROOT% ...
call :log "copying build folder ..."
rem F1-17: /PURGE removes destination files whose source disappeared. Plain /E
rem only adds and overwrites, so after a rename/delete upstream the relocated
rem tree kept the old file and :copyapp packed it into the installer. The
rem /XD and /XF exclusions are still honoured for deletion, so build outputs
rem (staging, Output, downloads) are never purged from under the build.
rem r13 build-0: .build-windows.lock MUST be excluded too. It is created in
rem the destination (%BUILDROOT%\build) before this line and never exists in
rem the source, so /PURGE used to delete it - and with it the pid file - taking
rem the F1-3 mutex with it for the rest of the wrapper and inner build (probe:
rem E:/rf214tmp/dwave/build/probe_a_robocopy_purge_lock.bat -> LOCK_DELETED /
rem PIDFILE_GONE). The exclusion also stops a stale lock left in the SOURCE
rem tree by an earlier local build from overwriting the live lock's pid.
robocopy "%SRCDIR%" "%BUILDROOT%\build" /E /PURGE /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache staging appdir Output downloads .build-windows.lock /XF *.AppImage *.tar.gz *.log >>"%LOG%" 2>&1
if errorlevel 8 goto :fail_copy
set "SRCPARENT=%SRCDIR%\.."
set "COPYFAIL="
for %%P in (custom_backend custom_frontend coscreen) do call :copydir "%%P"
if defined COPYFAIL goto :fail_copy
copy /y "%SRCPARENT%\THIRD_PARTY_NOTICES.md" "%BUILDROOT%\THIRD_PARTY_NOTICES.md" >nul
if errorlevel 1 goto :fail_copy
rem N-01: the relocated tree must also carry the root VERSION file. The inner
rem build copies it into the staging layout; without this line the UNC path
rem built installers whose installed app reported version 0.0.0
rem (coscreen/__init__.py and update_check.py fall back), while the installer
rem metadata only looked right because `call` inherits the outer APP_VERSION.
copy /y "%SRCPARENT%\VERSION" "%BUILDROOT%\VERSION" >nul
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
call :release_lock
exit /b 0

rem ============================================================
rem  Real build (always runs from a local directory)
rem ============================================================
:build
rem F1-3: lock the build directory unless the outer UNC wrapper already holds
rem it for us (RF_BUILD_LOCK_HELD). mkdir is atomic in cmd.exe, so a second
rem concurrent build stops here instead of deleting the first one's staging.
if not defined RF_BUILD_LOCK_HELD (
  set "LOCKDIR=%SRCDIR%\.build-windows.lock"
  call :acquire_lock
  if errorlevel 1 goto :end_fail
) else (
  rem Inherited lock: leave it for the wrapper to release after the copyback.
  set "RF_LOCK_HANDED_DOWN=1"
  set "RF_LOCK_OWNED=1"
)
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
rem F1-19: `where` proved an exe exists somewhere; bind each tool to THAT path.
rem The old code then called the bare name, which cmd resolves through
rem PATH+PATHEXT - a curl.bat/curl.cmd earlier in PATH ran instead of the
rem approved curl.exe and, being an inter-batch call without `call`, never gave
rem control back (the rest of the build silently did not run).
call :resolve_where curl.exe CURL_EXE
if not defined CURL_EXE goto :fail_nocurl
where powershell.exe >nul 2>nul
if errorlevel 1 goto :fail_nops
call :resolve_where powershell.exe POWERSHELL_EXE
if not defined POWERSHELL_EXE goto :fail_nops
set "PF86=%ProgramFiles(x86)%"
set "PF64=%ProgramFiles%"
set "ISCC="
for %%P in ("%PF86%\Inno Setup 6\ISCC.exe" "%PF64%\Inno Setup 6\ISCC.exe" "%PF86%\Inno Setup 7\ISCC.exe" "%PF64%\Inno Setup 7\ISCC.exe" "%PF86%\Inno Setup\ISCC.exe" "%PF64%\Inno Setup\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe" "%LOCALAPPDATA%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Inno Setup 7\ISCC.exe") do if not defined ISCC if exist "%%~P" set "ISCC=%%~P"
if defined ISCC set "INSTALLER_ENGINE=Inno Setup"
if defined ISCC goto :compiler_ready
where ISCC.exe >nul 2>nul
if not errorlevel 1 call :resolve_where ISCC.exe ISCC
if defined ISCC set "INSTALLER_ENGINE=Inno Setup"
if defined ISCC goto :compiler_ready
set "MAKENSIS="
if exist "%PF86%\NSIS\makensis.exe" set "MAKENSIS=%PF86%\NSIS\makensis.exe"
if not defined MAKENSIS if exist "%PF64%\NSIS\makensis.exe" set "MAKENSIS=%PF64%\NSIS\makensis.exe"
if not defined MAKENSIS where makensis.exe >nul 2>nul
if not defined MAKENSIS if not errorlevel 1 call :resolve_where makensis.exe MAKENSIS
if not defined MAKENSIS goto :fail_nocompiler
set "INSTALLER_ENGINE=NSIS"
:compiler_ready
if /I "%INSTALLER_ENGINE%"=="Inno Setup" if not exist "installer.iss" goto :fail_cd
if /I "%INSTALLER_ENGINE%"=="NSIS" if not exist "installer.nsi" goto :fail_cd
call :log "installer compiler: %INSTALLER_ENGINE%"

call :log "[1/6] downloading embedded Python %PYEMBED_VER%"
echo [1/6] downloading embedded Python %PYEMBED_VER% ...
if not exist "%DOWNLOADS%" mkdir "%DOWNLOADS%" 2>nul
rem MM9-13: every artifact is hash-checked BEFORE it runs - including files
rem already sitting in the downloads cache from an earlier run, so a tampered
rem or truncated cache can no longer be replayed silently. A cached file whose
rem digest mismatches is deleted and fetched once more; a second mismatch
rem aborts the build. :fetch_verified separates "the download failed" (1) from
rem "the bytes do not match the pin" (2) so the failure message can say which.
call :fetch_verified "%PYEMBED_URL%" "%DOWNLOADS%\%PYEMBED_ZIP%" "%PYEMBED_SHA256%"
if errorlevel 2 goto :fail_dlpy_sha
if errorlevel 1 goto :fail_dlpy
call :fetch_verified "%GETPIP_URL%" "%DOWNLOADS%\%GETPIP_FILE%" "%GETPIP_SHA256%"
if errorlevel 2 goto :fail_dlgetpip_sha
if errorlevel 1 goto :fail_dlgetpip
call :fetch_verified "%SETUPTOOLS_URL%" "%DOWNLOADS%\%SETUPTOOLS_FILE%" "%SETUPTOOLS_SHA256%"
if errorlevel 2 goto :fail_dlgetpip_sha
if errorlevel 1 goto :fail_dlgetpip
call :fetch_verified "%WHEEL_URL%" "%DOWNLOADS%\%WHEEL_FILE%" "%WHEEL_SHA256%"
if errorlevel 2 goto :fail_dlgetpip_sha
if errorlevel 1 goto :fail_dlgetpip
call :fetch_verified "%PACKAGING_URL%" "%DOWNLOADS%\%PACKAGING_FILE%" "%PACKAGING_SHA256%"
if errorlevel 2 goto :fail_dlgetpip_sha
if errorlevel 1 goto :fail_dlgetpip

call :log "[2/6] unpacking runtime and installing pip"
echo [2/6] unpacking runtime and installing pip ...
rem F1-24: a locked staging tree (antivirus scan, a leftover process, an open
rem Explorer window) used to make the silent `rmdir ... 2>nul` fail while the
rem `if not exist runtime` guard then read the SURVIVING old tree as present,
rem so Expand-Archive -Force stacked a new runtime on top of the old one and
rem the mixed tree shipped. The cleanup result is now checked and a tree that
rem could not be removed is a hard stop instead of a silent mix.
rem The check is a fresh process on purpose: cmd.exe's own `if exist` can still
rem answer "not there" for a directory that was only partially removed in the
rem same cmd.exe, while Test-Path reports what the next Expand-Archive will
rem find. The query goes through a temporary .ps1 so no path ever has to
rem survive cmd/PowerShell quote nesting (an inline escaped path silently
rem matched nothing - measured).
if not exist "%SRCDIR%\staging" goto :staging_clean
rmdir /s /q "%SRCDIR%\staging" 2>nul
call :path_exists "%SRCDIR%\staging" RF_STAGING_LEFT
if defined RF_STAGING_LEFT goto :fail_stageclean
:staging_clean
mkdir "%STAGING%\runtime" 2>nul
if not exist "%STAGING%\runtime" goto :fail_staging
rem F1-20: Expand-Archive's paths go through -LiteralPath, so a repository
rem folder whose name contains an apostrophe ("Kang's docs") is no longer cut
rem short by the single-quoted argument and misreported as "unpacking failed".
rem The destination is passed WITHOUT a trailing backslash: `...\runtime\"`
rem makes that backslash escape the closing quote, PowerShell then reads a
rem mangled path and reports the same misleading unpack error (measured).
"%POWERSHELL_EXE%" -NoProfile -NonInteractive -Command "Expand-Archive -Force -LiteralPath \"%DOWNLOADS%\%PYEMBED_ZIP%\" -DestinationPath \"%STAGING%\runtime\"" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_expand
rem the embedded distribution disables site-packages by default
>"%STAGING%\runtime\python312._pth" echo python312.zip
>>"%STAGING%\runtime\python312._pth" echo .
>>"%STAGING%\runtime\python312._pth" echo ..
>>"%STAGING%\runtime\python312._pth" echo Lib\site-packages
>>"%STAGING%\runtime\python312._pth" echo import site
if not exist "%STAGING%\runtime\python.exe" goto :fail_expand
rem r13 build-1: bootstrap pip from the VERIFIED wheel, no get-pip script. A
rem wheel is a zip, so `python <wheel>\pip ...` would run pip's CLI - but pip's
rem Windows guard (pip/_internal/utils/misc.py:
rem protect_pip_from_modification_on_windows) refuses to modify pip whenever
rem argv[0]'s basename is literally "pip", which is exactly what that path
rem produces (measured on pip 26.3). A tiny driver file keeps argv[0] an
rem ordinary script path and puts the wheel on sys.path instead. Nothing below
rem reaches the network: every wheel was fetched and digest-checked above and
rem --no-index forbids resolving anything else.
set "RF_PIPBOOT=%DOWNLOADS%\rf_pip_bootstrap.py"
> "%RF_PIPBOOT%" echo import sys
>>"%RF_PIPBOOT%" echo sys.path.insert(0, sys.argv[1])
>>"%RF_PIPBOOT%" echo from pip._internal.cli.main import main
>>"%RF_PIPBOOT%" echo raise SystemExit(main(sys.argv[2:]))
"%STAGING%\runtime\python.exe" "%RF_PIPBOOT%" "%DOWNLOADS%\%GETPIP_FILE%" install --no-index --no-warn-script-location --disable-pip-version-check "%DOWNLOADS%\%GETPIP_FILE%" "%DOWNLOADS%\%SETUPTOOLS_FILE%" "%DOWNLOADS%\%WHEEL_FILE%" "%DOWNLOADS%\%PACKAGING_FILE%" >>"%LOG%" 2>&1
set "PIPBOOT_RC=%ERRORLEVEL%"
del "%RF_PIPBOOT%" >nul 2>nul
if not "%PIPBOOT_RC%"=="0" goto :fail_getpip

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
rem F1-26: all nine entry points are copied AND verified. The old block ran
rem nine unchecked `copy /y ... >nul` lines and then verified only three of
rem them, so a missing/unreadable THIRD_PARTY_NOTICES.md (a licence obligation)
rem produced a "successful" build with no notices file: the installers package
rem the whole staging tree and never reference the name, so neither ISCC nor
rem makensis would notice either.
set "STAGECOPYFAIL="
for %%F in (launcher.py requirements-app.txt windows_start.py repair_reviewflow.bat windows_smoke.py icon.ico icon_256.png) do call :stagecopy "%SRCDIR%\%%F" "%STAGING%\%%F"
call :stagecopy "%SRCDIR%\..\VERSION" "%STAGING%\VERSION"
call :stagecopy "%SRCDIR%\..\THIRD_PARTY_NOTICES.md" "%STAGING%\THIRD_PARTY_NOTICES.md"
if defined STAGECOPYFAIL goto :fail_copy

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

rem ============================================================
rem  MM7-02: run the four static validators against the sources.
rem  httpx (used by the two Python validators) is installed into a
rem  throwaway --target dir so the shipped runtime lock stays clean;
rem  the embedded runtime ignores PYTHONPATH, hence the env var that
rem  the validators append to sys.path themselves.
rem ============================================================
call :log "running static validators"
echo Running static validators ...
"%STAGING%\runtime\python.exe" -m pip install --no-build-isolation --prefer-binary --no-warn-script-location --disable-pip-version-check --target "%SRCDIR%\validator_deps" httpx >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validators
set "REVIEWFLOW_VALIDATION_DEPENDENCIES=%SRCDIR%\validator_deps"
"%STAGING%\runtime\python.exe" "%SRCDIR%\validate_import_review.py" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validators
"%STAGING%\runtime\python.exe" "%SRCDIR%\validate_screening_rounds.py" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validators
rem MM7-02 (R1): the same four validators CI runs are a hard requirement of a
rem local build too - node.exe must be present. validate_team_prisma_static.js
rem degrades its git-diff augmentation by itself when the build tree is not a
rem git checkout, so it runs unconditionally here.
where node.exe >nul 2>nul
if errorlevel 1 goto :fail_no_node
call :resolve_where node.exe NODE_EXE
if not defined NODE_EXE goto :fail_no_node
"%NODE_EXE%" "%SRCDIR%\validate_import_review_static.cjs" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validators
"%NODE_EXE%" "%SRCDIR%\validate_team_prisma_static.js" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validators
rem MM9-07: verify the staged payload tree against the checked-out sources
rem with the same gate CI runs on the built artifact - previously every local
rem Windows build shipped without this check.
"%STAGING%\runtime\python.exe" "%SRCDIR%\verify_release_payload.py" windows >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_validators
set "REVIEWFLOW_VALIDATION_DEPENDENCIES="
if exist "%SRCDIR%\validator_deps" rmdir /s /q "%SRCDIR%\validator_deps" >nul 2>nul

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
rem F1-18: the previous installer used to be deleted BEFORE the compiler ran,
rem so a failed compile (disk full, an antivirus lock, a broken .iss) left the
rem build machine with no usable installer at all. The old artifact is moved
rem aside first and restored when this compile fails.
if exist "%SRCDIR%\Output" if not exist "%INSTALLER%.prev" if exist "%INSTALLER%" move /y "%INSTALLER%" "%INSTALLER%.prev" >nul 2>nul
if exist "%INSTALLER%" del /q "%INSTALLER%"
if not exist "%SRCDIR%\Output" mkdir "%SRCDIR%\Output"
if /I "%INSTALLER_ENGINE%"=="Inno Setup" goto :compile_inno
rem F1-38: installer.nsi is UTF-8. Without /INPUTCHARSET (and without a BOM)
rem makensis decodes it with the build machine's ANSI code page, which on a
rem CJK ACP aborts with "Bad text encoding" - or, on an ACP that happens to
rem accept the bytes, ships a mojibake wizard.
"%MAKENSIS%" /INPUTCHARSET UTF8 /DAPP_VERSION=%APP_VERSION% "installer.nsi" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_compiler
goto :compile_done
:compile_inno
"%ISCC%" /DAppVersion=%APP_VERSION% "installer.iss" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail_compiler
:compile_done
if not exist "%INSTALLER%" goto :fail_compiler
if exist "%INSTALLER%.prev" del /q "%INSTALLER%.prev" >nul 2>nul

:done
rem F1-3: release the build lock on the success path (the failure path
rem releases in :end_fail; only the owning process releases).
call :release_lock
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
:fail_noversion
call :err "VERSION file not found or empty next to the build folder: %SRCDIR%\..\VERSION"
goto :end_fail
:fail_stageclean
call :err "the previous staging tree could not be removed (a process or antivirus scan may hold it): %SRCDIR%\staging - close ReviewFlow and retry"
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
call :err "downloading the embedded Python runtime failed - check the network or proxy"
goto :end_fail
:fail_dlpy_sha
call :err "the embedded Python runtime does not match its pinned SHA-256 - the download was corrupted or tampered with. Delete %DOWNLOADS%\%PYEMBED_ZIP% and retry; update PYEMBED_SHA256 only together with a new, independently verified file"
goto :end_fail
:fail_dlgetpip
call :err "downloading the pip bootstrap wheels from PyPI failed - check the network or proxy"
goto :end_fail
:fail_dlgetpip_sha
call :err "a downloaded pip bootstrap wheel does not match its pinned SHA-256 - nothing was executed; the download was corrupted or tampered with. Delete the files in %DOWNLOADS% and retry; update a pin only together with a new, independently verified wheel"
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
:fail_validators
call :err "static validators failed - see the validator output in the log"
goto :end_fail
:fail_no_node
call :err "node.exe not found - required to run the JavaScript static validators (install Node.js or add it to PATH)"
goto :end_fail
:fail_smoke
call :err "smoke test failed - see %SRCDIR%\build_windows_smoke.log"
type "%SRCDIR%\build_windows_smoke.log" >>"%LOG%"
goto :end_fail
:fail_compiler
rem F1-18: put the previous installer back so a failed compile does not cost
rem the build machine its last usable installer.
if not exist "%INSTALLER%" if exist "%INSTALLER%.prev" move /y "%INSTALLER%.prev" "%INSTALLER%" >nul 2>nul
if exist "%INSTALLER%" call :err "installer compilation failed: %INSTALLER_ENGINE% (the previous installer was kept)"
if not exist "%INSTALLER%" call :err "installer compilation failed: %INSTALLER_ENGINE%"
goto :end_fail

:end_fail
rem F1-3: release the build lock on every failure path (guarded - a process
rem that failed to ACQUIRE the lock must never delete the owner's lock).
call :release_lock
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
rem F1-17: /PURGE - see the note on the build-folder copy above.
robocopy "%SRCPARENT%\%~1" "%BUILDROOT%\%~1" /E /PURGE /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache /XF SPEC.md *Zone.Identifier >>"%LOG%" 2>&1
if errorlevel 8 set "COPYFAIL=1"
goto :eof
:copydir_missing
call :log "note: %~1 not found in the source tree (skipped)"
goto :eof

:copyapp
rem %~1 = folder name, copied into the staging tree
if not exist "%SRCDIR%\..\%~1" goto :copyapp_missing
rem F1-17: staging is wiped at [2/6], so a plain /E is enough here - but the
rem relocated copy above can leave a deleted-upstream file behind, so /PURGE
rem keeps this pass authoritative too.
robocopy "%SRCDIR%\..\%~1" "%STAGING%\%~1" /E /PURGE /NFL /NDL /NJH /NJS /XD __pycache__ .pytest_cache /XF SPEC.md *Zone.Identifier >>"%LOG%" 2>&1
if errorlevel 8 set "COPYFAIL=1"
goto :eof
:copyapp_missing
call :log "note: %~1 not found in the source tree (skipped)"
goto :eof

:stagecopy
rem F1-26: %1 = source file, %2 = staged destination. One checked copy; a
rem failure is recorded so the caller can stop, and the missing file is named.
copy /y "%~1" "%~2" >nul 2>nul
if not exist "%~2" (
  call :err "cannot stage %~nx2 (source: %~1)"
  set "STAGECOPYFAIL=1"
)
goto :eof

:resolve_where
rem F1-19: bind %1 (an exe name) to the FIRST full path `where` reports, in %2
rem (the variable to set). %2 stays undefined when `where` finds nothing or
rem only reports a non-exe shadow, so the caller can fail with its own message.
set "%~2="
for /f "usebackq delims=" %%I in (`where %~1 2^>nul`) do if not defined %~2 if /I "%%~xI"=="%~x1" set "%~2=%%I"
goto :eof

:path_exists
rem F1-24: %1 = path, %2 = variable to set when the path EXISTS. The test runs
rem in a brand-new PowerShell process because cmd.exe's own `if exist` can
rem report a directory as absent after a partial rmdir in the same cmd.exe
rem (measured), and the path is handed over in a temp .ps1 so neither cmd's
rem nor PowerShell's quote nesting can mangle it. The scratch file lives next
rem to the build log, not in %TEMP%. Unset POWERSHELL_EXE falls back to cmd's
rem own check so the subroutine is still usable standalone.
set "%~2="
set "RF_PE_SCRIPT=%SRCDIR%\rf_path_exists_%RANDOM%%RANDOM%.ps1"
> "%RF_PE_SCRIPT%" echo if (Test-Path -LiteralPath $args[0]) { exit 3 } else { exit 0 }
if defined POWERSHELL_EXE (
  if defined LOGOK (
    "%POWERSHELL_EXE%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%RF_PE_SCRIPT%" "%~1" >>"%LOG%" 2>&1
  ) else (
    "%POWERSHELL_EXE%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%RF_PE_SCRIPT%" "%~1" >nul 2>&1
  )
  if not errorlevel 1 goto :path_exists_clean
  set "%~2=1"
  goto :path_exists_done
)
if exist "%~1" set "%~2=1"
:path_exists_done
del "%RF_PE_SCRIPT%" >nul 2>nul
goto :eof
:path_exists_clean
del "%RF_PE_SCRIPT%" >nul 2>nul
goto :eof

:log
rem F1-21: %~1 is echoed through a delayed-expansion variable. A bare
rem `echo [%TIME%] %~1` re-parsed the message, so a path containing `&` was cut
rem at the ampersand and the tail ran as a command (cmd's "system cannot find
rem the path" went to the log instead of the message). `echo(` prints the whole
rem line literally, even when it would start with a switch.
setlocal EnableDelayedExpansion
set "RFLINE=[%TIME%] %~1"
echo(!RFLINE!
if defined LOGOK >>"%LOG%" echo(!RFLINE!
endlocal
goto :eof

:err
rem F1-21: same re-parsing hazard as :log - see the note there.
setlocal EnableDelayedExpansion
set "RFLINE=[ERROR] %~1"
echo.
echo(!RFLINE!
if defined LOGOK >>"%LOG%" echo(!RFLINE!
endlocal
goto :eof

:acquire_lock
rem F1-3: atomic mutual exclusion via mkdir (atomic in cmd.exe).
rem %LOCKDIR% must be set by the caller. There is deliberately NO automatic
rem reclaim of an abandoned lock: every pid-based reclaim scheme is racy (the
rem winner must rd+mkdir, and a contender holding the stale pid will "take
rem over" the freshly created live lock - measured up to 4 of 4 builds inside
rem the section). A crashed build leaves the directory behind and the next
rem build stops with an actionable message; RF_BUILD_LOCK_HELD (set by the UNC
rem wrapper) tells an inner build it inherited an already-held lock.
if not defined RF_BUILD_LOCK_HELD (
  mkdir "%LOCKDIR%" 2>nul
  if errorlevel 1 goto :lock_busy
)
set "RF_LOCK_OWNED=1"
rem Record the owning cmd.exe so a later build can name it in its error
rem message. For /f runs its command through one child cmd.exe, so the query
rem walks two parent levels up from PowerShell's own process. The query goes
rem through a temporary .ps1 (a literal `for /f` with an inline web of quotes
rem breaks cmd's own parser) and the whole command is wrapped in an extra pair
rem of quotes because for /f strips the outer pair - that keeps
rem "%POWERSHELL_EXE%" (a full path that may contain spaces) as one token.
rem When PowerShell was not resolved - the subroutine is also exercised on its
rem own by the regression nails - the pid is simply unknown; the lock itself
rem is still owned, and :lock_busy names it without a pid.
set "LOCKPID="
if defined POWERSHELL_EXE call :lock_pid
if defined LOCKPID > "%LOCKDIR%\pid" echo %LOCKPID%
exit /b 0
:lock_pid
set "RF_PID_SCRIPT=%LOCKDIR%\..\rf_lock_pid.ps1"
> "%RF_PID_SCRIPT%" echo $p=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)).ParentProcessId
>>"%RF_PID_SCRIPT%" echo (Get-CimInstance Win32_Process -Filter ('ProcessId='+$p)).ParentProcessId
for /f "usebackq delims=" %%P in (`""%POWERSHELL_EXE%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%RF_PID_SCRIPT%""`) do set "LOCKPID=%%P"
del "%RF_PID_SCRIPT%" >nul 2>nul
goto :eof
:lock_busy
set "LOCKHOLDER="
if exist "%LOCKDIR%\pid" set /p LOCKHOLDER=<"%LOCKDIR%\pid"
call :err "another ReviewFlow packaging build holds %LOCKDIR%%LOCKHOLDER%; stop it first (delete the folder only if no build is running)"
exit /b 1

:release_lock
rem F1-3: only the owning process removes the lock, and an inner build must not
rem remove the lock its outer UNC wrapper still holds - the wrapper still has
rem to copy the installer back after the inner build returns.
if not defined RF_LOCK_OWNED goto :eof
if defined RF_LOCK_HANDED_DOWN goto :eof
if defined LOCKDIR if exist "%LOCKDIR%" rd /s /q "%LOCKDIR%" >nul 2>nul
set "RF_LOCK_OWNED="
goto :eof

:fetch_verified
rem MM9-13: %1=URL %2=cache path %3=pinned sha256. A cached file is
rem re-verified on every run; a mismatch deletes it and re-downloads once.
rem The two failure modes stay distinct because their fixes differ:
rem   exit 0 = the cached/downloaded file matches the pin
rem   exit 1 = the download itself failed (network, proxy, HTTP error)
rem   exit 2 = the bytes arrived but their digest does not match the pin
call :sha256_matches "%~2" "%~3"
if not errorlevel 1 exit /b 0
if exist "%~2" del /q "%~2" >nul 2>nul
"%CURL_EXE%" -L --fail --retry 2 -o "%~2" "%~1" >>"%LOG%" 2>&1
if errorlevel 1 (
  if exist "%~2" del /q "%~2" >nul 2>nul
  exit /b 1
)
call :sha256_matches "%~2" "%~3"
if errorlevel 1 exit /b 2
exit /b 0

:sha256_matches
rem MM9-13: %1=file %2=expected sha256; exit 0 iff certutil's digest of %1
rem equals %2 (case-insensitive). certutil prints the digest on the line after
rem a localized header; the two length checks reject a localized error message
rem that took the digest line's place (certutil text is locale-dependent).
if not exist "%~1" exit /b 1
set "ACTUAL_SHA256="
for /f "usebackq skip=1 delims=" %%H in (`certutil -hashfile "%~1" SHA256 2^>nul`) do if not defined ACTUAL_SHA256 set "ACTUAL_SHA256=%%H"
if not defined ACTUAL_SHA256 exit /b 1
if "%ACTUAL_SHA256:~63,1%"=="" exit /b 1
if not "%ACTUAL_SHA256:~64%"=="" exit /b 1
if /i not "%ACTUAL_SHA256%"=="%~2" exit /b 1
exit /b 0

