"""串行导出、恢复平台异步任务、校验原始数据和 Excel。"""
import csv, datetime as dt, hashlib, json, re, shutil, sys, time, uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from accounts import atomic, load_json
from webbridge import Chrome, TMALL_LOGIN_URL
import jst_export as jst
import tmall_export as tmall
import tmall_bridge
from tables import read_table
import xlsx_writer
from workbook import prepare_sheets, source_order_counts, verify_xlsx

PLATFORMS={'jst':'聚水潭','tmall':'天猫'}
BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
# 工作簿由进程内的流式写入器生成，不再启动 Node 子进程。

# 退出登录的人工提示。两个平台的页面都没有退出入口（2026-09-27 在真实页面实测：
# 天猫商家后台整页 27 个链接、0 个悬停目标、0 个含「退出/注销/登出」的元素；
# 聚水潭 ss.erp321.com 的报表页同样为 0/0/0，连更宽的候选元素扫描也找不到用户头像）。
# 所以不能沿用「点击平台的退出登录」——那会让用户去找一个不存在的按钮。
LOGOUT_HINTS={
    'default':'请在 Chrome 点击平台的「退出登录」，完成后点「已退出，继续」。APP 会核对退出状态。',
    'jst':'聚水潭这个报表页面上没有退出登录入口（已在真实页面核实）。需要换账号时，请先在 Chrome 中退出聚水潭账号，再点「已退出，继续」；APP 会核对退出状态，未确认退出时不会继续处理下一个账号。只用一个账号时可以不退出，登录态会保留，下次导出无需重新登录。',
    'tmall':'天猫商家后台没有退出入口（已在真实页面核实）。请在 Chrome 里打开 taobao.com 从账号菜单退出，完成后点「已退出，继续」。APP 会核对退出状态；只用一个店铺时也可以不退出，登录态会保留，下次导出无需重新登录。',
}

def session_already_open(browser):
    """判断浏览器里是否已经登着某个天猫账号。

    天猫商家后台没有可判断「登的是哪个账号」的稳定元素，但「在不在登录页」是可判断的：
    登录页是 loginmyseller.taobao.com（is_logged_out 靠 URL 里的 login 判为未登录），
    商家后台则是 myseller.taobao.com 且页面无密码框。任何异常都按「不确定」处理，
    宁可回到通用提示，也不要凭猜测吓唬用户。
    """
    try:
        host=(browser.evaluate('location.hostname').get('value') or '')
    except Exception:
        return False
    if not str(host).endswith('taobao.com'):
        return False
    try:
        return not browser.is_logged_out()
    except Exception:
        return False

def next_same_platform(accounts,index):
    """队列里第 index 个账号之后，是否还有同一平台的账号。

    有就得先退出当前登录态：Chrome 配置目录是所有账号共用的（<数据目录>/chrome-profile），
    同一平台连着导出两个账号时，第二个会直接沿用第一个的登录态。没有就不必退出——保留
    登录态可以省掉下次登录，也不必弹一个没有下一步的确认框。
    """
    platform=accounts[index]['platform']
    return any(a['platform']==platform for a in accounts[index+1:])

def cached_sources(source_dir,key,month):
    """任务里已下载的原始明细文件。

    只认平台会产出的表格类型，并按类型收窄范围：同一前缀下还有中间产物与工作簿草稿，
    靠前缀 glob 取第一个会取到 .jsonl 这种东西。
    """
    kinds={'.csv','.xlsx','.xlsm','.xls','.txt'}
    return sorted(p for p in source_dir.glob(f'{key}-{month}.*') if p.suffix.lower() in kinds)

def ensure_writable(folder):
    """在动平台之前确认保存位置可写。

    归档发生在登录、提交导出、下载之后；如果到那时才发现目录不可写，就白白消耗了
    一次平台导出（聚水潭的异步导出是真实占额度的）。所以在建任务前先探一次。
    """
    try:
        folder.mkdir(parents=True,exist_ok=True)
        probe=folder/'.commercebill-write-probe'
        probe.write_text('ok',encoding='utf8')
        probe.unlink()
    except OSError as exc:
        reason=exc.strerror or exc.__class__.__name__
        raise RuntimeError(f'保存位置不可写：{folder}（{reason}）') from None

def check_claim(rows,headers,flow,month,claim,label=''):
    """核对「页面自己说的数字」与「下载到的文件」是否一致。

    聚水潭那条链路一直有这道核对（查询 N 单 vs 导出 M 单，见 workbook.prepare_sheets）；
    天猫这边此前没有——页面月汇总那一行其实写着明细笔数与收入金额，真实样本已经对过
    （202608：页面 953 笔 / 21,025.78，下载文件 953 行、收入合计 21,025.78）。

    只在**声明确实属于这一次、这一份**时才核对（key 与 month 都对得上）：
    上一轮下载留下的声明如果被拿去核对下一轮，会把一次正常导出判成失败。
    """
    claims = (flow or {}).get('claims') or {}
    if not claim or not claims:
        return
    if claim.get('key') != (flow or {}).get('key') or claim.get('month') != month:
        return                                  # 不属于这一份，宁可不错杀
    label = label or ''
    declared = claim.get('rows')
    if declared is not None:
        digits = re.sub(r'\D','',str(declared))
        if digits and int(digits) != len(rows):
            raise RuntimeError(f'{flow["name"]}{label}：页面上写的是 {digits} 笔明细，'
                               f'下载到的文件里是 {len(rows)} 行，两者不一致，已停止导出。'
                               f'请重新下载该月明细；若反复不一致，请核对天猫页面结构')
    income = claim.get('income')
    column = claims.get('incomeColumn')
    if income and column in headers:
        index = headers.index(column)
        total = Decimal('0')
        for row in rows:
            value = (row[index] if index < len(row) else '').strip().replace(',','')
            if not value:
                continue
            try:
                total += Decimal(value)
            except InvalidOperation:
                raise RuntimeError(f'{flow["name"]}{label}：明细里有读不出金额的取值「{value[:20]}」，'
                                   f'无法与页面上的合计核对，已停止导出') from None
        wanted = Decimal(str(income).replace(',','').strip() or '0')
        if total != wanted:
            raise RuntimeError(f'{flow["name"]}{label}：页面上的{claims["income"]}是 {wanted}，'
                               f'下载到的文件里合计是 {total}，两者不一致，已停止导出。'
                               f'请重新下载该月明细；若反复不一致，请核对天猫页面结构')


