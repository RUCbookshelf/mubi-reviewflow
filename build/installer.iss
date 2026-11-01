; 木笔ReviewFlow — Inno Setup 6/7 安装脚本。
; 由 build_windows.bat 从 staging\ReviewFlow 编译。
;
; F1-33: this file must stay UTF-8 *with* BOM. Inno Setup 6.3.0 was the first
; release that decodes a BOM-less .iss as UTF-8; 6.0-6.2 fall back to the
; machine's ANSI code page, which on a CJK ACP either aborts with "Missing
; closing quote" (measured with ISCC 6.0.5 on the BOM-less file) or silently
; ships a mojibake wizard. The BOM makes the file's encoding unconditional.
;
; F1-34/F1-40: the AppVersion fallback used to be a hardcoded "2.0.1" that had
; drifted two minor versions behind VERSION, so any compile without /D lied
; about what it built. Without /D the version is now read from the repository
; VERSION file (verified: ISPP FileRead yields 2.1.4 for the checkout, and
; /DAppVersion still wins, so build_windows.bat keeps passing it).
;
; F1-29/F1-32: [InstallDelete] clears the previous version's runtime and
; application trees before the new ones land (Inno keeps files that are not in
; the new [Files] list, and windows_start.py puts {app} first on sys.path, so
; stale modules were importable); [UninstallDelete] removes the bytecode
; caches the app writes into {app} at run time, which Inno's uninstall log
; never knew about.
;
; F1-30/F1-37: the install directory is shared with installer.nsi, and both
; installers used to leave the other engine's uninstaller behind - the old
; NSIS uninstaller then did `RMDir /r` on an Inno install. Installing now
; removes the NSIS leftovers (its uninstaller and its ARP entry), marks the
; tree as an Inno install ("reviewflow-install.marker") so the NSIS
; uninstaller refuses to touch it, and closes a running ReviewFlow before
; replacing or removing files (the launcher keeps the backend alive after the
; browser closes, and its cwd/file handles make the deletes fail silently).

#define AppName "木笔ReviewFlow"
#ifndef AppVersion
  #define AppVersion Trim(FileRead(FileOpen(AddBackslash(SourcePath) + "..\\VERSION")))
#endif

[Setup]
AppId={{A5037275-099D-4E16-9FAF-F1EA40F38A2C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Kang Tairong (bookshelf@ruc.edu.cn)
DefaultDirName={localappdata}\Programs\ReviewFlow
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\icon.ico
OutputDir=Output
OutputBaseFilename=ReviewFlow-Setup-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
SetupIconFile=icon.ico

[Files]
Source: "staging\ReviewFlow\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; F1-30: a previous NSIS install leaves its own uninstaller in this same
; directory; the Inno package takes the tree over and retires it.
Type: files; Name: "{app}\Uninstall.exe"
; F1-29: the whole runtime and the three application trees are replaced, so
; files that no longer exist upstream cannot survive an upgrade. These are
; exactly the trees the package owns - no wildcard over {app}, so a file the
; user happened to put there is not swept up.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\custom_backend"
Type: filesandordirs; Name: "{app}\custom_frontend"
Type: filesandordirs; Name: "{app}\coscreen"
Type: filesandordirs; Name: "{app}\__pycache__"
; the staging root's own entry points (all restored from the new package)
Type: files; Name: "{app}\launcher.py"
Type: files; Name: "{app}\windows_start.py"
Type: files; Name: "{app}\windows_smoke.py"
Type: files; Name: "{app}\repair_reviewflow.bat"
Type: files; Name: "{app}\requirements-app.txt"
Type: files; Name: "{app}\runtime-lock.txt"
Type: files; Name: "{app}\THIRD_PARTY_NOTICES.md"
Type: files; Name: "{app}\VERSION"
Type: files; Name: "{app}\icon.ico"
Type: files; Name: "{app}\icon_256.png"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\检查与修复 ReviewFlow"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"" --repair"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："; Flags: unchecked

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"""; WorkingDir: "{app}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; F1-32: run-time __pycache__ trees and the replaced source trees are outside
; Inno's uninstall log; without these entries they stay behind forever.
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\custom_backend"
Type: filesandordirs; Name: "{app}\custom_frontend"
Type: filesandordirs; Name: "{app}\coscreen"
Type: files; Name: "{app}\reviewflow-install.marker"

[Code]
const
  RF_ARP_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\ReviewFlow';
  RF_MARKER = 'reviewflow-install.marker';

{ F1-37: ReviewFlow's launcher keeps the backend process alive after the
  browser is closed and runs it with the install directory as its working
  directory. Replacing or deleting files under a live installation either
  fails silently (Inno asks the user to retry a locked file) or leaves a
  half-mixed tree. Stop the processes that live under the install directory
  first; report whether the directory ended up free. The PowerShell body is
  written to a temp file so it needs no shell quoting; exit 0 = nothing
  running, 3 = still running. }
function StopInstalledProcesses(const Root: String): Boolean;
var
  Script, ScriptFile: String;
  ResultCode: Integer;
begin
  Script :=
    '$d = $args[0]' + #13#10 +
    'if (-not $d) { exit 0 }' + #13#10 +
    '$f = [IO.Path]::GetFullPath($d).TrimEnd([char]92) + [string][char]92' + #13#10 +
    'function Live { $n = 0; foreach ($p in Get-Process) { try { if ($p.Path -and $p.Path.StartsWith($f, [StringComparison]::OrdinalIgnoreCase)) { $n++ } } catch {} }; return $n }' + #13#10 +
    'if ((Live) -eq 0) { exit 0 }' + #13#10 +
    'foreach ($p in Get-Process) { try { if ($p.Path -and $p.Path.StartsWith($f, [StringComparison]::OrdinalIgnoreCase)) { $p.Kill() } } catch {} }' + #13#10 +
    'Start-Sleep -Seconds 2' + #13#10 +
    'if ((Live) -eq 0) { exit 0 }' + #13#10 +
    'exit 3';
  ScriptFile := ExpandConstant('{tmp}\rf_stop_instances.ps1');
  SaveStringToFile(ScriptFile, Script, False);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ScriptFile + '" "' + Root + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := ResultCode = 0;
end;

{ F1-30: retire the other engine's uninstall identity when this package takes
  over the directory. Only reached when the old NSIS uninstaller is present,
  i.e. this directory really was installed by installer.nsi. }
procedure RetireLeftoverNsisInstall;
begin
  if FileExists(ExpandConstant('{app}\Uninstall.exe')) then begin
    RegDeleteKeyIncludingSubkeys(HKCU, RF_ARP_KEY);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopInstalledProcesses(ExpandConstant('{app}')) then
    Result := '无法关闭正在运行的木笔ReviewFlow：请从任务管理器结束 ReviewFlow 后重试安装。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Marker: String;
begin
  if CurStep = ssInstall then
    RetireLeftoverNsisInstall;
  if CurStep = ssPostInstall then begin
    Marker := 'engine=inno' + #13#10 + 'version={#AppVersion}' + #13#10;
    SaveStringToFile(ExpandConstant('{app}\') + RF_MARKER, Marker, False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    if not StopInstalledProcesses(ExpandConstant('{app}')) then
      if MsgBox('无法关闭正在运行的木笔ReviewFlow，卸载可能留下残余文件。仍要继续吗？',
                mbConfirmation, MB_YESNO) = IDNO then
        Abort;
  end;
end;
