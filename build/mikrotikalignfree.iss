; Скрипт за Inno Setup 6 (безплатен). Компилира се от build_windows.bat.
#ifndef AppVersion
  #define AppVersion "0.5.0"
#endif

[Setup]
AppId={{6F2B8E54-3D1A-4C77-9B0E-A11C0DEF0001}
AppName=MikrotikAlignFree
AppVersion={#AppVersion}
AppPublisher=MikrotikAlignFree
DefaultDirName={autopf}\MikrotikAlignFree
DefaultGroupName=MikrotikAlignFree
DisableProgramGroupPage=yes
OutputDir=..\installer
OutputBaseFilename=MikrotikAlignFree-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
; Windows 7 SP1 или по-нов; 32-битовата програма работи и на 32-, и на 64-битови системи
MinVersion=6.1sp1
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\MikrotikAlignFree.exe
WizardStyle=modern

[Languages]
#if FileExists(CompilerPath + "Languages\Bulgarian.isl")
Name: "bg"; MessagesFile: "compiler:Languages\Bulgarian.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\MikrotikAlignFree\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{group}\MikrotikAlignFree"; Filename: "{app}\MikrotikAlignFree.exe"
Name: "{autodesktop}\MikrotikAlignFree"; Filename: "{app}\MikrotikAlignFree.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MikrotikAlignFree.exe"; Description: "{cm:LaunchProgram,MikrotikAlignFree}"; Flags: nowait postinstall skipifsilent
