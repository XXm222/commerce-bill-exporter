"""浏览器运行环境自检与安装。

APP 的浏览器操作依赖用户安装的 Google Chrome，以及两个外部组件：
  · Google Chrome —— APP 只检测，不随包安装；缺失时打开 Google 官方下载页。
  · 运行端（本地桥接服务，监听 127.0.0.1:10086）—— **由 APP 负责安装**（`install()`）。
    从官方 CDN 下载二进制、校验 sha256、落到官方同款路径、启动。
    **不随安装包内置**：它是第三方程序，打包再分发需要许可；从官方 CDN 取只是
    「替用户执行官方安装」，顺带避免随包版本过期。
  · 浏览器扩展 —— **只能用户自己装**。它只能从 Chrome 应用商店装进用户的日常浏览器，
    程序没有正当手段代装（企业策略强推要管理员权限，对个人用户不适用），所以只做引导：
    `extension_store_url()` 给直达链接，`official_page()` 给国内打不开商店时的入口。

为什么要自检：缺少浏览器、运行端或扩展时，都要告诉用户下一步该做什么。

**底层是 CDP**（官方 FAQ 原文：扩展通过 Chrome DevTools Protocol 操作你当前用的 Chrome）。
CDP 的授权裹在扩展自身权限里——用户不需要手动开调试端口，也不会被弹授权框；
代价是扩展挂上时 Chrome 会显示一条「已开始调试此浏览器」的信息条。

安装布局（2026-09-28 读官方 install.sh / install.ps1 确认，勿改）：
    macOS/Linux  $HOME/.kimi-webbridge/bin/kimi-webbridge
    Windows      %USERPROFILE%\\.kimi-webbridge\\bin\\kimi-webbridge.exe
    下载地址     https://cdn.kimi.com/webbridge/{版本}/releases/kimi-webbridge-{平台}
"""
import hashlib, json, os, platform, subprocess, sys, sysconfig, time, urllib.request
from pathlib import Path

STATUS_URL='http://127.0.0.1:10086/status'
CHROME_DOWNLOAD='https://www.google.com/chrome/download-chrome/'
OFFICIAL='https://www.kimi.com/products/kimi-browser-extension'
STORE='https://chromewebstore.google.com/detail/kimi/fldmhceldgbpfpkbgopacenieobmligc'
VERSION_API='https://cdn.kimi.com/webbridge/latest/version.json'
HELP='https://www.kimi.com/zh-cn/help/kimi-webbridge'
INSTALL_CMD={'darwin':'curl -fsSL https://cdn.kimi.com/webbridge/install.sh | bash',
             'linux':'curl -fsSL https://cdn.kimi.com/webbridge/install.sh | bash',
             'win32':'irm https://cdn.kimi.com/webbridge/install.ps1 | iex'}
DAEMON_DIR=Path.home()/'.kimi-webbridge'
DAEMON_BIN=DAEMON_DIR/'bin'/('kimi-webbridge.exe' if os.name=='nt' else 'kimi-webbridge')
DAEMON_LOG=DAEMON_DIR/'logs'/'daemon.log'
TIMEOUT=6
IS_WINDOWS=os.name=='nt'
IS_MACOS=sys.platform=='darwin'

def chrome_executable(override=''):
    """只查本机已安装的 Google Chrome，不启动浏览器，也不下载 Chromium。"""
    candidates=[override] if override else []
    if IS_WINDOWS:
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',0,winreg.KEY_READ|view) as key:
                            candidates.append(winreg.QueryValue(key,None).strip('"'))
                    except OSError:pass
        except ImportError:pass
        for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
            if os.environ.get(key):candidates.append(str(Path(os.environ[key])/'Google/Chrome/Application/chrome.exe'))
    elif IS_MACOS:
        candidates.extend(('/Applications/Google Chrome.app',str(Path.home()/'Applications/Google Chrome.app')))
    for value in candidates:
        if not value:continue
        path=Path(value)
        if IS_MACOS and path.suffix.lower()=='.app':path=path/'Contents/MacOS/Google Chrome'
        if path.is_file() and path.name.lower() in ('chrome.exe','google chrome'):
            return str(path)
    return ''

def install_command(system=None):
    return INSTALL_CMD.get(system or sys.platform,INSTALL_CMD['linux'])