def archive(draft,target):
    """把产物从任务目录复制到保存位置。

    ensure_writable 已经在动平台之前探过一次可写，但**中途**仍可能写不进去：磁盘在导出期间
    被占满、目标目录被同步软件锁住、U 盘写保护等等。而这一步发生在**平台导出已经消耗之后**，
    所以失败时必须讲清楚三件事：写不进的是哪个目录、可能是为什么、以及产物还在（换位置能重试）。
    OSError 的原话是一句英文加 errno（`[Errno 13] Permission denied: /…/out.part`），
    用户既看不出发生了什么，也不知道已经白跑了一趟平台导出。

    失败时不留下半截 `.part`（与写入器 xlsx_writer 的做法一致）：先写 `.part` 再改名，
    中途失败就把 `.part` 删掉。否则用户的保存目录里会攒下一堆大小不一的 `.part`，
    而下一次成功导出只会覆盖同名的那一个。
    """
    partial=target.with_suffix('.part')
    try:
        shutil.copy2(draft,partial)
        partial.replace(target)
    except OSError as exc:
        reason=exc.strerror or exc.__class__.__name__
        try:partial.unlink(missing_ok=True)
        except OSError:pass
        raise RuntimeError(f'写不进保存位置：{target.parent}（{reason}）。'
                           f'已下载的明细仍保留在任务目录，换一个保存位置或清理磁盘后可以重试。') from None

def readable_error(exc):
    """把异常转成给用户看的文字。

    项目的约定是「中文说明 +（原始原因）」：原始信息保留（便于上报），但正文必须是用户看得懂、
    能照着做的一句话（ensure_writable / archive 就是这么写的）。
    这里统一兜住 **OSError 这一类穿透**：磁盘满、目录只读、文件被占用——它们可能出现在导出链路
    的任何一步（写草稿、保存下载、归档、清理），原话都是英文加 errno。逐个调用点包一遍容易漏，
    所以在「异常要变成界面文字」这道口子上统一转。已经写好的中文消息（RuntimeError）原样通过。
    """
    if isinstance(exc,OSError):
        reason=exc.strerror or exc.__class__.__name__
        return f'读写文件失败：{reason}（{exc.filename or "路径未知"}）。请检查磁盘空间与目录权限后重试。'
    return str(exc)

def parse_date(value):
    """平台时间文本 -> 写入器需要的 {'date': ISO}；识别不了返回 None（原样保留文本）。"""
    for pattern in ('%Y-%m-%d %H:%M:%S','%Y/%m/%d %H:%M:%S','%Y-%m-%d','%Y/%m/%d'):
        try:
            moment=dt.datetime.strptime(value,pattern)
            return {'date':moment.strftime('%Y-%m-%dT%H:%M:%SZ')}
        except ValueError:
            pass
    return None

def cell_number(value):
    """数值列转换：超过 15 位有效数字返回 None，由调用方保留文本。

    Excel 只有 15 位有效数字，19 位订单号按数字写入会被改写成近似值。
    """
    try:
        number=Decimal(value)
    except InvalidOperation:
        return None
    if not number.is_finite() or len(number.as_tuple().digits)>15:
        return None
    return int(number) if number==int(number) else float(number)

def validate_dates(start,end):
    try:
        first,last=dt.date.fromisoformat(start),dt.date.fromisoformat(end)
        if first>last:raise ValueError()
    except ValueError:raise ValueError('日期请填写 YYYY-MM-DD，开始日期不能晚于结束日期') from None
    return first,last

def export_period(platform,start,end):
    """APP 日期保留原值；天猫账单覆盖这些日期涉及的完整月份。"""
    first,last=validate_dates(start,end)
    if platform!='tmall':return {'start':start,'end':end,'months':[]}
    first=first.replace(day=1);months=[];cursor=first
    while cursor<=last:
        months.append(cursor.strftime('%Y-%m'))
        cursor=dt.date(cursor.year+1,1,1) if cursor.month==12 else dt.date(cursor.year,cursor.month+1,1)
    return {'start':first.isoformat(),'end':(cursor-dt.timedelta(days=1)).isoformat(),'months':months}

def jst_month_periods(start,end):
    """聚水潭单次查询最多跨一个自然月；保留用户选定的首尾日期。"""
    first,last=validate_dates(start,end)
    periods=[]
    while first<=last:
        next_month=(dt.date(first.year+1,1,1) if first.month==12 else
                    dt.date(first.year,first.month+1,1))
        finish=min(last,next_month-dt.timedelta(days=1))
        periods.append((first.isoformat(),finish.isoformat()))
        first=finish+dt.timedelta(days=1)
    return periods


def jst_source_periods(folder,task):
    """已下载的月度原文件及其查询数；旧版单文件任务仍按原范围处理。"""
    segments=task.get('segments') or {}
    if segments:
        return [(month,segment['start'],segment['end'],segment['orderCount'],folder/f'source-{month}.csv')
                for month,segment in sorted(segments.items()) if segment.get('orderCount',0)>0]
    return [(task['start'][:7],task['start'],task['end'],task['orderCount'],folder/'source.csv')]


def confirmed_pdd_warnings(periods,task,shops,query_pdd):
    """仅在其他平台订单数完全对上时，把拼多多缺口转成提醒。"""
    selected=set(map(str,task.get('selectedShops',[])))
    pdd_ids={str(shop['id']) for shop in shops
             if (str(shop.get('platform','')).lower()=='pdd' or
                 '拼多多' in str(shop.get('platform','')) or
                 str(shop.get('name','')).startswith('【拼多多】'))
             and (task.get('allShops') or str(shop['id']) in selected)}
    warnings=[]
    for month,start,end,expected,source in periods:
        if not source.is_file():continue
        pdd_actual,other_actual=source_order_counts(source,pdd_ids)
        if pdd_actual+other_actual>=expected or not pdd_ids:continue
        pdd_query=query_pdd(start,end,sorted(pdd_ids))
        if (pdd_query>pdd_actual and expected-pdd_query==other_actual and
                expected-pdd_actual-other_actual==pdd_query-pdd_actual):
            warnings.append({'month':month,'pddQuery':pdd_query,'pddExported':pdd_actual,
                             'missing':pdd_query-pdd_actual})
    return warnings

