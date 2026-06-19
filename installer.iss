; EcountERP 재고현황 조회 - Inno Setup 설치 스크립트
; 빌드: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; 사전 조건: dist\EcountInventory.exe 가 먼저 PyInstaller 로 빌드되어 있어야 함
;
; 업데이트 정책: 동일 AppId 로 식별되며, 새 버전 설치 시작 시 이전 버전을
;               자동으로 silent 언인스톨한 뒤 새로 설치한다(이전 버전 잔존 방지).

#define MyAppId "8F3C2A91-7B4D-4E26-9A1F-EC0A17E5C001"
#define MyAppName "실시간 재고 현황(EcountERP) 및 평균 원가(Wizfasta) 비교"
#define MyShortcutName "원가비교 프로그램"
#define MyAppVersion "1.0.89"
#define MyAppPublisher "THE FEEL KOREA CO.,LTD."
#define MyAppExeName "EcountInventory.exe"

[Setup]
; 기존 설치(v1.0.0)와 동일한 AppId 를 유지해야 같은 앱으로 인식되어 덮어쓰기/제거가 된다.
; 이 형태는 런타임 AppId 가 "{8F3C...C001}}" (닫는 중괄호 2개)로 저장된다.
AppId={{8F3C2A91-7B4D-4E26-9A1F-EC0A17E5C001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (C) THE FEEL KOREA CO.,LTD.
DefaultDirName={autopf}\EcountInventory
DefaultGroupName={#MyShortcutName}
; 업그레이드 시에도 이전 그룹명을 재사용하지 않고 새 이름(원가비교 프로그램)을 사용
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=EcountInventory_Setup_v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 설치 마법사 / 제어판 표시 아이콘
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; 같은 버전 재설치 시에도 깔끔하게 덮어쓰도록
UninstallDisplayName={#MyAppName}
; 설치 대상 exe 가 실행 중이면 Restart Manager 로 자동 종료(잠금 해제)
CloseApplications=force
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\EcountInventory.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "wizfasta_extract.js"; DestDir: "{app}"; Flags: ignoreversion
Source: "update.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "data\wizfasta_products.json"; DestDir: "{app}\data"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyShortcutName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\{cm:UninstallProgram,{#MyShortcutName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyShortcutName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[Run]
; 일반(대화형) 설치: 마침 페이지의 체크박스로 실행
; (무인/자동 업데이트 설치 시 재실행은 앱의 업데이터 창이 담당하므로 여기서는 하지 않음)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
{ --- 설치 시작 시 이전 버전을 자동으로 제거(언인스톨)한다 --- }

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  { 실제 등록 키는 여는 중괄호 1개 + 닫는 중괄호 2개 + _is1 형태이다 }
  { 문자열 안 리터럴 중괄호는 Chr 로 생성해 Pascal 주석 오인을 피한다 }
  sUnInstPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Chr(123) + '{#MyAppId}' + Chr(125) + Chr(125) + '_is1';
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function UnInstallOldVersion(): Integer;
var
  sUnInstallString: String;
  iResultCode: Integer;
begin
  { 반환: 1 - 이전 버전 없음, 2 - 언인스톨 실행 실패, 3 - 언인스톨 성공 }
  Result := 0;
  sUnInstallString := GetUninstallString();
  if sUnInstallString <> '' then begin
    sUnInstallString := RemoveQuotes(sUnInstallString);
    if Exec(sUnInstallString,
            '/SILENT /NORESTART /SUPPRESSMSGBOXES',
            '', SW_HIDE, ewWaitUntilTerminated, iResultCode) then
      Result := 3
    else
      Result := 2;
  end else
    Result := 1;
end;

procedure KillRunningApp();
var
  iResultCode: Integer;
begin
  { 실행 중인 앱을 강제 종료해 설치 대상 파일의 잠금을 푼다 }
  Exec('taskkill.exe', '/IM {#MyAppExeName} /F', '',
       SW_HIDE, ewWaitUntilTerminated, iResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { 실제 파일 복사(ssInstall) 직전에: 실행 중 앱 종료 → 이전 버전 제거 }
  if (CurStep = ssInstall) then
  begin
    KillRunningApp();
    if IsUpgrade() then
      UnInstallOldVersion();
  end;
end;
