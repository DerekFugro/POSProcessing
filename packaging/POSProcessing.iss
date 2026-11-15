; Inno Setup script - POSProcessing_Setup_<version>.exe.  Built by packaging\build.ps1
; (which passes /DAppVersion=2026.11.15-abc1234 /DAppNumber=2026.11.15).
;
; Installs the app to C:\Program Files\Fugro Roadware\ACSApps\POSProcessing (next to the ACS
; apps) and makes C:\ProgramData\Fugro Roadware\POSProcessing for settings, the NOAA base-data
; cache and map tiles (shared by all users). Also installs the Trimble Office Configuration
; Utility and Trimble Convert to RINEX if they are not on the PC yet (needed for processing).
; The Trimble installers come from the local installers\ folder - never from git (public repo).
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef AppNumber
  #define AppNumber "0.0.0"
#endif
#define AppName "POSProcessing"
#define Dist "dist\POSProcessing"
#define TrimbleCfg "..\installers\TrimbleCFGUpdate.exe"
#define TrimbleRinex "..\installers\convertToRinex_v4_0_1_10_sign.msi"

[Setup]
; never change AppId - it is how Windows knows a new Setup.exe is an update
AppId={{8C3F5A21-6B7D-4E19-A0C4-5D2E9F716B30}
AppName=POS PPK Processor
AppVerName=POS PPK Processor {#AppVersion}
AppVersion={#AppVersion}
VersionInfoVersion={#AppNumber}
AppPublisher=Fugro Roadware
DefaultDirName={commonpf64}\Fugro Roadware\ACSApps\{#AppName}
DisableDirPage=yes
DefaultGroupName=POS PPK Processor
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename={#AppName}_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\POSProcessing.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Desktop shortcut"; Flags: unchecked

[InstallDelete]
; an update replaces the program files completely (old libraries never linger)
Type: filesandordirs; Name: "{app}\_internal"

[Dirs]
; settings + caches: one shared copy, every user may write; kept on uninstall
Name: "{commonappdata}\Fugro Roadware\POSProcessing"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\rtklib\rnx2rtkp.exe"; DestDir: "{app}\tools\rtklib"; Flags: ignoreversion
Source: "..\installers\README.txt"; DestDir: "{app}\installers"; Flags: ignoreversion
Source: "{#TrimbleCfg}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedTrimbleCfg
Source: "{#TrimbleRinex}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedTrimbleRinex

[Icons]
Name: "{autoprograms}\POS PPK Processor"; Filename: "{app}\POSProcessing.exe"
Name: "{autodesktop}\POS PPK Processor"; Filename: "{app}\POSProcessing.exe"; Tasks: desktopicon

[Run]
; Trimble first: Convert to RINEX crashes at start-up without the Configuration Utility
Filename: "{tmp}\TrimbleCFGUpdate.exe"; StatusMsg: "Installing Trimble Office Configuration Utility..."; Flags: waituntilterminated; Check: NeedTrimbleCfg
Filename: "msiexec.exe"; Parameters: "/i ""{tmp}\convertToRinex_v4_0_1_10_sign.msi"" /passive /norestart"; StatusMsg: "Installing Trimble Convert to RINEX..."; Flags: waituntilterminated; Check: NeedTrimbleRinex
Filename: "{app}\POSProcessing.exe"; Description: "Start POS PPK Processor"; Flags: nowait postinstall skipifsilent

[Code]
function NeedTrimbleCfg: Boolean;
begin
  Result := not DirExists(ExpandConstant('{commonpf32}\Common Files\Trimble\Config'));
end;

function NeedTrimbleRinex: Boolean;
begin
  Result := not FileExists(ExpandConstant('{commonpf32}\Trimble\ConvertToRinex\convertToRinex.exe'));
end;
