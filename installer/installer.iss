; Osmo Offload — Inno Setup installer
; Build: iscc installer\installer.iss  (after PyInstaller has produced dist\OsmoOffload.exe)

#ifndef AppVersion
#define AppVersion "0.9.0"
#endif

[Setup]
AppId={{7E1F63A2-8B7D-4A79-B9E5-2F1D0A6C4E11}
AppName=Osmo Offload
AppVersion={#AppVersion}
AppPublisher=Quindor
AppPublisherURL=https://intermit.tech
DefaultDirName={autopf}\Osmo Offload
DefaultGroupName=Osmo Offload
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=OsmoOffload-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\OsmoOffload.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startupicon"; Description: "&Start Osmo Offload with Windows"; Flags: unchecked

[Files]
Source: "..\dist\OsmoOffload.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Osmo Offload"; Filename: "{app}\OsmoOffload.exe"
Name: "{autodesktop}\Osmo Offload"; Filename: "{app}\OsmoOffload.exe"; Tasks: desktopicon
Name: "{userstartup}\Osmo Offload"; Filename: "{app}\OsmoOffload.exe"; Tasks: startupicon

[Run]
Filename: "{app}\OsmoOffload.exe"; Description: "Launch Osmo Offload now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; user data (config/history/logs in %APPDATA%) is deliberately kept on uninstall
Type: filesandordirs; Name: "{app}"