def merge_jst_sources(sources,target):
    """把逐月原始 CSV 合成一份；列结构变化时不覆盖已有完整文件。"""
    partial=target.with_suffix('.part')
    headers=None
    try:
        with partial.open('w',encoding='utf-8-sig',newline='') as output:
            writer=csv.writer(output)
            for source in sources:
                with source.open('r',encoding='utf-8-sig',newline='') as input_file:
                    reader=csv.reader(input_file)
                    current=next(reader,None)
                    if not current:raise RuntimeError('聚水潭月度明细为空，不能合并')
                    if headers is None:
                        headers=current;writer.writerow(headers)
                    elif current!=headers:
                        raise RuntimeError('聚水潭各月明细的列名不同，已停止合并，原始文件已保留')
                    for row in reader:
                        if len(row)!=len(headers):raise RuntimeError('聚水潭月度明细列数不一致，已停止合并')
                        writer.writerow(row)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)

def safe_name(name):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')[:64]
    return value or '账号'

def jst_filename(scope,start,end,task_id):
    """聚水潭产物名：带任务号，所以**同一段时间重复导出会得到多个文件**，互不覆盖。

    这是有意的。聚水潭的导出是异步任务，平台侧按任务保留记录，重新导一次会得到一个全新的任务；
    产物名跟着任务号走，用户手上就有两份可以对照的文件（也能看出哪次是哪次）。
    代价是重复导出会攒文件，所以界面上有导出记录可以回看。
    """
    return f"聚水潭_{scope}_{start}_{end}_{task_id[:6]}.xlsx"

def tmall_filename(flow_name,month):
    """天猫产物名：只有「来源 + 月份」，**没有任务号**，所以同店同月重复导出是就地覆盖。

    这同样是有意的，而且与聚水潭相反。天猫这两个报表的粒度就是「账号 + 月份」，
    重跑同一个月要的是「把这份月度明细更新成最新的一份」，不是攒出两份内容几乎一样、
    却不知道该信哪一份的文件（页面上也只会保留一条该月的导出记录）。
    覆盖是先写 `.part` 再改名（见 archive），所以写到一半失败不会把旧的那份弄坏。
    """
    return f"{flow_name}_{month}.xlsx"

