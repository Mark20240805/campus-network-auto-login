; 校园网自动认证系统 —— Inno Setup 安装脚本
; 打包内容：Nuitka 目录版（stage_nuitka\校园网自动认证系统\），不含任何个人数据
; 安装时会自动检测旧版本安装目录并覆盖升级，保留 config.json / login.log

#define MyAppName "校园网自动认证系统"
#define MyAppVersion "1.1.0"
#define MyAppExeName "校园网自动认证系统.exe"
#define MyAppPublisher "CampusNet AutoLogin"
#ifndef MyPayloadDir
  #define MyPayloadDir "..\stage_nuitka\校园网自动认证系统"
#endif

[Setup]
SetupIconFile=..\tools\app.ico
; 固定 AppId：升级安装时能识别为同一个程序，不会重复装两份
AppId={{8F3A2C74-5B1E-4D9A-9C6E-2D7B4A1F0E33}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}

; 装到当前用户的 LocalAppData\Programs，无需管理员权限
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\CampusNetAutoLogin
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; 同 AppId 升级时沿用旧目录；[Code] 里还会再扫一遍，兼容换过 AppId 的旧版
UsePreviousAppDir=yes

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=output
OutputBaseFilename=校园网自动认证系统_安装包_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; 安装前若程序在运行，提示关闭
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[InstallDelete]
; 旧版 PyInstaller 目录结构：先清掉 _internal，避免和新版扁平结构混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; 整个载荷目录（PyInstaller 的 _internal 或 Nuitka 的扁平结构都适用）
Source: "{#MyPayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.json,login.log,_MEI*"
[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; 卸载时清掉程序写入的开机自启项
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; \
    ValueName: "CampusNetAutoLogin"; Flags: uninsdeletevalue

[UninstallDelete]
; 卸载时一并清掉用户配置、日志和单文件版可能留下的解包目录
Type: files; Name: "{app}\config.json"
Type: files; Name: "{app}\login.log"
Type: filesandordirs; Name: "{app}\_MEI*"

[Code]
const
  UNINST_ROOT = 'Software\Microsoft\Windows\CurrentVersion\Uninstall';
  OUR_APPID   = '{8F3A2C74-5B1E-4D9A-9C6E-2D7B4A1F0E33}_is1';
  OUR_EXE     = '校园网自动认证系统.exe';

{ 目录里确实有本程序的 exe 才算数，避免误判 }
function LooksLikeInstallDir(const Dir: String): Boolean;
var
  D: String;
begin
  Result := False;
  if Dir = '' then
    Exit;
  D := Dir;
  if Copy(D, Length(D), 1) <> '\' then
    D := D + '\';
  Result := FileExists(D + OUR_EXE);
end;

function ReadInstallLocation(RootKey: Integer; const SubKey: String): String;
var
  S: String;
begin
  Result := '';
  if RegQueryStringValue(RootKey, SubKey, 'InstallLocation', S) then
    if LooksLikeInstallDir(S) then
      Result := S;
end;

{ 遍历卸载项，按显示名匹配（兼容换过 AppId / 改过名字的旧版） }
function FindByDisplayName(RootKey: Integer): String;
var
  Names: TArrayOfString;
  I: Integer;
  SubKey, DisplayName, Loc: String;
begin
  Result := '';
  if not RegGetSubkeyNames(RootKey, UNINST_ROOT, Names) then
    Exit;
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    SubKey := UNINST_ROOT + '\' + Names[I];
    if RegQueryStringValue(RootKey, SubKey, 'DisplayName', DisplayName) then
      if (Pos('校园网', DisplayName) > 0) or (Pos('CampusNet', DisplayName) > 0) then
      begin
        Loc := ReadInstallLocation(RootKey, SubKey);
        if Loc <> '' then
        begin
          Result := Loc;
          Exit;
        end;
      end;
  end;
end;

function FindPreviousInstall: String;
var
  Roots: array[0..5] of Integer;
  I: Integer;
  P: String;
begin
  Roots[0] := HKCU;
  Roots[1] := HKLM;
  Roots[2] := HKCU32;
  Roots[3] := HKCU64;
  Roots[4] := HKLM32;
  Roots[5] := HKLM64;

  { 1) 本安装包的 AppId }
  for I := 0 to 5 do
  begin
    Result := ReadInstallLocation(Roots[I], UNINST_ROOT + '\' + OUR_APPID);
    if Result <> '' then
      Exit;
  end;

  { 2) 任意名字像本程序的卸载项 }
  for I := 0 to 5 do
  begin
    Result := FindByDisplayName(Roots[I]);
    if Result <> '' then
      Exit;
  end;

  { 3) 以往版本可能出现过的默认目录 }
  P := ExpandConstant('{localappdata}\Programs\CampusNetAutoLogin');
  if LooksLikeInstallDir(P) then begin Result := P; Exit; end;
  P := ExpandConstant('{localappdata}\Programs\校园网自动认证系统');
  if LooksLikeInstallDir(P) then begin Result := P; Exit; end;
  P := ExpandConstant('{pf}\CampusNetAutoLogin');
  if LooksLikeInstallDir(P) then begin Result := P; Exit; end;
  P := ExpandConstant('{pf32}\CampusNetAutoLogin');
  if LooksLikeInstallDir(P) then begin Result := P; Exit; end;
  Result := '';
end;

function DirParamGiven: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(Copy(ParamStr(I), 1, 5), '/DIR=') = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function IsUnderProgramFiles(const Dir: String): Boolean;
begin
  Result := (Pos(ExpandConstant('{pf}\'), Dir) = 1) or
            (Pos(ExpandConstant('{pf32}\'), Dir) = 1);
end;

procedure InitializeWizard;
var
  Found: String;
begin
  if DirParamGiven then
  begin
    Log('命令行指定了 /DIR，跳过旧版本目录检测');
    Exit;
  end;

  Found := FindPreviousInstall;
  if Found = '' then
  begin
    Log('未检测到旧版本安装目录，使用默认目录');
    Exit;
  end;

  if (not IsAdmin) and IsUnderProgramFiles(Found) then
  begin
    Log('旧版本在 Program Files 下且当前非管理员，保持默认目录: ' + Found);
    Exit;
  end;

  Log('检测到旧版本安装目录，将覆盖安装: ' + Found);
  WizardForm.DirEdit.Text := Found;
end;

{ 清掉旧版单文件/目录版可能留下的解包目录，避免和新版混在一起 }
procedure CleanOldLayout;
var
  Base: String;
  SR: TFindRec;
begin
  Base := ExpandConstant('{app}');
  if DirExists(Base + '\_internal') then
  begin
    Log('清理旧版 _internal 目录');
    DelTree(Base + '\_internal', True, True, True);
  end;
  if FindFirst(Base + '\_MEI*', SR) then
  begin
    repeat
      if (SR.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        Log('清理旧版解包目录: ' + SR.Name);
        DelTree(Base + '\' + SR.Name, True, True, True);
      end;
    until not FindNext(SR);
    FindClose(SR);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    CleanOldLayout;
end;
