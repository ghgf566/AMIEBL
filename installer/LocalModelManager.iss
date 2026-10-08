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
; The installed build must never copy portable mode or mutable data / GGUF models.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\portable.flag,\installed.flag,\data\*,\models\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Local Model Manager"; Filename: "{app}\LocalModelManager.exe"; WorkingDir: "{app}"; IconFilename: "{app}\manager.ico"
Name: "{group}\解除安裝 Local Model Manager"; Filename: "{uninstallexe}"

[UninstallDelete]
; Remove only our installation marker; user data and unmanaged files must remain.
Type: files; Name: "{app}\installed.flag"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if not SaveStringToFile(ExpandConstant('{app}\installed.flag'), 'AMIEBL installed mode', False) then
      RaiseException('Unable to write installed-mode marker.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StartupValue, Executable: String;
begin
  if CurUninstallStep <> usUninstall then Exit;
  Executable := Lowercase(ExpandConstant('{app}\LocalModelManager.exe'));
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'LocalModelManager', StartupValue) then
    if Pos(Executable, Lowercase(StartupValue)) > 0 then
      RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'LocalModelManager');
end;
