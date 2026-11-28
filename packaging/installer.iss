; packaging/installer.iss — Inno Setup script for the Windows installer.
;
; Compiles dist\OwnVoiceReader-Setup-x86_64.exe from the PyInstaller one-dir bundle
; (dist\OwnVoiceReader\). Compile with:  iscc packaging\installer.iss
; (the driver is packaging\make_installer.ps1, which installs Inno Setup if absent).
;
; The onedir must be copied WHOLE — the launcher exe must stay beside its _internal\
; sibling — so we install the folder recursively. SolidCompression + lzma2/max
; re-compresses the already-packed onedir to keep the setup size down.
;
; Paths are relative to this .iss (packaging/), so ..\dist == <repo>/dist.

#define AppName "OwnVoiceReader"
#define AppExe "OwnVoiceReader.exe"
#define Onedir "..\dist\" + AppName

[Setup]
; A fixed GUID identifies the app for uninstall; keep it stable across versions.
; No braces: Inno Setup 6's AppId takes any unique string. The {{...}} wrapping is a
; legacy Windows-registry convention, and an unbalanced one (two '{' , one '}') puts the
; ISPP preprocessor in error-recovery mode, which misreports the *next* directive
; ("Unrecognized [Setup] section directive Architectures") even though it's valid.
AppId=B2F4C8A1-7D3E-4A9C-9E2B-1F6A3C5D8E01
AppName={#AppName}
AppVersion=0.1.0
AppPublisher=Paulo Viviurka do Carmo
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=OwnVoiceReader-Setup-x86_64
Architectures=x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The onedir is ~1.4 GB; reserve room so the wizard fails cleanly on a small disk.
DiskSpaceReq=2048
UninstallDisplayIcon={app}\{#AppExe}

[Files]
Source: "{#Onedir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent
