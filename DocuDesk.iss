#define MyAppName "DocuDesk"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "DocuDesk"
#define MyAppExeName "DocuDesk.exe"

[Setup]
AppId={{B16C3E65-6D9D-4D7E-9D25-0C77E1C4E12A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\DocuDesk
DefaultGroupName=DocuDesk
OutputDir=installer
OutputBaseFilename=DocuDesk-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
Source: "dist\DocuDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DocuDesk"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\DocuDesk"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить DocuDesk"; Flags: nowait postinstall skipifsilent
