; 木笔ReviewFlow — NSIS 安装脚本（Inno Setup 之外的备选引擎）。
;
; F1-38: this file is UTF-8 *with* BOM. makensis decodes a BOM-less script
; with the build machine's ANSI code page - on this CJK-ACP machine the
; BOM-less file aborted with "Bad text encoding: installer.nsi:32", and on an
; ACP that happens to swallow the bytes it ships a mojibake wizard. The BOM
; makes the encoding unconditional; build_windows.bat additionally passes
; /INPUTCHARSET UTF8 so a bare `makensis installer.nsi` stays correct too.
;
; F1-40/F1-34: the APP_VERSION fallback was a hardcoded "2.0.1" that had
; drifted behind VERSION. Without /D the version is now read from the
; repository VERSION file (verified: 2.1.4 from build/ as cwd); /D still wins.
;
; F1-36/F1-37: the uninstaller used to `RMDir /r "$INSTDIR"` unconditionally
; with no marker, no root-directory guard and no check for a running app -
; and it deleted the ARP entry *before* the files, so a failed delete left an
; orphan directory that claimed to be uninstalled. The uninstaller now
; re-reads InstallLocation from the registry, refuses a path that is not the
; install tree, stops a running ReviewFlow first, and only removes the
; registry entry after the files are gone.
;
; F1-30: installer.iss installs into the same directory. Installing this
; engine over an Inno install retires the Inno uninstaller + its ARP entry;
; an Inno install writes "reviewflow-install.marker", so this uninstaller
; refuses to recursively delete a tree it does not own.

Unicode true
SetCompressor /SOLID zlib

!ifndef APP_VERSION
  !define /file APP_VERSION "${__FILEDIR__}\..\VERSION"
!endif
!include "MUI2.nsh"
!include "x64.nsh"
; FileFunc provides ${GetFileName}, used by un.onInit to check that the
; recorded install location really is "...\ReviewFlow".
!include "FileFunc.nsh"

!define RF_MARKER "reviewflow-install.marker"
!define RF_ARP "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow"

Name "木笔ReviewFlow"
OutFile "Output\ReviewFlow-Setup-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\ReviewFlow"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "InstallLocation"
RequestExecutionLevel user
Icon "icon.ico"
UninstallIcon "icon.ico"
BrandingText "木笔ReviewFlow ${APP_VERSION}"

!define MUI_FINISHPAGE_RUN "$INSTDIR\runtime\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS '$\"$INSTDIR\windows_start.py$\"'
!define MUI_FINISHPAGE_RUN_TEXT "启动木笔ReviewFlow"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; F1-37: write the process probe to a temp file once per run; both the
; installer and the uninstaller call it. Exit 0 = no live process under the
; install directory, 3 = still running after the kill attempt.
!macro RF_WRITE_STOP_SCRIPT SCRIPTFILE
  FileOpen $0 "${SCRIPTFILE}" w
  FileWrite $0 "$$d = $$args[0]; if (-not $$d) { exit 0 }; $\r$\n"
  FileWrite $0 "$$f = [IO.Path]::GetFullPath($$d).TrimEnd([char]92) + [string][char]92; $\r$\n"
  FileWrite $0 "function Live { $$n = 0; foreach ($$p in Get-Process) { try { if ($$p.Path -and $$p.Path.StartsWith($$f, [StringComparison]::OrdinalIgnoreCase)) { $$n++ } } catch {} }; return $$n }; $\r$\n"
  FileWrite $0 "if ((Live) -eq 0) { exit 0 }; $\r$\n"
  FileWrite $0 "foreach ($$p in Get-Process) { try { if ($$p.Path -and $$p.Path.StartsWith($$f, [StringComparison]::OrdinalIgnoreCase)) { $$p.Kill() } } catch {} }; $\r$\n"
  FileWrite $0 "Start-Sleep -Seconds 2; $\r$\n"
  FileWrite $0 "if ((Live) -eq 0) { exit 0 }; $\r$\n"
  FileWrite $0 "exit 3$\r$\n"
  FileClose $0
!macroend

!macro RF_STOP_INSTALLED_PROCESSES RESULTVAR
  ; $PLUGINSDIR is NSIS' standard scratch directory, so no FileFunc macro is
  ; needed (and $TEMP may itself contain characters the PowerShell -File path
  ; would have to survive).
  InitPluginsDir
  StrCpy $R9 "$PLUGINSDIR\rf_stop_instances.ps1"
  !insertmacro RF_WRITE_STOP_SCRIPT "$R9"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$R9" "$INSTDIR"'
  Pop ${RESULTVAR}
  Delete "$R9"
!macroend

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 64 位 Windows。"
    Abort
  ${EndIf}
  ; F1-37: upgrading while ReviewFlow is running must not silently skip
  ; locked files.
  !insertmacro RF_STOP_INSTALLED_PROCESSES $0
  ${If} $0 == "3"
    MessageBox MB_OK|MB_ICONSTOP "无法关闭正在运行的木笔ReviewFlow：请从任务管理器结束 ReviewFlow 后重试安装。"
    Abort
  ${EndIf}
  ; F1-30: this engine is taking over the directory from an Inno install.
  ${If} ${FileExists} "$INSTDIR\unins000.exe"
    Delete "$INSTDIR\unins000.exe"
    Delete "$INSTDIR\unins000.dat"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\{A5037275-099D-4E16-9FAF-F1EA40F38A2C}_is1"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\{A5037275-099D-4E16-9FAF-F1EA40F38A2C}_is1_user"
  ${EndIf}
