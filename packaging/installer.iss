; Inno Setup script: wraps the PyInstaller folder (with Tesseract inside) into one Setup.exe.
; Built by .github/workflows/build.yml:  iscc /DAppVersion=1.0.0 /DSourceDir=..\dist\PoE Harvest Tracker installer.iss
;
; Installs for the current user only (no administrator prompt), into
; %LOCALAPPDATA%\Programs\PoE Harvest Tracker. Uninstalling keeps your data (Documents\PoE Harvest).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\PoE Harvest Tracker"
#endif
#define AppName "PoE Harvest Tracker"
#define AppExe "PoE Harvest Tracker.exe"

[Setup]
AppId={{6C4B7B8E-3F2A-4C1D-9E7B-2A5D9F0C8E41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Alyrico
AppPublisherURL=https://github.com/Alyrico/poe-harvest
AppSupportURL=https://github.com/Alyrico/poe-harvest/issues
AppUpdatesURL=https://github.com/Alyrico/poe-harvest/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=PoE-Harvest-Tracker-Setup-{#AppVersion}
SetupIconFile=..\src\poe_harvest\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE.txt
; close a running copy before updating it
AppMutex=Local\PoEHarvestTracker
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM ""{#AppExe}"" /F"; Flags: runhidden; RunOnceId: "StopApp"

[Registry]
; "Start with Windows" is switched on in the app's Settings; remove it when uninstalling
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey
