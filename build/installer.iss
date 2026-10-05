; 木笔ReviewFlow — Inno Setup 6/7 安装脚本。
; 由 build_windows.bat 从 staging\ReviewFlow 编译。

#define AppName "木笔ReviewFlow"
#ifndef AppVersion
#define AppVersion "2.0.1"
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

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\检查与修复 ReviewFlow"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"" --repair"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："; Flags: unchecked

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows_start.py"""; WorkingDir: "{app}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent
