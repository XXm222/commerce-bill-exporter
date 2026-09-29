"""Windows 当前用户 DPAPI 凭据；每个账号独立 Chrome 数据目录。"""
import base64, ctypes, datetime as dt, json, os, subprocess, sys, uuid
from pathlib import Path
from ctypes import wintypes

class Blob(ctypes.Structure):
    _fields_ = [('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def crypt(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('账号密码保存需要 Windows 系统')
    data=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(data,ctypes.POINTER(ctypes.c_ubyte)))
    result=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data,result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data,ctypes.c_void_p))

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)

# APP 启动时读状态文件遇到的问题，按发生顺序记在这里，由界面如实说一句。
# 做成模块级列表是因为读取发生在界面构造之前（Store / Engine 都在 App.__init__ 里建），
# 那时还没有任何可以报信的地方。
STARTUP_PROBLEMS=[]

def load_json(path,default,what):
    """读一个 JSON 状态文件；**文件损坏时不能让 APP 起不来**。

    背景很具体：accounts.json / history.json / settings.json 只要有一个损坏（空文件、
    被截断、被别的程序改坏、从坏掉的备份恢复回来），此前 APP 会**完全打不开**——
    启动时抛 JSONDecodeError，界面上只弹出一句英文（`Expecting value: line 1 column 1`）。
    用户既看不懂，也没法在界面里自救：唯一的出路是自己找那个文件删掉，而记录也跟着没了。
    对一个按月使用的桌面工具来说，这种「因为一个状态文件坏了就彻底用不了」是不能接受的。

    所以这里改成：损坏的文件**改名留证**（不删、不覆盖），以默认值继续启动，
    并把发生了什么写进 STARTUP_PROBLEMS，由界面说一句人话。
    留下的那份 `.bad-<时间戳>` 是给用户（或以后的我）修数据用的——它可能就是全部账号记录。
    """
    path=Path(path)
    if not path.exists():return default
    try:
        return json.loads(path.read_text('utf8'))
    except (OSError,ValueError) as exc:
        stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        keep=path.with_name(f'{path.name}.bad-{stamp}')
        try:
            path.replace(keep);placed=f'已把损坏的文件改名为 {keep.name} 留着'
        except OSError:
            placed='（改名也没成功，文件还在原处）'
        STARTUP_PROBLEMS.append(f'{what}读不出来（{exc}）；{placed}。本次以空记录启动，可以继续使用。')
        return default

def keychain_write(account_id,password):
    """安全写入 macOS 钥匙串：先写临时条目校验，确认能原样读回，再覆盖正式条目。

    直接覆盖正式条目有两个坑：一是实测含非 ASCII 的密码读回的是十六进制串，
    写进去就取不回原文；二是失败后旧密码已被覆盖、再删掉就等于把用户原本
    能用的密码弄丢了。所以先拿一个一次性服务名做往返校验，通过才动真条目。
    """
    probe=f'CommerceBillExport.probe.{uuid.uuid4().hex}'
    add=['/usr/bin/security','add-generic-password','-U','-a',account_id,'-s',probe,'-w']
    payload=(password+'\n'+password+'\n').encode('utf8')
    if subprocess.run(add,input=payload,capture_output=True).returncode:
        raise RuntimeError('无法写入 macOS 钥匙串，请允许电商账单访问钥匙串')
    try:
        got=subprocess.run(['/usr/bin/security','find-generic-password','-a',account_id,'-s',probe,'-w'],capture_output=True)
        stored=got.stdout.decode('utf8').rstrip('\n') if got.returncode==0 else None
    finally:
        subprocess.run(['/usr/bin/security','delete-generic-password','-a',account_id,'-s',probe],capture_output=True)
    if stored!=password:
        raise RuntimeError('密码无法原样存入系统钥匙串（通常是含非 ASCII 字符），请改用纯 ASCII 字符的密码后重试')
    real=['/usr/bin/security','add-generic-password','-U','-a',account_id,'-s','CommerceBillExport','-w']
    if subprocess.run(real,input=payload,capture_output=True).returncode:
        raise RuntimeError('无法把密码保存到 macOS 钥匙串，请允许电商账单访问钥匙串')

class Store:
    def __init__(self,folder):
        self.folder=Path(folder);self.path=self.folder/'accounts.json'
        self.items=load_json(self.path,[],'账号记录')
    def save(self):
        atomic(self.path,self.items)
    def put(self,platform,name,username,password,account_id=None):
        if not name.strip(): raise ValueError('请填写账号名称')
        row=next((a for a in self.items if a['id']==account_id),None)
        created=row is None
        if created:
            row={'id':uuid.uuid4().hex,'platform':platform,'shops':[],'allShops':True,'selectedShops':[],'enabled':True}
            self.items.append(row)
        try:
            if row['platform'] != platform: raise ValueError('账号平台不可更改')
            if row.get('username') and row['username']!=username.strip():
                row.pop('identity',None);row.update(shops=[],selectedShops=[],allShops=True)
            row.update(name=name.strip(),username=username.strip())
            if password:
                if sys.platform=='darwin':
                    # 密码经 stdin 传入，不出现在命令行参数里：security 自带帮助也写明
                    # 用 -w <password> 是不安全的，同用户的任何进程都能从 ps 看到。
                    keychain_write(row['id'],password)
                    row['keychain']=True;row.pop('secret',None)
                else:row['secret']=base64.b64encode(crypt(password.encode('utf8'))).decode('ascii')
        except Exception:
            # 失败时不能留下半截记录：否则界面报错、列表里却已经多出一条，
            # 用户再点一次保存就会生成第二条。
            if created and row in self.items:self.items.remove(row)
            self.save()
            raise
        self.save();return row
    def password(self,account):
        try:
            if sys.platform=='darwin':
                if not account.get('keychain'):return ''
                r=subprocess.run(['/usr/bin/security','find-generic-password','-a',account['id'],'-s','CommerceBillExport','-w'],capture_output=True)
                if r.returncode:raise RuntimeError()
                return r.stdout.decode('utf8').rstrip('\n')
            return crypt(base64.b64decode(account['secret']),True).decode('utf8') if account.get('secret') else ''
        except Exception:
            raise RuntimeError('无法读取此账号密码，请在当前系统用户下重新保存密码') from None
    def forget_password(self,account):
        if sys.platform=='darwin' and account.get('keychain'):
            r=subprocess.run(['/usr/bin/security','delete-generic-password','-a',account['id'],'-s','CommerceBillExport'],capture_output=True)
            if r.returncode not in (0,44):raise RuntimeError('钥匙串密码移除失败')
        account.pop('secret',None);account.pop('keychain',None);self.save()