def extension_store_url():
    return STORE

def open_extension_store(chrome_override=''):
    """使用本机 Google Chrome 打开扩展页，不交给系统默认浏览器。"""
    executable=chrome_executable(chrome_override)
    if not executable:
        raise RuntimeError('未找到 Google Chrome，请先安装浏览器，再重新检查运行环境')
    if IS_MACOS:
        # 实机验证：直接执行 Chrome 或 open -a 都可能返回 0 却不打开标签。
        # Apple Events 的 open location 能把网址交给用户已在运行的 Chrome。
        bundle=str(Path(executable).parents[2])
        script=f'tell application {json.dumps(bundle,ensure_ascii=False)} to open location {json.dumps(STORE)}'
        try:
            result=subprocess.run(['/usr/bin/osascript','-e',script],
                                  capture_output=True,text=True,timeout=12)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f'无法让 Google Chrome 打开扩展页：{exc}') from None
        if result.returncode:
            raise RuntimeError('Google Chrome 未能打开扩展页：'+(result.stderr.strip() or '请检查系统自动化权限')[:180])
    else:
        try:
            subprocess.Popen([executable,'--new-tab',STORE],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise RuntimeError(f'无法启动 Google Chrome：{exc}') from None
    return executable

def official_page():
    return OFFICIAL

def help_page():
    return HELP

def probe(timeout=TIMEOUT,port=10086,chrome_override=''):
    """检查 Chrome 安装与本地运行端；连不上运行端也保留 Chrome 结果。

    /status 会一起告诉我们扩展连没连上，所以一次请求就能判完两件事。
    """
    chrome=bool(chrome_executable(chrome_override))
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/status',timeout=timeout) as response:
            state=json.load(response)
    except Exception:
        return {'daemon':False,'chrome_installed':chrome}
    state['daemon']=True;state['chrome_installed']=chrome
    return state

def verdict(state):
    # 扩展已连接意味着浏览器正在使用；即使 Chrome 装在非标准路径，也不拦住可用会话。
    if state.get('chrome_installed') is False and not state.get('extension_connected'):
        return 'chrome_missing'
    if not state.get('daemon'):return 'daemon_missing'
    if not state.get('extension_connected'):return 'extension_missing'
    return 'ready'

def describe(state):
    """把状态转成界面要用的三段话，避免文案散在界面代码里。

    action 取值：'install'（界面该去装运行端）/ 'open'（界面该打开 url）/ ''（无事可做）。
    """
    level=verdict(state)
    if level=='ready':
        browser='Google Chrome 已安装' if state.get('chrome_installed') else '浏览器已连接'
        detail=f"{browser} ｜ 运行端 {state.get('version','?')} ｜ 扩展 {state.get('extension_version','?')} 已连接"
        if state.get('version') and state.get('extension_version') \
                and str(state['version']).lstrip('v')!=str(state['extension_version']).lstrip('v'):
            detail+=f"（版本不一致，可用 kimi-webbridge upgrade 升级）"
        return {'verdict':level,'title':'浏览器运行环境就绪','detail':detail,'action':'','url':''}
    if level=='chrome_missing':
        return {'verdict':level,'title':'未找到 Google Chrome',
                'detail':'请先安装 Google Chrome，再回到 APP 点击「重新检查」。APP 使用你安装的浏览器，不附带浏览器。',
                'action':'open','url':CHROME_DOWNLOAD}
    if level=='daemon_missing':
        return {'verdict':level,'title':'缺少运行端，需要先安装',
                'detail':'浏览器操作依赖本机的桥接服务（127.0.0.1:10086）。点右侧按钮由 APP 自动下载安装。',
                'action':'install','url':OFFICIAL}
    return {'verdict':level,'title':'缺少浏览器扩展，需要你手动安装',
            'detail':'点击右侧按钮，APP 会启动本机 Google Chrome 并打开扩展安装页。请在 Chrome 中完成安装，之后回到这里重新检查。',
            'action':'open','url':STORE}

def platform_key():
    """官方 OSS 里的平台目录名，如 darwin-arm64 / windows-amd64。"""
    if sys.platform=='win32':
        # Windows ARM64 can run an x64 app under emulation.  platform.machine()
        # reports the host's ARM64 in that case, while the executable needs
        # the windows-amd64 bridge binary.
        target=sysconfig.get_platform().lower()
        return 'windows-arm64' if 'arm64' in target else 'windows-amd64'
    system={'darwin':'darwin','win32':'windows'}.get(sys.platform,'linux')
    machine=platform.machine().lower()
    return f"{system}-{'arm64' if machine in ('arm64','aarch64') else 'amd64'}"

def fetch_release(timeout=15):
    """取当前版本的下载地址与 sha256。"""
    with urllib.request.urlopen(VERSION_API,timeout=timeout) as response:
        data=json.load(response)
    key=platform_key();info=(data.get('binaries') or {}).get(key)
    if not info:
        raise RuntimeError(f"官方接口里没有 {key} 的下载地址（可用：{list((data.get('binaries') or {}))}）")
    return data.get('version',''),info['url'],info.get('sha256','')

def download(dest=None,progress=None,timeout=120,release=None):
    """下载运行端并**校验 sha256**，返回 (版本, 落地路径)。

    官方安装脚本自己不校验哈希（下完直接 mv）。这是个要常驻的本地程序，装错比装不上更糟，
    所以这里校验接口给出的 sha256；不一致就丢掉、不落地。
    """
    version,url,want=release or fetch_release()
    if not want:
        raise RuntimeError('官方版本信息缺少 sha256，已停止安装运行端')
    dest=Path(dest or DAEMON_BIN);dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_name(dest.name+'.part')
    digest=hashlib.sha256();done=0;total=0;step=0
    with urllib.request.urlopen(url,timeout=timeout) as response,open(temp,'wb') as out:
        total=int(response.headers.get('Content-Length') or 0)
        if progress:progress(f"正在下载运行端 {version}（{total//1024} KB）")
        while True:
            chunk=response.read(65536)
            if not chunk:break
            out.write(chunk);digest.update(chunk);done+=len(chunk)
            if total and done*100//total>=step+25:
                step=(done*100//total)//25*25
                if progress:progress(f"正在下载运行端 {version}… {step}%")
    got=digest.hexdigest()
    if got!=want:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f'运行端安装包校验不通过（期望 {want[:16]}…，实际 {got[:16]}…），已丢弃')
    os.chmod(temp,0o755)
    temp.replace(dest)
    return version,dest

def start(binary=None,wait=25,progress=None):
    """启动运行端，并等它**真的起来**（以 /status 为准，不看进程退出码）。"""
    binary=Path(binary or DAEMON_BIN)
    if not binary.exists():raise RuntimeError(f'找不到运行端程序：{binary}')
    try:
        subprocess.Popen([str(binary),'start'],stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,start_new_session=True)
    except OSError as exc:
        raise RuntimeError(f'启动运行端失败：{exc}') from None
    deadline=time.time()+wait
    while time.time()<deadline:
        if probe(timeout=2).get('daemon'):return True
        time.sleep(1)
    if progress:progress(f'运行端没在 {wait} 秒内就绪，可查看日志：{DAEMON_LOG}')
    return False

def install(progress=None,start_service=True):
    """装运行端：下载 → 校验 → 落地 → 启动。返回 (版本, 路径)。"""
    release=fetch_release()
    version,path=download(progress=progress,release=release)
    if progress:progress(f'运行端 {version} 已安装到 {path}（校验通过）')
    if start_service:
        if start(path,progress=progress):
            if progress:progress('运行端已启动')
        else:
            raise RuntimeError(f'运行端已安装但没能启动，请查看日志：{DAEMON_LOG}')
    return version,path

def ensure(progress=None,install_missing=True):
    """自检；缺运行端且允许时自动装。返回 (是否就绪, 状态)。扩展缺失只报告、不处理。"""
    state=probe()
    if verdict(state)=='daemon_missing' and install_missing:
        install(progress=progress)
        state=probe()
    return verdict(state)=='ready',state

if __name__=='__main__':
    # 排障用：直接跑这个文件就能看到缺什么、以及该执行哪条命令。
    state=probe()
    info=describe(state)
    print(f"[{info['verdict']}] {info['title']}")
    print(f"  {info['detail']}")
    if info['verdict']!='ready':
        print(f"  运行端手动安装：{install_command()}")
        print(f"  官方页面：{OFFICIAL}")
