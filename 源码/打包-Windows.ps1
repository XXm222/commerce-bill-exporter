# 电商账单 Windows 打包脚本
#
# 在「源码」目录中运行：
#   powershell -ExecutionPolicy Bypass -File .\打包-Windows.ps1
#
# 建虚拟环境装依赖 -> PyInstaller 打包 -> 安装自检 -> 编译安装包。
# 本文件必须存为「UTF-8 带 BOM」：Windows PowerShell 5.1 对没有 BOM 的 .ps1 会按系统 ANSI
# （中文机器上是 GBK）解码，下面所有中文提示都会变成乱码，用户就看不到失败原因了。
# 运行方式见上一行：powershell -ExecutionPolicy Bypass -File，用的正是 5.1。
# 任一步失败都会停下并打印原因，不会带着坏状态继续。

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Fail($text) { Write-Host "失败：$text" -ForegroundColor Red; exit 1 }

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Source
$Project = Split-Path -Parent $Source
Write-Host "源码目录：$Source"
Write-Host "项目目录：$Project"

# 1. Python 3.12
Step '检查 Python 3.12'
# 探测脚本写成临时文件，再用文件路径运行——命令行里因此不出现引号嵌套。
# 原来用的是 cmd /c "... -c ""...""" 这种写法，我加架构探测时多套了一层引号，
# 内层的 " 提前闭合了外层字符串；而开发机上通常没有 PowerShell，人眼很难看出来。
# 改成按文件运行后，这类错误从根上没有了。文件内容同时避开 %（会被 cmd 当变量展开）。
#
# 命令行里**不能出现带空格的路径**（2026-09-28 复查时发现）。原先这里拼的是 $env:TEMP
# 的全路径，而 %TEMP% 就是 C:\Users\<用户名>\AppData\Local\Temp——用户名带空格的机器
# （John Smith 这种很常见）路径里就有空格，cmd 会把路径拆成两截，py 收到的是一个不存在的
# 文件，报错走 stderr 被丢进 $null，stdout 为空；于是下面的判断认为「没找到 Python」，
# 脚本在第 49 行停下并显示「未找到 Python 3.12」——**而机器上其实装得好好的**。
# 现在改为在工作目录（= 源码目录）下建一个纯文件名的临时脚本，命令行里只有文件名，
# 不含路径也就不可能带空格；用完立刻删掉。
$probeName = 'commercebill-probe.py'
$probeFile = Join-Path (Get-Location) $probeName
Set-Content -LiteralPath $probeFile -Encoding ASCII -Value @(
    'import sys, sysconfig',
    'print(str(sys.version_info[0]) + chr(46) + str(sys.version_info[1]))',
    'print(sysconfig.get_platform())'
)
$python = $null
$arch = ''
foreach ($candidate in @('py -3.12', 'python')) {
    try {
        # 只给文件名，不给路径：路径一旦带空格，cmd 会把它拆开，见上面的说明。
        $out = & cmd /c "$candidate $probeName" 2>$null
        if ($out.Count -ge 1 -and "$($out[0])".Trim() -eq '3.12') {
            $python = $candidate
            if ($out.Count -ge 2) { $arch = "$($out[1])".Trim() }
            break
        }
    } catch { }
}
Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
if (-not $python) { Fail '未找到 Python 3.12。请先安装 Python 3.12（勾选 Add to PATH）后重试。' }
Write-Host "使用：$python（$arch）"

# 检查 Python 进程架构，不看系统架构：Windows ARM64 中运行 x64 Python 时，
# platform.machine() 仍可能报告 ARM64；sysconfig.get_platform() 才是打包目标。
if ($arch -ne 'win-amd64') {
    Fail "当前 Python 的构建目标是 $arch，需要 x64 Python 3.12（win-amd64），否则会生成与文件名不符的安装包。"
}

# 磁盘空间：PyInstaller 的 onedir 产物与 Inno Setup 的安装包会各占一份，空间不足会在中途失败。
try { $drive = Get-PSDrive -Name $Source.Substring(0,1) -ErrorAction Stop
      if ($drive.Free -lt 3GB) { Fail "磁盘可用空间不足 3GB（当前 $([math]::Round($drive.Free/1GB,1))GB）。打包会在中途失败，请先清理空间。" } } catch { }