class Engine:
    def __init__(self,data,store,notify,ask,chrome_path=''):
        self.data=Path(data);self.store=store;self.notify=notify;self.ask=ask;self.chrome_path=chrome_path
        self.path=self.data/'history.json';self.tasks=load_json(self.path,[],'导出记录')
        self.browser=None;self.busy=False;self.cancelled=False
        for task in self.tasks:
            if task['status']=='running':task.update(status='failed',stage='上次运行中断，可继续')
        self.save()
    def save(self):atomic(self.path,self.tasks)
    def update(self,task,**values):
        task.update(values);self.save();self.notify('tasks',None)
        self.notify('stage',task.get('stage',''))
    def stop(self):self.cancelled=True
    def checkpoint(self):
        if self.cancelled:raise RuntimeError('已停止；已下载文件和平台任务已保留，可继续')
    def open_account(self,account):
        self.checkpoint()
        browser=Chrome(self.data,account,self.chrome_path);browser.start();self.browser=browser
        if account['platform']=='tmall':
            # 退出登录后账单页会重定向到登录页，店铺名永远等不来；原来的写法会把
            # 15 秒等满——切换账号时用户看着登录页白停七八秒就是这里。先认登录页，
            # 认出即返回空店铺，直接进入自动登录流程。
            def shop_or_login():
                try:
                    if browser.is_logged_out():
                        return 'login'
                    return browser.current_shop() or None
                except RuntimeError:
                    return None
            state=browser.wait(shop_or_login,15) or ''
            shop='' if state=='login' else state
            if shop != account['name']:
                if shop:
                    # A known different shop must be logged out before typing
                    # the next account's credentials into the same Chrome.
                    tmall.logout(browser)
                    shop=''
                password=self.store.password(account)
                saved_credentials=bool(account.get('username') and password)
                filled={'filled':False,'submitted':False,'reason':'missing_credentials'}
                if saved_credentials:
                    # The myseller login shell embeds a cross-origin iframe.
                    # Its fields are inaccessible to the bridge's top-document
                    # actions.  The direct Taobao login page is a first-party
                    # form in this same Chrome session.
                    browser.open_url(TMALL_LOGIN_URL)
                    if browser.wait_for_login_form():
                        filled=browser.autofill(account['username'],password)
                password=''
                if filled['submitted']:
                    self.notify('stage',f'已自动提交「{account["name"]}」的登录，等待页面确认')
                    def login_left():
                        href=browser.js('location.href') or ''
                        host=urlparse(href).hostname or ''
                        return (host and not any(host==h or host.endswith('.'+h)
                                                 for h in tmall.LOGIN_HOSTS)
                                and not re.search(r'captcha|punish|verify|security',href,re.I))
                    if browser.wait(login_left,45):
                        browser.open_url(tmall.FLOWS['bill_income']['url'])
                        shop=browser.wait(browser.current_shop,15) or ''
                if shop and shop != account['name']:
                    raise RuntimeError(f'天猫店铺未核对通过：期望「{account["name"]}」，页面读到「{shop}」。已停止，避免账单归错店铺')
                if not shop:
                    if saved_credentials:
                        failure={
                            'consent_failed':'登录页协议勾选未成功',
                            'consent_unresolved':'登录页协议勾选框结构已变化，未能安全确认勾选',
                            'button_missing':'没有找到唯一的登录按钮',
                            'form_missing':'没有找到登录表单',
                            'form_changed':'勾选协议后登录表单发生变化',
                            'user_rejected':'登录账号未被页面接受',
                            'password_not_retained':'密码框没有完整保留自动输入的内容（尚未提交登录）',
                            'overwritten':'浏览器自动填充覆盖了保存的凭据',
                            'unsafe_host':'登录页跳到了非淘宝域名',
                            'input_failed':'浏览器输入未成功',
                        }
                        detail=(failure.get(filled.get('reason'),'提交后未确认登录成功')
                                if not filled['submitted'] else '提交后未确认登录成功')
                        diagnostic = filled.get('diagnostic') or {}
                        input_failures = {
                            'field_changed': '登录输入框已刷新或不可编辑',
                            'field_length_limit': '保存内容超过网页输入框长度限制，未截断、未提交',
                            'field_unstable': '浏览器自动填充或页面持续修改输入框',
                            'focus_lost': '输入焦点被其他控件或浏览器弹层移走',
                            'selection_failed': '未能全选输入框原有内容，未提交',
                            'input_overwritten': '输入后内容又被浏览器或页面覆盖',
                            'input_truncated': '输入未完整送达或被网页截短',
                            'input_mismatch': '输入结果与保存内容不一致',
                            'input_transport': '浏览器输入通道失败，请在设置中检查运行端和扩展',
                            'unsafe_host': '登录页已跳转，停止输入账号密码',
                        }
                        if not filled['submitted'] and diagnostic.get('code') in input_failures:
                            field_name = '密码框' if diagnostic.get('field') == 'password' else '账号框'
                            detail = field_name + '：' + input_failures[diagnostic['code']]
                            expected, actual = diagnostic.get('expectedLength'), diagnostic.get('actualLength')
                            method = {'keyboard':'逐字输入', 'browser_text':'浏览器文本输入',
                                      'field_fill':'控件直接填写'}.get(diagnostic.get('method'))
                            if (type(expected) is int and type(actual) is int and method):
                                detail += f'（预期 {expected} 字符，读回 {actual} 字符；{method}）'
                        raise RuntimeError(f'「{account["name"]}」自动登录未完成：{detail}。已停止，避免把账单归到错误店铺')
                    self.ask(f'该店铺没有保存完整的账号密码，请在 Chrome 登录「{account["name"]}」。'
                             '登录完成后点「已登录，继续」。',account['name'])
                    browser.open_url(tmall.FLOWS['bill_income']['url'])
                    shop=browser.wait(browser.current_shop,15) or ''
            if shop != account['name']:
                raise RuntimeError(f'天猫店铺未核对通过：期望「{account["name"]}」，页面读到「{shop or "空"}」。已停止，避免账单归错店铺')
            return browser,None,None
        adapter=jst.Browser(browser.rpc)
        try:
            adapter.open()
        except RuntimeError as exc:
            if '登录' not in str(exc) and '页面未准备好' not in str(exc):raise
            password=self.store.password(account)
            if account.get('username') and password:
                browser.wait_for_login_form()
                filled=browser.autofill(account['username'],password)
            else:
                filled={'filled':False}
            password=''
            message='请在 Chrome 完成聚水潭登录，再点「已登录，继续」。'
            if filled['filled']:message='已填入保存的账号密码，请在 Chrome 点击登录并完成验证，再点「已登录，继续」。'
            self.ask(message,account['name'])
            browser.open_url(jst.PAGE)
            adapter.open()
        self.checkpoint()
        identity=adapter.api('account',{'data':{}})['data']
        if not identity.get('companyId'):raise RuntimeError('未检测到聚水潭登录，请重新登录')
        if account.get('identity') and account['identity']!=identity:
            raise RuntimeError('当前登录账号与保存账号不一致，请退出后登录正确账号')
        # 首次给账号绑定身份前先认一下：配置目录是所有账号共用的，浏览器里可能还留着上一个
        # 账号的登录态。若当前登录的公司已经绑在别的账号上，接下来导出的很可能就是那个公司的
        # 数据——文件名、月份、行数都会正常，只有归属是错的。这里点名问一句而不是直接报错：
        # 同一家公司的两个登录（主账号/子账号）是正当用法，硬报错会让第二个账号永远绑不上身份。
        other=next((a for a in self.store.items if a['platform']=='jst' and a['id']!=account['id']
                    and a.get('identity') and a['identity'].get('companyId')==identity.get('companyId')),None)
        if other and not account.get('identity'):
            self.ask(f'Chrome 里登录的聚水潭公司，已经绑在账号「{other["name"]}」上。\n'
                     f'如果「{account["name"]}」与它是同一家公司的另一个登录，点「已登录，继续」即可；\n'
                     f'如果不是，请先在 Chrome 中退出聚水潭账号，再登录「{account["name"]}」，然后继续。',
                     account['name'])
        account['identity']=identity;self.store.save()
        return browser,adapter,identity
    def logout(self,browser,account):
        """退出当前平台账号（换账号之前必须做，否则下一个会沿用它的登录态）。

        平台自己确认退出就收工；没确认就人工确认一次，**并且仍然要核对退出状态**——
        配置目录是所有账号共用的，没退出就继续的话，第二个账号会在第一个的登录态里导出，
        文件名、月份、行数全都正常，只有归属是错的。
        """
        self.notify('stage',f"退出账号：{account['name']}")
        confirmed=browser.logout()
        if not confirmed:
            self.ask(LOGOUT_HINTS.get(account['platform'],LOGOUT_HINTS['default']),account['name'],logout=True)
            if not browser.is_logged_out():
                raise RuntimeError(f"「{account['name']}」未确认退出登录，已停止。"
                                   f"请先在 Chrome 中退出该账号，再继续下一个")
        browser.close()
    def logout_for_switch(self,browser,account):
        """换账号前的退出：天猫走**自动退出**，聚水潭保持原来的行为（那边已经验证过了）。

        天猫商家后台没有退出入口、账号区的悬停菜单也不是普通 DOM（实测按店铺名找不到可点元素），
        所以走淘宝固定的登出地址，并且回读确认；自动退出失败就退回人工确认——
        宁可多问一句，也不能带着上一个账号的登录态去导下一个账号的数据。
        """
        if account['platform']!='tmall':
            self.notify('stage',f"退出账号：{account['name']}")
            confirmed=browser.logout()
            if not confirmed:
                self.ask(LOGOUT_HINTS.get(account['platform'],LOGOUT_HINTS['default']),account['name'],logout=True)
                if not browser.is_logged_out():
                    raise RuntimeError(f"「{account['name']}」未确认退出登录，已停止。"
                                       f"请先在 Chrome 中退出该账号，再继续下一个")
            browser.close()
            return
        self.notify('stage',f"退出账号：{account['name']}")
        try:
            tmall.logout(browser)
            self.notify('stage',f"已退出账号：{account['name']}")
        except Exception as exc:
            self.ask(f'自动退出天猫账号没有成功（{exc}）。\n'
                     f'请在 Chrome 中退出当前账号，再点「已退出，继续」。',account['name'],logout=True)
            if not browser.is_logged_out():
                raise RuntimeError(f'「{account["name"]}」未确认退出，已停止切换账号')
        self.checkpoint()
    def release(self,browser,account,next_account):
        """收尾浏览器。只有同一平台上还有下一个账号时才需要退出登录。

        Chrome 配置目录是所有账号共用的（<数据目录>/chrome-profile），登录态会串号：
        同一平台连着导出两个账号时，第二个会直接沿用第一个的登录态，数据归属就错了，
        所以必须先把当前账号退掉。平台上没有下一个账号时不必退出——直接关窗口保留登录态，
        与设置里「登录态默认保留」的说明一致，也省掉一次没有下一步的确认框：聚水潭的
        确认框出现在生成 Excel 之前，用户点「取消」会把已经下载好的任务判成失败。
        """
        # 换账号：天猫走自动退出（平台菜单/登出地址 + 回读确认），聚水潭保持原样。
        if next_account:self.logout_for_switch(browser,account)
        browser.close();self.browser=None
    def check_login(self,account):
        try:
            browser,_,_=self.open_account(account)
            if account['platform']=='tmall':
                # 淘宝登录成本高（滑块/短信二次验证），检查登录后保留登录态供导出复用。
                self.notify('stage','天猫登录检查通过，已保留登录态')
                return
            self.notify('stage','登录检查通过，正在退出平台账号')
            try:
                self.logout(browser,account)
                self.notify('stage','登录检查通过，已退出平台账号')
            except Exception as exc:
                # 该平台页面上没有退出入口时退出必然失败（两个平台都已实测），但这不代表
                # 登录检查失败——把它报成「未完成」会让用户以为凭据有问题。退出结果照实说。
                self.notify('stage',f'登录检查通过；未能自动退出平台账号（{exc}）。登录态已保留。')
        finally:
            if self.browser:self.browser.close()
            self.browser=None
    def create(self,account,start,end,output,all_shops,selected):
        period=export_period(account['platform'],start,end)
        if account['platform']!='jst' and not period['months']:raise ValueError('天猫任务缺少账单月份')
        if account['platform']=='jst' and not all_shops and not selected:raise ValueError(f"{account['name']}：请至少选择一家店铺")
        ensure_writable(Path(output))
        task={'id':uuid.uuid4().hex,'accountId':account['id'],'accountName':account['name'],
              'platform':account['platform'],'start':period['start'],'end':period['end'],'requestedStart':start,'requestedEnd':end,'billingMonths':period['months'],'dateField':'send_date',
              'allShops':all_shops,'selectedShops':[str(x) for x in selected],
              'output':str(output),'createdAt':dt.datetime.now().isoformat(timespec='seconds'),
              'status':'queued','stage':'等待导出','rows':0,'sheetCount':0,'orderCount':0}
        self.tasks.insert(0,task);self.save();return task
    def download_jst_months(self,task,adapter,folder,selected):
        """逐月查询与下载；每次提交的任务号立刻落盘，续跑不重复占用导出额度。"""
        periods=jst_month_periods(task['start'],task['end'])
        segments=task.setdefault('segments',{})
        sources=[]
        for start,end in periods:
            self.checkpoint()
            key=start[:7]
            segment=segments.setdefault(key,{'start':start,'end':end})
            if (segment.get('start'),segment.get('end'))!=(start,end):
                raise RuntimeError(f'{key} 月度任务日期与原任务不一致，已停止导出')
            source=folder/f'source-{key}.csv'
            if source.exists():
                if not isinstance(segment.get('orderCount'),int) or segment['orderCount']<=0:
                    raise RuntimeError(f'{key} 已下载文件缺少对应订单数，已停止合并')
                sources.append(source)
                continue
            if segment.get('orderCount')==0:
                continue
            c=jst.condition(start,end,0,'send_date',1,True)
            c['shop']=[] if task['allShops'] else [int(s) for s in selected]
            if not segment.get('taskId') and not segment.get('downloadUrl'):
                if segment.get('submissionUnknown'):
                    raise RuntimeError(f'{key} 上次提交结果未知，请到聚水潭异步导出管理核查，避免重复提交')
                self.update(task,stage=f'聚水潭 {start} 至 {end}：查询订单',segments=segments)
                count=jst.query(adapter,c)
                segment['orderCount']=count
                self.update(task,stage=f'聚水潭 {start} 至 {end}：查到 {count:,} 单',
                            orderCount=sum(s.get('orderCount',0) for s in segments.values()),segments=segments)
                if not count:continue
                export_c={**c,'exportType':'pforderdetails','isAds':False,'isExport':True,
                          'untype':'pforderprofit_orderprofitbyoid','isExportCond':False,'shopCategoryId':0}
                segment['submissionUnknown']=True
                self.update(task,stage=f'聚水潭 {start} 至 {end}：提交 {count:,} 单商品数据',segments=segments)
                result=adapter.api('export',{'data':{'exportCondition':json.dumps(
                    {'isAds':False,'condition':export_c},ensure_ascii=False)}})['data']
                if not isinstance(result,dict) or result.get('isSuccess') is not True:
                    segment['submissionUnknown']=False
                    self.update(task,segments=segments)
                    raise RuntimeError(f'{key} 平台拒绝导出，请检查权限或导出额度')
                segment.update(submissionUnknown=False,taskId=result.get('taskId',0),
                               downloadUrl=result.get('url'),exportType=result.get('exportType',0))
                self.update(task,segments=segments)
            url=segment.get('downloadUrl')
            if segment.get('taskId'):
                self.update(task,stage=f'聚水潭 {start} 至 {end}：等待生成文件（{segment["orderCount"]:,} 单，可继续）')
                if not url:
                    url=self.wait_jst_url(adapter,segment['taskId'])
                    segment['downloadUrl']=url
                    self.update(task,segments=segments)
            if not url:raise RuntimeError(f'{key} 平台未返回有效下载地址')
            parsed=urlparse(url)
            if parsed.scheme!='https' or not parsed.hostname or not (
                parsed.hostname.endswith('.erp321.com') or parsed.hostname.endswith('.erp321.cn')):
                raise RuntimeError('平台下载域名已改变，请更新 APP')
            self.update(task,stage=f'聚水潭 {start} 至 {end}：下载原始商品数据')
            partial=folder/f'source-{key}.part'
            for attempt in range(3):
                self.checkpoint()
                try:
                    with urlopen(url,timeout=60) as response, partial.open('wb') as out:
                        shutil.copyfileobj(response,out)
                    partial.replace(source)
                    break
                except (OSError,TimeoutError):
                    partial.unlink(missing_ok=True)
                    if attempt==2:raise RuntimeError(f'{key} 下载失败，可在导出记录中继续') from None
                    time.sleep(1)
            sources.append(source)
        total=sum(s.get('orderCount',0) for s in segments.values())
        self.update(task,orderCount=total,segments=segments)
        if not total:
            self.update(task,status='empty',stage='所选期间没有订单')
            return False
        if len(sources)!=sum(s.get('orderCount',0)>0 for s in segments.values()):
            raise RuntimeError('聚水潭月度文件尚未全部下载，已停止合并')
        self.update(task,stage='合并逐月商品数据')
        merge_jst_sources(sources,folder/'source.csv')
        return True
    def wait_jst_url(self,adapter,task_id):
        """等待平台异步文件；超时保留任务号，下次继续查询，不重新提交。"""
        deadline=time.monotonic()+15*60
        while True:
            self.checkpoint()
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise RuntimeError(f'聚水潭任务 {task_id} 已等待 15 分钟，平台仍未返回文件。'
                                   '任务号已保留，可稍后在导出记录中继续，不会重复提交。')
            try:return jst.wait_task(adapter,task_id,min(20,remaining))
            except TimeoutError:continue
    def execute(self,task,account,next_account=False):
        if account['platform']!='jst':return self.execute_tmall(task,account,next_account)
        folder=self.data/'tasks'/task['id'];folder.mkdir(parents=True,exist_ok=True)
        browser=None
        try:
            self.update(task,status='running',stage=f"登录：{account['name']}",error=None,
                        warning=None,pddWarnings=[])
            source=folder/'source.csv'
            # 下载后只做本地恢复，无需再次提交或登录平台。
            if not source.exists():
                browser,adapter,identity=self.open_account(account)
                if task.get('identity') and task['identity']!=identity:raise RuntimeError('恢复任务与当前平台账号不一致')
                self.update(task,identity=identity)
                live=jst.shops(adapter)
                account['shops']=live;self.store.save();self.notify('shops',account['id'])
                live_ids={str(s['id']) for s in live}
                selected=task['selectedShops']
                if not task['allShops'] and not set(selected).issubset(live_ids):raise RuntimeError('部分所选店铺已不可用，请重新选择店铺')
                periods=jst_month_periods(task['start'],task['end'])
                # 旧版已提交的平台任务继续按原任务号恢复，避免重复消耗异步导出额度。
                legacy=any(task.get(k) for k in ('taskId','downloadUrl','submissionUnknown'))
                if len(periods)>1 and not legacy:
                    complete=self.download_jst_months(task,adapter,folder,selected)
                    if not complete:return
                else:
                    c=jst.condition(task['start'],task['end'],0,'send_date',1,True)
                    c['shop']=[] if task['allShops'] else [int(s) for s in selected]
                    if not task.get('taskId') and not task.get('downloadUrl'):
                        if task.get('submissionUnknown'):raise RuntimeError('上次提交结果未知，请到聚水潭异步导出管理核查，避免重复提交')
                        period=f'聚水潭 {task["start"]} 至 {task["end"]}'
                        self.update(task,stage=f'{period}：查询所选店铺订单')
                        count=jst.query(adapter,c)
                        self.update(task,stage=f'{period}：查到 {count:,} 单',orderCount=count)
                        if not count:
                            self.update(task,status='empty',stage='所选期间没有订单');return
                        export_c={**c,'exportType':'pforderdetails','isAds':False,'isExport':True,'untype':'pforderprofit_orderprofitbyoid','isExportCond':False,'shopCategoryId':0}
                        self.update(task,stage=f'{period}：提交 {count:,} 单商品数据',submissionUnknown=True)
                        result=adapter.api('export',{'data':{'exportCondition':json.dumps({'isAds':False,'condition':export_c},ensure_ascii=False)}})['data']
                        if not isinstance(result,dict) or result.get('isSuccess') is not True:
                            self.update(task,submissionUnknown=False)
                            raise RuntimeError('平台拒绝导出，请检查权限或导出额度')
                        self.update(task,submissionUnknown=False,taskId=result.get('taskId',0),downloadUrl=result.get('url'),exportType=result.get('exportType',0))
                    url=task.get('downloadUrl')
                    if task.get('taskId'):
                        self.update(task,stage=f'聚水潭 {task["start"]} 至 {task["end"]}：'
                                               f'等待生成文件（{task.get("orderCount",0):,} 单，可继续）')
                        if not url:
                            url=self.wait_jst_url(adapter,task['taskId'])
                            self.update(task,downloadUrl=url)
                    if not url:raise RuntimeError('平台未返回有效下载地址')
                    parsed=urlparse(url)
                    if parsed.scheme!='https' or not parsed.hostname or not (parsed.hostname.endswith('.erp321.com') or parsed.hostname.endswith('.erp321.cn')):raise RuntimeError('平台下载域名已改变，请更新 APP')
                    self.update(task,stage=f'聚水潭 {task["start"]} 至 {task["end"]}：下载原始商品数据')
                    self.checkpoint()
                    for attempt in range(3):
                        try:
                            with urlopen(url,timeout=60) as response, (folder/'source.part').open('wb') as out:shutil.copyfileobj(response,out)
                            (folder/'source.part').replace(source);break
                        except (OSError,TimeoutError):
                            if attempt==2:raise RuntimeError('下载失败，可在导出记录中继续') from None
                            time.sleep(1)
            self.checkpoint();self.update(task,stage='校验原始明细并按店铺分组')
            periods=jst_source_periods(folder,task)
            has_gap=any(path.is_file() and sum(source_order_counts(path,set()))<expected
                        for _,_,_,expected,path in periods)
            pdd_warnings=[]
            if has_gap:
                # 只读补查缺口归属。续跑使用已缓存的原始 CSV，不重新提交导出任务。
                if browser is None:
                    browser,adapter,identity=self.open_account(account)
                    if task.get('identity') and task['identity']!=identity:
                        raise RuntimeError('恢复任务与当前平台账号不一致')
                    live=jst.shops(adapter)
                    account['shops']=live;self.store.save();self.notify('shops',account['id'])
                def pdd_query(start,end,shop_ids):
                    self.checkpoint()
                    c=jst.condition(start,end,0,task['dateField'],1,True)
                    c['shop']=[int(identity) for identity in shop_ids]
                    return jst.query(adapter,c)
                pdd_warnings=confirmed_pdd_warnings(periods,task,live,pdd_query)
            if browser:
                self.release(browser,account,next_account);browser=None
            index=prepare_sheets(source,folder,task,pdd_warnings)
            if not task['allShops'] and not {s['id'] for s in index['shops']}.issubset(set(task['selectedShops'])):
                raise RuntimeError('平台返回了未选择店铺的数据，停止导出')
            draft=folder/'export.xlsx'
            self.update(task,stage=f"生成 {len(index['shops'])} 个店铺 Sheet",rows=index['rows'],sheetCount=len(index['shops']))
            self.build_excel(folder/'sheets.json',draft,task)
            self.checkpoint();verify_xlsx(draft,index)
            # 聚水潭一个任务（可跨多月）只出一个工作簿；不按起始月份再分目录。
            destination=Path(task['output'])/PLATFORMS['jst']/(safe_name(task['accountName'])+'_'+account['id'][:6])
            destination.mkdir(parents=True,exist_ok=True)
            scope='全部店铺' if task['allShops'] else '所选店铺'
            file=destination/jst_filename(scope,task['start'],task['end'],task['id'])
            archive(draft,file)
            missing=sum(item['missing'] for item in pdd_warnings)
            warning=(f'拼多多商品明细少 {missing:,} 单，可能受近三个月导出范围限制；'
                     '工作簿「导出说明」已列出缺失月份，本文件不是全量账单。') if missing else None
            self.update(task,status='done',stage='导出完成（有拼多多历史明细提醒）' if warning else '导出完成',
                        file=str(file),sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                        finishedAt=dt.datetime.now().isoformat(timespec='seconds'),
                        warning=warning,pddWarnings=pdd_warnings,error=None)
            if warning:self.notify('warning',warning)
        except Exception as exc:
            self.update(task,status='failed',stage='导出未完成',error=readable_error(exc))
            raise
        finally:
            if browser:
                # 出错时仍尝试平台退出；不清除设备标识、Cookie 或持久目录。
                try:self.release(browser,account,next_account)
                except Exception:browser.close()
            self.browser=None
    def execute_tmall(self,task,account,next_account=False):
        """天猫：逐月、逐来源下载明细，每个「来源+月份」产出一个单 Sheet 工作簿。

        实测两个报表都不含店铺列，因此不按店铺分 Sheet（2026-09-27 用户确认：
        一个账号 + 一个月度明细 = 一个工作簿、单 Sheet）。
        """
        folder=self.data/'tasks'/task['id'];folder.mkdir(parents=True,exist_ok=True)
        months=task.get('billingMonths') or []
        if not months:raise RuntimeError('天猫任务缺少账单月份')
        browser=None
        try:
            # 断点续跑：只要每个来源每个月都已有原始文件，就不必再登录下载。
            # 原始下载单独放在 source/ 子目录：同目录下还有 .jsonl / .log / .sheets.json /
            # 工作簿草稿，用前缀 glob 取「已下载文件」会取到中间产物（字母序里 .jsonl 排最前），
            # 续跑会以「不支持的明细文件类型」失败。
            source_dir=folder/'source';source_dir.mkdir(parents=True,exist_ok=True)
            needed=[(key,month) for month in months for key in tmall.FLOWS]
            missing=[(key,month) for key,month in needed if not cached_sources(source_dir,key,month)]
            if missing:
                self.update(task,status='running',stage=f"登录：{account['name']}",error=None)
                browser,_,_=self.open_account(account)
            else:
                self.update(task,status='running',stage='原始明细已就绪，无需登录',error=None)
            produced=[];checksums={};total_rows=0
            # Phase 1: 下载。每个来源做一次范围查询、逐月按行下载；无数据的月份
            # 在 fetch_source 里就跳过了，downloaded 里不会有那一项。
            downloaded={}   # (key,month)->path
            claims={}       # (key,month)->页面声明数字（明细笔数/收入金额）
            if missing:
                for key in tmall.FLOWS:
                    self.checkpoint()
                    flow=dict(tmall.FLOWS[key],key=key)
                    needed=[m for m in months if not cached_sources(source_dir,key,m)]
                    if not needed: continue
                    self.update(task,stage=f"{flow['name']}：范围查询 {needed[0]}~{needed[-1]}")
                    paths=tmall_bridge.fetch_source(browser,key,needed,source_dir,lambda text:self.notify('stage',text),self.checkpoint)
                    for m,p in paths.items():
                        downloaded[(key,m)]=p
                    if key=='fund_detail':
                        for m,claim in (getattr(browser,'last_detail_claims',{}) or {}).items():
                            claims[(key,m)]=claim
            # Phase 2: 逐月整理归档。该月没下载到原始文件（无数据/已跳过）就不出工作簿。
            for month in months:
                for key in tmall.FLOWS:
                    self.checkpoint()
                    flow=dict(tmall.FLOWS[key],key=key)
                    cached=cached_sources(source_dir,key,month)
                    source=cached[0] if cached else downloaded.get((key,month))
                    if not source:
                        self.notify('stage',f"{flow['name']}：{month} 无明细，跳过")
                        continue
                    self.update(task,stage=f"{flow['name']}：整理 {month} 明细")
                    draft=folder/f'{key}-{month}.xlsx'
                    # 页面声明的数字只在本轮下载时才有（用缓存文件续跑时没有页面可读），
                    # 因此 claim 允许为 None；一旦给了，check_claim 会自行核对它是否属于这一份。
                    claim=claims.get((key,month))
                    index=self.build_tmall_excel(folder,source,flow['name'],draft,f'{key}-{month}',task,f'（{month}）',flow=flow,month=month,claim=claim)
                    # 归档目录带上账号 id 后缀：只按名称归档时，两个同名店铺（例如同一家店的
                    # 主账号与子账号）会写进同一目录，同名工作簿互相覆盖，第二个静默盖掉第一个。
                    # 聚水潭链路一直带这个后缀，这里与它保持一致。
                    destination=Path(task['output'])/PLATFORMS['tmall']/(safe_name(account['name'])+'_'+account['id'][:6])/month
                    destination.mkdir(parents=True,exist_ok=True)
                    final=destination/tmall_filename(flow['name'],month)
                    archive(draft,final)
                    produced.append(final)
                    checksums[str(final)]=hashlib.sha256(final.read_bytes()).hexdigest()
                    total_rows+=index['rows']
                    # 增量写入 files/rows：中途失败时界面仍能看到已完成的工作簿，
                    # rows 也要累计，否则多工作簿任务只会显示最后一个的行数。
                    self.update(task,file=str(final),files=[str(p) for p in produced],
                                sheetCount=len(produced),rows=total_rows,
                                stage=f"{flow['name']}：{month} 完成")
            self.update(task,status='done',stage=f'导出完成，共 {len(produced)} 个工作簿',
                        files=[str(p) for p in produced],checksums=checksums,rows=total_rows,
                        finishedAt=dt.datetime.now().isoformat(timespec='seconds'),error=None)
            return produced
        except Exception as exc:
            self.update(task,status='failed',stage='导出未完成',error=readable_error(exc))
            raise
        finally:
            if browser:
                # 出错时仍尝试平台退出；不清除设备标识、Cookie 或持久目录。
                try:self.release(browser,account,next_account)
                except Exception:browser.close()
            self.browser=None
    def build_tmall_excel(self,folder,source,sheet_name,draft,stem,task=None,label='',*,flow,month,claim=None):
        """把平台明细整理成单 Sheet 工作簿：保留原始列名与取值，长数字按文本写入。

        label 用于在报错里补上月份：一次导出可能覆盖好几个自然月，只说「哪个来源」
        用户无法判断是哪个月出的问题。
        """
        headers,rows=read_table(source)
        if not rows:
            # 空明细有两种可能：这个月确实没有数据（所选日期跨到本月时很常见），
            # 或者下载到的不是明细（登录页之类）。两边都要说，别只报「没有数据行」。
            raise RuntimeError(f'{sheet_name}{label}：明细文件没有数据行——'
                               f'如果该月确实没有数据，这是正常的（已导好的其它月份不受影响）；'
                               f'否则请核对下载到的文件内容是否就是该月明细')
        # 再核对「这是不是这份报表、这一期」。flow / month 是**必填**关键字参数，就是为了
        # 不给「忘了传、于是静默跳过校验」留口子——这条校验挡的是实测算出来的漏洞：
        # 一份 HTML 登录页存成 .csv 会被当成正常明细，产出「成功」的错表。
        tmall.check_detail(headers,rows,flow,month,label)
        check_claim(rows,headers,flow,month,claim,label)
        if len(headers)>16384:raise RuntimeError(f'{sheet_name}：列数超过 Excel 上限')
        date_columns=[i for i,h in enumerate(headers) if h.endswith('时间') or h.endswith('日期')]
        numeric_columns=[i for i,h in enumerate(headers) if '元' in h or h in ('数量','明细笔数')]
        lines=folder/f'{stem}.jsonl'
        with lines.open('w',encoding='utf8') as handle:
            for row in rows:
                values=[]
                for index,value in enumerate(row):
                    text=value.strip()
                    moment=parse_date(text) if index in date_columns and text else None
                    converted=cell_number(text) if index in numeric_columns and text else None
                    if moment is not None:values.append(moment)
                    elif converted is not None:values.append(converted)
                    else:values.append(text)
                handle.write(json.dumps(values,ensure_ascii=False)+'\n')
        index={'headers':headers,'dateColumns':date_columns,'numericColumns':numeric_columns,
               'rows':len(rows),'orders':0,
               'shops':[{'id':'1','name':sheet_name,'rows':len(rows),'path':str(lines),'sheetName':sheet_name[:31]}]}
        jst.write_json(folder/f'{stem}.sheets.json',index)
        # 注意：build_excel 的第一个参数是索引 JSON 的**路径**（沿用聚水潭链路既有签名）。
        self.build_excel(folder/f'{stem}.sheets.json',draft,task)
        verify_xlsx(draft,index)
        return index
    def build_excel(self,index,draft,task=None):
        """写出工作簿。用内置的流式写入器，内存只与单行有关。

        原先调 node + @oai/artifact-tool 在内存里建整本工作簿，实测约 0.5 MB/行：
        8000 行就要 4.6GB、16000 行直接 OOM——而按月的聚水潭导出远超这个规模
        （真实整月约 4 万单），也就是那条路根本走不到头。写入过程中会检查停止标记，
        取消时不会留下半截文件（只有 .part，成功才改名）。
        """
        def progress(done,total,name):
            self.checkpoint()
            if task:self.update(task,stage=f"生成 Sheet {done}/{total}：{name}")
        xlsx_writer.write(draft,index,progress)
