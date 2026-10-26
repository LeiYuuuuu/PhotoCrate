; Inno Setup 6 安装脚本：把 dist\PhotoCrate 打成 PhotoCrate-Setup.exe
#define AppName "PhotoCrate"
#define AppVersion "2.2.1"

[Setup]
AppId={{6E3B2E7A-8C1D-4F5B-9A3E-5C2D7B1A9F40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PhotoCrate
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=dist
OutputBaseFilename=PhotoCrate-Setup
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\PhotoCrate.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\PhotoCrate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PhotoCrate.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PhotoCrate.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PhotoCrate.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