# 2. 虚拟环境与依赖
Step '准备虚拟环境并安装依赖'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & cmd /c "$python -m venv .venv"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path '.venv\Scripts\python.exe')) { Fail '创建虚拟环境失败' }
}
$venv = '.venv\Scripts\python.exe'
# 原生命令（pip / PyInstaller / ISCC）失败时 PowerShell 不会自动中断，$ErrorActionPreference
# 管不到它们，必须逐个查 $LASTEXITCODE——否则 pip 装不上依赖也会继续打包出一个缺库的目录。
& $venv -m pip install --upgrade pip -q
if ($LASTEXITCODE -ne 0) { Fail '升级 pip 失败（检查网络或代理；国内网络可改用镜像：把 requirements-build.txt 那一行的 -r 换成 -r requirements-build.txt -i https://pypi.tuna.tsinghua.edu.cn/simple）' }
& $venv -m pip install -q -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { Fail '安装依赖失败（检查网络或代理；国内网络可加 -i https://pypi.tuna.tsinghua.edu.cn/simple 重试）' }
& $venv -m pip list | Select-String -Pattern 'PyQt6|pyinstaller'

# 4. 打包
Step 'PyInstaller 打包'
if (Test-Path 'dist') { Remove-Item -Recurse -Force 'dist' }
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
& $venv -m PyInstaller --noconfirm --clean CommerceBill.spec
if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller 打包失败' }
if (-not (Test-Path 'dist\CommerceBill\CommerceBill.exe')) { Fail '未生成 dist\CommerceBill\CommerceBill.exe' }

# 5. 安装自检（必须 PASS 才继续）
Step '运行安装自检'
$selfTest = Join-Path $env:TEMP 'commercebill-selftest.json'
# 先删掉上一次的报告：程序若启动就崩（缺 DLL 等），Get-Content 会读到旧报告，
# 里面可能写着 PASS，脚本就会带着坏构建继续做安装包。
if (Test-Path $selfTest) { Remove-Item -Force $selfTest }
& 'dist\CommerceBill\CommerceBill.exe' --self-test $selfTest
# Windows GUI 子系统程序可能在 PowerShell 返回之后才写出报告；实机出现过
# exe 已写 PASS=true、脚本却立即把“报告不存在”当失败。最多等 60 秒，
# 同时等 JSON 写完整，不能读到旧报告（上面已先删除）。
$deadline = (Get-Date).AddSeconds(60)
$result = $null
while (-not $result -and (Get-Date) -lt $deadline) {
    if (Test-Path $selfTest) {
        try { $result = Get-Content $selfTest -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { $result = $null }
    }
    if (-not $result) { Start-Sleep -Milliseconds 250 }
}
if (-not $result) { Fail "60 秒内未读到完整自检报告：$selfTest（程序可能启动失败）" }
$result | ConvertTo-Json -Depth 5
if (-not $result.PASS) { Fail '安装自检未通过，请检查上面的 errors' }
Write-Host '安装自检 PASS'

# 6. 安装包
Step '编译 Windows 安装包'
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host '未找到 Inno Setup 6 的 ISCC.exe。' -ForegroundColor Yellow
    Write-Host 'dist\CommerceBill 已经是可运行的程序目录；装上 Inno Setup 6.3 或更高（含 ChineseSimplified.isl）后重跑本脚本即可出安装包。'
    exit 0
}
# installer.iss 的 [Languages] 用了 compiler:Languages\ChineseSimplified.isl，安装包自带英文语言文件，
# 简体中文需要自己放进去；缺了它 ISCC 只会报一句找不到文件，不如在这里先查清楚。
$isl = Join-Path (Split-Path -Parent $iscc) 'Languages\ChineseSimplified.isl'
if (-not (Test-Path $isl)) {
    Fail "缺少简体中文语言文件：$isl`n请从 https://jrsoftware.org/files/istrans/ 下载 ChineseSimplified.isl 放到该目录。"
}
& $iscc 'installer.iss'
if ($LASTEXITCODE -ne 0) { Fail 'ISCC 编译失败：确认 Inno Setup 为 6.3 或更高——installer.iss 里的 ArchitecturesAllowed=x64compatible 需要 6.3+，而它正是 ARM64 Windows 上安装 x64 应用所必需的' }

Step '完成'
Get-ChildItem (Join-Path $Project 'installer') -Filter '*.exe' | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    Write-Host "$($_.Name)  $([math]::Round($_.Length/1MB,1)) MB"
    Write-Host "SHA256 $hash"
}
