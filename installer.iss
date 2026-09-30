; 电商账单 Windows 安装包（Inno Setup）
;
; 编译需要 Inno Setup 6.3 或更高：
;   ArchitecturesAllowed=x64compatible 是 6.3 才有的标识（6.3 之前叫 x64，语义也不同）。
;   这里要的正是 6.3 的新语义——x64compatible 允许在 ARM64 Windows 11 上通过模拟安装
;   x64 应用，而本项目的验收环境就是 Windows 11 ARM 的 x64 兼容环境；
;   若改用旧的 x64（现名 x64os）会把这类机器挡在门外。
; [Languages] 用到的 compiler:Languages\ChineseSimplified.isl 不自带，需自行放入 Inno 安装目录的
; Languages 子目录（下载：https://jrsoftware.org/files/istrans/）。
;
; 本文件存为不带 BOM 的 UTF-8。这是合规的，但只在 6.3 及以上合规：据 Inno Setup 官方文档
; （Unicode Inno Setup），它支持 UTF-8 的 .iss，且「从 6.3 起不再需要 BOM」；6.3 之前没有 BOM
; 会按系统 ANSI 解码，中文（AppName、快捷方式描述等）会变成乱码。上面那条 6.3+ 的要求
; 因此不只是为了 x64compatible，也是本文件编码能正确解析的前提。

[Setup]
AppId={{230E4CC8-C55D-463E-9B9B-4FA7DE44FD18}
AppName=电商账单
AppVersion=0.6.3
AppPublisher=电商账单
DefaultDirName={localappdata}\Programs\CommerceBill
DefaultGroupName=电商账单
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=installer
OutputBaseFilename=CommerceBill_Windows_x64_0.6.3_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\CommerceBill.exe
CloseApplications=yes
[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
[Files]
; 源目录由 CommerceBill.spec 产出：PyInstaller 默认输出 dist\CommerceBill。
; 若改用命令行并指定 --distpath，这里必须同步修改，否则安装包会缺文件。
Source: "dist\CommerceBill\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\电商账单"; Filename: "{app}\CommerceBill.exe"
Name: "{autodesktop}\电商账单"; Filename: "{app}\CommerceBill.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\CommerceBill.exe"; Description: "打开电商账单"; Flags: nowait postinstall skipifsilent
