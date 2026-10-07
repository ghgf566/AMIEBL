#define MyAppName "Local Model Manager"
#define MyAppVersion "1.0.0"
#ifndef SourceDir
  #define SourceDir "..\..\outputs\LocalModelManager-release\portable"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\outputs\LocalModelManager-release\installer"
#endif

[Setup]
AppId={{E2C7192B-0D96-4D5E-AF2E-90E6C0C21C9D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Local Model Manager
DefaultDirName={localappdata}\Programs\LocalModelManager
DefaultGroupName={#MyAppName}
OutputDir={#OutputDir}
OutputBaseFilename=LocalModelManager-Setup
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
Uninstallable=yes
UninstallDisplayIcon={app}\manager.ico

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Local Model Manager"; Filename: "{app}\LocalModelManager.exe"; WorkingDir: "{app}"; IconFilename: "{app}\manager.ico"
Name: "{group}\解除安裝 Local Model Manager"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LocalModelManager"; ValueData: "{app}\LocalModelManager.exe --data-dir ""{localappdata}\LocalModelManager"""; Flags: uninsdeletevalue

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\LocalModelManager"