FunctionEnd

Section "木笔ReviewFlow" SecApp
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  ; F1-29: Inno leaves files that are not in this engine's file list; the
  ; previous tree is cleared first so an upgrade cannot keep stale modules.
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\custom_backend"
  RMDir /r "$INSTDIR\custom_frontend"
  RMDir /r "$INSTDIR\coscreen"
  RMDir /r "$INSTDIR\__pycache__"
  File /r "staging\ReviewFlow\*"
  ; F1-30: this tree is now an NSIS install; installer.iss' uninstaller
  ; checks for this marker before it deletes anything.
  FileOpen $0 "$INSTDIR\${RF_MARKER}" w
  FileWrite $0 "engine=nsis$\r$\n"
  FileWrite $0 "version=${APP_VERSION}$\r$\n"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\ReviewFlow"
  CreateShortCut "$SMPROGRAMS\ReviewFlow\ReviewFlow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\windows_start.py$\"' "$INSTDIR\icon.ico"
  CreateShortCut "$SMPROGRAMS\ReviewFlow\检查与修复 ReviewFlow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\windows_start.py$\" --repair' "$INSTDIR\icon.ico"
  CreateShortCut "$SMPROGRAMS\ReviewFlow\卸载 ReviewFlow.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${RF_ARP}" "DisplayName" "木笔ReviewFlow"
  WriteRegStr HKCU "${RF_ARP}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${RF_ARP}" "Publisher" "Kang Tairong (bookshelf@ruc.edu.cn)"
  WriteRegStr HKCU "${RF_ARP}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${RF_ARP}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${RF_ARP}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "${RF_ARP}" "NoModify" 1
  WriteRegDWORD HKCU "${RF_ARP}" "NoRepair" 1
SectionEnd

Section /o "桌面快捷方式" SecDesktop
  SetShellVarContext current
  CreateShortCut "$DESKTOP\ReviewFlow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\windows_start.py$\"' "$INSTDIR\icon.ico"
SectionEnd

Function un.onInit
  ; F1-36: never trust a moved Uninstall.exe. The directory to delete is the
  ; InstallLocation this package wrote; a missing or foreign value aborts
  ; instead of `RMDir /r`-ing wherever the uninstaller happens to run from.
  ReadRegStr $0 HKCU "${RF_ARP}" "InstallLocation"
  ${If} $0 == ""
    MessageBox MB_OK|MB_ICONSTOP "找不到 ReviewFlow 的安装位置记录，已取消卸载（未删除任何文件）。"
    Abort
  ${EndIf}
  ${GetFileName} "$0" $1
  ${If} $1 != "ReviewFlow"
    MessageBox MB_OK|MB_ICONSTOP "安装位置记录异常（$0），已取消卸载（未删除任何文件）。"
    Abort
  ${EndIf}
  StrCpy $INSTDIR "$0"
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  ; F1-30: installer.iss installs into this same directory. An Inno install
  ; owns the tree through its own uninstaller (unins000.exe); this uninstaller
  ; must not recursively delete a tree another engine currently manages. The
  ; Inno package deletes Uninstall.exe when it takes the tree over, so this
  ; guard is the second half of a symmetric handover.
  ${If} ${FileExists} "$INSTDIR\unins000.exe"
    IfSilent un_refuse_quiet
    MessageBox MB_OK|MB_ICONSTOP "此目录当前由 Inno Setup 安装包管理，请用它自己的卸载程序卸载。"
    un_refuse_quiet:
    Abort
  ${EndIf}
  ; F1-37: close a running ReviewFlow first - its pythonw.exe is a live
  ; process under $INSTDIR and the delete would silently fail.
  !insertmacro RF_STOP_INSTALLED_PROCESSES $0
  ${If} $0 == "3"
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "无法关闭正在运行的木笔ReviewFlow，卸载可能留下残余文件。仍要继续吗？" IDNO stop_uninstall
  ${EndIf}
  Delete "$DESKTOP\ReviewFlow.lnk"
  Delete "$SMPROGRAMS\ReviewFlow\ReviewFlow.lnk"
  Delete "$SMPROGRAMS\ReviewFlow\检查与修复 ReviewFlow.lnk"
  Delete "$SMPROGRAMS\ReviewFlow\卸载 ReviewFlow.lnk"
  RMDir "$SMPROGRAMS\ReviewFlow"
  ; F1-36: only ever the directory named ReviewFlow, and only the tree this
  ; package installed (the marker above). $INSTDIR cannot reach a drive root
  ; or any ancestor of it.
  RMDir /r "$INSTDIR"
  ; F1-37: the ARP entry goes last, so a failed delete no longer leaves an
  ; "uninstalled" entry with an orphan directory behind it.
  DeleteRegKey HKCU "${RF_ARP}"
  Goto done
  stop_uninstall:
    Abort
  done:
SectionEnd
