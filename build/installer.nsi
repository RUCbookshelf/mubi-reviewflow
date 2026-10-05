Unicode true
SetCompressor /SOLID zlib

!ifndef APP_VERSION
  !define APP_VERSION "2.0.1"
!endif
!include "MUI2.nsh"
!include "x64.nsh"

Name "木笔ReviewFlow"
OutFile "Output\ReviewFlow-Setup-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\ReviewFlow"
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

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 64 位 Windows。"
    Abort
  ${EndIf}
FunctionEnd

Section "木笔ReviewFlow" SecApp
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "staging\ReviewFlow\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\ReviewFlow"
  CreateShortCut "$SMPROGRAMS\ReviewFlow\ReviewFlow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\windows_start.py$\"' "$INSTDIR\icon.ico"
  CreateShortCut "$SMPROGRAMS\ReviewFlow\检查与修复 ReviewFlow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\windows_start.py$\" --repair' "$INSTDIR\icon.ico"
  CreateShortCut "$SMPROGRAMS\ReviewFlow\卸载 ReviewFlow.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "DisplayName" "木笔ReviewFlow"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "Publisher" "Kang Tairong (bookshelf@ruc.edu.cn)"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow" "NoRepair" 1
SectionEnd

Section /o "桌面快捷方式" SecDesktop
  SetShellVarContext current
  CreateShortCut "$DESKTOP\ReviewFlow.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\windows_start.py$\"' "$INSTDIR\icon.ico"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\ReviewFlow.lnk"
  Delete "$SMPROGRAMS\ReviewFlow\ReviewFlow.lnk"
  Delete "$SMPROGRAMS\ReviewFlow\检查与修复 ReviewFlow.lnk"
  Delete "$SMPROGRAMS\ReviewFlow\卸载 ReviewFlow.lnk"
  RMDir "$SMPROGRAMS\ReviewFlow"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow"
  RMDir /r "$INSTDIR"
  ; User data lives in $LOCALAPPDATA\ReviewFlow and is retained.
SectionEnd
