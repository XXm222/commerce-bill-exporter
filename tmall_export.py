"""天猫两个账单入口的月度明细下载。

入口与月度规划由用户指定；**页面标签与交互方式已用真实登录态核实**（2026-09-27，
天猫商家中心 收支账单页），因此下面的标签不是推测值：

  收支账单收入（已验证）：
    收入账单 -> 月汇总 -> 设置「开始月份/结束月份」-> 查询 -> 下载全量明细
    -> 弹出「已生成完成，可在历史下载记录中查看」-> 历史下载记录
    -> 找到时间范围匹配该月的那一行 -> 点该行「下载」

  聚核算（已验证）：
    ?active=fund_detail -> 点「月汇总」（URL 变为 active=fund_month）
    -> 展开「入账日期」范围控件 #billCycle（左右两个年份面板，同一月份格点两次即单月）
    -> 点「搜索」 -> 月汇总表格中该月那一行（日期列形如 202608）的「下载明细」直接落盘
    实测导出为 xlsx，命名 2099141102_202608_0.xlsx（{账号}_{YYYYMM}_{序号}）。

两个关键实测结论，无法从文档推断：

1) 「下载全量明细」不直接下载文件。它先弹对话框；若该月已生成，提示去「历史下载记录」
   取。真正的文件是在历史记录里点某一行的「下载」才落盘。
2) 该行的「下载」必须用**可信点击**：JS 合成点击（event.isTrusted=false）在淘宝被忽略，
   点了没有任何反应。APP 侧用 Patchright 的原生 click（走 CDP 真实输入事件）。

下载文件命名实测为 {业务小类}_{YYYYMM}_{YYYYMM}.csv；真实 2026-08 货款收入明细为
22 列 2974 行，**不含店铺列**，因此本报表无法按店铺分 Sheet（见 PRODUCT.md）。
"""
import json
import re, time

# 点击候选标签的容器：与 chrome.logout 一致的"叶子元素唯一匹配"策略。
CLICKABLE = 'a,button,[role=button],[role=tab],[role=menuitem],li,span,div,label,td'

FLOWS = {
    'bill_income': {
        'name': '收支账单收入全量明细',
        'url': 'https://myseller.taobao.com/home.htm/whale-accountant/bill/summary?billDirection=income&billType=month',
        'tabs': ('收入账单', '月汇总'),
        'month': {'kind': 'inputs', 'placeholders': ('开始月份', '结束月份')},
        'confirm': ('查询',),
        'mode': 'history',
        'trigger': ('下载全量明细',),
        'history': ('历史下载记录',),
        # 应有的列（取自 2026-09-28 真实下载的 交易货款_202608_202608.csv，22 列）。
        # 只取三列做签名：足够认出「这是不是这份报表」，又不至于平台加一列就误判。
        'columns': ('账期', '订单号', '业务流水号'),
        # 账期列的值形如 20260803（按天），前 6 位就是月份——用来独立核对「下到的是不是这一期」。
        'monthColumn': '账期',
    },
    'fund_detail': {
        'name': '聚核算月度明细',
        'url': 'https://myseller.taobao.com/home.htm/whale-accountant/pay/capital/home?active=fund_detail',
        # 实测：点「月汇总」后 URL 变为 ?active=fund_month，表格按自然月一行。
        'tabs': ('月汇总',),
        # 实测：#billCycle 是「入账日期」范围选择器（不是账单月份），
        # 展开后左右两个年份面板，同一月份格点两次即得单月范围。
        'month': {'kind': 'rangepicker', 'control': '#billCycle',
                  'panel': '.next-calendar2-panel', 'yearHeader': '.next-calendar2-header'},
        'confirm': ('搜索',),
        # 实测：月汇总表格「操作」列有「下载明细」，日期列形如 202608。
        'mode': 'row_action',
        'rowKey': 'day',          # 日期列取值 YYYYMM
        'action': ('下载明细',),
        'history': ('导出记录',),
        # 应有的列（取自 2026-09-28 真实下载的 2099141102_202608_0.xlsx，14 列）。
        # 注意真实的写法是「入帐日期」（帐）与半角括号的「收入金额(元)」，与收支账单那份不同。
        'columns': ('入帐日期', '支付流水号', '收入金额(元)'),
        'monthColumn': '入帐日期',
        # 月汇总表格里那一行自己声明的数字——用来核对「下到的文件是否与页面上说的一致」。
        # 页面表头写的是全角括号的「收入金额（元）」，而下载文件里的列名是半角括号的
        # 「收入金额(元)」；两者是同一个数（真实样本对过：202608 两边都是 21,025.78）。
        'claims': {'rows': '明细笔数', 'income': '收入金额（元）', 'incomeColumn': '收入金额(元)'},
    },
    'alipay_month': {
        'name': '支付宝月资金账单',
        'url': 'https://b.alipay.com/page/mbillexprod/bill/download/fundBill',
        'mode': 'alipay_zip',
    },
}


def month_range_text(month):
    """历史下载记录里「时间范围」列的写法，实测形如 2026-08 ~ 2026-08。"""
    year, number = month.split('-')
    return f'{year}-{number} ~ {year}-{number}'


_PROBE_JS = '''(()=>{const label=LABEL,tags=TAGS,act=ACT;
  const norm=e=>(e.textContent||'').replace(/\\s+/g,' ').trim();
  const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
  const all=Array.from(document.querySelectorAll(tags)).filter(e=>vis(e)&&norm(e)===label);
  const leaf=all.filter(e=>!all.some(x=>x!==e&&e.contains(x)));
  if(act&&leaf.length===1){leaf[0].scrollIntoView({block:'center'});leaf[0].click();}
  return {count:leaf.length};})()'''


def _probe(browser, label, tags=CLICKABLE, act=False):
    """返回文本严格等于 label 的可见叶子元素数量；act 为真时顺带点击。

    严格取"叶子元素"并只接受唯一匹配，避免点到包含该文本的父容器，
    或页面出现重复标签时点错目标。
    """
    code = (_PROBE_JS.replace('LABEL', json.dumps(label))
                     .replace('TAGS', json.dumps(tags))
                     .replace('ACT', 'true' if act else 'false'))
    return (browser.evaluate(code).get('value') or {}).get('count', 0)


def has_text(browser, label, tags=CLICKABLE):
    """页面上是否存在文本严格等于 label 的可见元素（只读，不点击）。"""
    return _probe(browser, label, tags) == 1


def click_text(browser, label, optional=False, tags=CLICKABLE):
    """点击文本严格等于 label 的唯一叶子元素。

    找不到且 optional 时返回 False；找不到且必需、或匹配到多个时抛出可读错误，
    避免"点错元素静默导出错误月份"这类难查问题。
    """
    count = _probe(browser, label, tags, act=True)
    if count == 1:
        return True
    if optional:
        return False
    if count == 0:
        raise RuntimeError(f'页面上找不到「{label}」，可能是页面已改版或未登录；请核对天猫页面结构')
    raise RuntimeError(f'页面上有 {count} 个「{label}」可点击元素，无法确定点哪一个，请人工核对页面')


def first_present(browser, labels):
    """返回第一个在页面上存在的标签，都没有则报错。"""
    for label in labels:
        if has_text(browser, label):
            return label
    raise RuntimeError(f'页面上找不到任何一个预期标签：{list(labels)}；请核对天猫页面结构')


def set_month(browser, flow, month, notify=None):
    """设置账单月份。只实现已核实的控件类型。"""
    spec = flow['month']
    if spec['kind'] == 'inputs':
        for placeholder in spec['placeholders']:
            field = browser.page.get_by_placeholder(placeholder, exact=True).first
            field.wait_for(state='visible', timeout=20000)
            field.fill(month)
            # 回读校验：填了但页面没接受（只读输入框、组件拒绝等）会导致带着错误月份
            # 继续导出，而结果看起来一切正常。宁可当场停。
            got = (field.input_value() or '').strip()
            if got != month:
                raise RuntimeError(f'{flow["name"]}：「{placeholder}」没有接受 {month}'
                                   f'（读回 {got or "空值"}），请核对天猫页面')
        if notify:
            notify(f'{flow["name"]}：已设置账单月份 {month}')
        return month
    if spec['kind'] == 'rangepicker':
        return _set_range_month(browser, flow, spec, month, notify)
    raise RuntimeError(f'{flow["name"]}：月份控件类型 {spec["kind"]} 尚未支持')


def _set_range_month(browser, flow, spec, month, notify=None):
    """阿里 Fusion 月份范围选择器：展开后在同一月份格上点两次即得单月范围。

    实测 #billCycle 是「入账日期」范围选择器，展开后左右两个年份面板，
    只按月份文本点会命中两处（2026年/2027年各一个），必须按年份面板限定。
    """
    year, number = month.split('-')
    label = f'{int(number)}月'
    browser.page.locator(spec['control']).first.click()
    browser.page.wait_for_timeout(800)
    panels = browser.page.locator(spec['panel'])
    total = panels.count()
    if total == 0:
        raise RuntimeError(f'{flow["name"]}：月份控件展开后没有出现日历面板')
    target = None
    for index in range(total):
        panel = panels.nth(index)
        header = panel.locator(spec['yearHeader']).first
        text = (header.inner_text() or '').strip() if header.count() else ''
        if text.startswith(year):
            target = panel
            break
    if target is None:
        years = [panels.nth(i).locator(spec['yearHeader']).first.inner_text() for i in range(total)]
        raise RuntimeError(f'{flow["name"]}：月份面板里没有 {year} 年（当前面板：{years}）')
    cell = target.get_by_text(label, exact=True).first
    if cell.count() == 0:
        raise RuntimeError(f'{flow["name"]}：{year} 年面板里找不到 {label}')
    cell.click()          # 起始
    browser.page.wait_for_timeout(400)
    cell = target.get_by_text(label, exact=True).first
    if cell.count():
        cell.click()      # 结束：同月点两次得到单月范围
    browser.page.wait_for_timeout(400)
    browser.page.keyboard.press('Escape')
    browser.page.wait_for_timeout(500)
    # 回读校验：选择器没吃上点击时页面会保留原范围，不校验就会导出错误的月份。
    inputs = browser.page.locator(f"{spec['control']} input")
    if inputs.count() >= 2:
        start = (inputs.nth(0).input_value() or '').strip()
        end = (inputs.nth(1).input_value() or '').strip()
        if (start, end) != (month, month):
            raise RuntimeError(f'{flow["name"]}：入账日期没有设成 {month}~{month}'
                               f'（读回 {start or "空"}~{end or "空"}），请核对天猫页面')
    if notify:
        notify(f'{flow["name"]}：已设置入账日期 {month}')
    return month


def open_flow(browser, flow, month, notify=None, checkpoint=None):
    """进入入口页、切到月汇总、设置月份并查询。"""
    if checkpoint:
        checkpoint()
    browser.page_for(flow['url'])
    browser.page.wait_for_timeout(2500)
    for tab in flow['tabs']:
        if checkpoint:
            checkpoint()
        if click_text(browser, tab, optional=True):
            if notify:
                notify(f'{flow["name"]}：进入「{tab}」')
            browser.page.wait_for_timeout(1200)
    set_month(browser, flow, month, notify)
    for label in flow['confirm']:
        # 确认按钮走可信点击：实测淘宝对 JS 合成点击的处理并不一致，
        # 用唯一匹配先确认目标存在，再交给 Playwright 派发真实输入事件。
        if has_text(browser, label):
            browser.click_native(label)
            browser.page.wait_for_timeout(2200)
    return True


def month_compact(month):
    """月汇总表格「日期」列的写法，实测形如 202608。"""
    year, number = month.split('-')
    return f'{year}{number}'


LOGOUT_URL = 'https://login.taobao.com/member/logout.jhtml'
LOGIN_HOSTS = ('login.taobao.com', 'loginmyseller.taobao.com', 'havanalogin.taobao.com')


def logout(browser):
    """退出当前淘宝/天猫账号。

    两条路都试，成一条就算成：
      ① **平台自己的退出入口**——天猫商家后台右上角账号菜单里的「退出当前账号」
         （2026-09-28 用户截图确认；本项目此前写的「没有退出入口」是错的，
         判据里也一直没有这个写法，所以永远点不到）；
      ② 淘宝固定的登出地址（页面结构变了也能兜住）。
    最后**回读确认真的退出了**：退出失败却继续下一个账号，下一次导出会在上一个人的登录态里
    进行——文件名、月份、行数全都正常，只有归属是错的。
    """
    try:
        if browser.logout():
            return True
    except Exception:
        pass
    browser.open_url(LOGOUT_URL)
    for _ in range(20):
        try:
            href = browser.evaluate('location.href').get('value') or ''
            host = (browser.evaluate('location.hostname').get('value') or '')
        except Exception:
            href, host = '', ''
        if any(h in str(host) for h in LOGIN_HOSTS) or 'login' in str(href):
            return True
        time.sleep(.5)
    # 没跳到登录页不一定就是没退出（可能只是停在 www.taobao.com），再用密码框确认一次
    try:
        if browser.is_logged_out():
            return True
    except Exception:
        pass
    raise RuntimeError('退出天猫账号没有成功（没跳转到登录页，也没检测到未登录状态）。'
                       '为避免下一个账号沿用它，已停止；请在 Chrome 里手动退出后重试')


def observed_months(browser, limit=8):
    """这一页上**可见**的月份（形如 202608）。只作为事实写进报错，不做因果判断。

    第一版想用它来区分「该月没有数据」和「页面结构变了」——**想错了**，而且错在危险的方向：
    月汇总表格是按搜索条件过滤的，找不不到目标月份时页面上本来就不会有别的月份，
    于是它会对一个「这个月没数据」的正常情形说成「页面变了，去核对结构」。
    现在的做法是把它当纯事实陈述，因果交给下面的 missing_row_reason 用**已成功的步骤**来推断。

    innerText 只含可见内容（隐藏标签页的文字不算），这正是我们要的「用户此刻看到什么」。
    """
    code = ("(()=>{const t=document.body.innerText||'';"
            "return (t.match(/(?<!\\d)(20\\d{4})(?!\\d)/g)||[]).slice(0,60);})()")
    try:
        raw = browser.evaluate(code).get('value') or []
    except Exception:
        return []
    found = []
    for item in raw:
        text = str(item)
        if len(text) == 6 and text[4:6].isdigit() and 1 <= int(text[4:6]) <= 12 and text not in found:
            found.append(text)
    return found[:limit]


_CLAIMS_JS = """(()=>{const target=TARGET;
 const heads=Array.from(document.querySelectorAll('th')).map(e=>(e.textContent||'').trim());
 const row=Array.from(document.querySelectorAll('tr')).find(tr=>tr.querySelector('td')
   &&(tr.textContent||'').includes(target));
 const cells=row?Array.from(row.querySelectorAll('td')).map(e=>(e.textContent||'').trim()):[];
 return {headers:heads,cells:cells};})()"""


def read_row_claims(browser, flow, month):
    """读月汇总那一行自己声明的「明细笔数」与「收入金额（元）」。

    为什么值得单独读一遍：这是**页面自己说的数字**，独立于我们请求了什么、也独立于下载到的
    文件。真实样本已经对过（202608：页面 953 笔 / 21,025.78，下载文件 953 行、收入合计 21,025.78），
    所以拿它核对下载结果是有效的。聚水潭那条链路一直有这道核对（查询 N 单 vs 导出 M 单），
    天猫这边此前没有。

    表头与数据行在真实页面上分属**两个 table**（实测），所以这里分别取：表头从所有 th 里拿，
    数据行找含目标月份且有 td 的那一行，再按列序对齐。取不到就返回空——这只是核对，
    绝不能因为它读不到就把一次正常导出判成失败。
    """
    claims = flow.get('claims')
    if not claims:
        return {}
    code = _CLAIMS_JS.replace('TARGET', json.dumps(month_compact(month)))
    try:
        value = browser.evaluate(code).get('value') or {}
    except Exception:
        return {}
    headers = value.get('headers') or []
    cells = value.get('cells') or []
    if not headers or not cells:
        return {}
    found = {}
    for name, key in (('rows', claims['rows']), ('income', claims['income'])):
        if key in headers:
            index = headers.index(key)
            if index < len(cells):
                found[name] = cells[index]
    return found


def missing_row_reason(browser, flow, month):
    """把「没找到那一行」的原因尽量写清楚（诊断文字，追加在报错后面）。

    依据不是页面上有没有别的月份，而是**这次已经走成功的那几步**：能走到这里，说明
    「月汇总」标签打开了、入账日期被设成目标月份并被回读确认、搜索也点过了（聚核算），
    或者「下载全量明细」点过了、「历史下载记录」也打开了（收支账单）。
    这些都成功，就说明页面结构没变——因此最常见的原因是该月**确实没有数据**。
    这个判断很要紧：把「没数据」误报成「页面改版」，用户会去改一份其实没坏的适配器；
    反过来把「改版」误报成「没数据」，则会让一次真实失败被当成正常而放过。
    """
    steps = ('已经成功打开「月汇总」、把入账日期设成该月并点过「搜索」' if flow['mode'] == 'row_action'
             else '已经点过「下载全量明细」并打开了「历史下载记录」')
    seen = observed_months(browser)
    visible = (f'页面上此刻可见的月份是 {"、".join(seen)}。' if seen
               else '页面上此刻没有可见的月份单元格（表格按搜索条件过滤，没有数据时本来就是这样）。')
    return (f'；{steps}，这些都成功了，说明页面结构没变，最常见的原因是 {month} **确实没有数据**'
            f'（例如所选日期跨到了这个月，货款收入还没产生）。{visible}'
            f'若确认该月有数据却仍是这样，请核对天猫页面结构。'
            f'已经导好的其它月份不受影响。')


def check_detail(headers, rows, flow, month, label=''):
    """核对下载到的确实是**这份报表、这个月份**的明细；不是就当场停。

    为什么需要它（实测出来的漏洞，2026-09-28）：`.xlsx` 那条路早就有防线（读到不是 zip 就报
    「这个文件不是有效的 Excel」），但 `.csv` 那条路**什么检查都没有**。把一份 HTML 登录页
    存成 .csv 交给整理环节，它会当成一份正常的 CSV：列名变成 `<!DOCTYPE html>...`，
    页面片段变成数据行，最后产出一份「成功」的工作簿——行数、校验、归档全都正常。
    也就是说「下到的不是明细」会变成「用户拿到一份错的账单，且看不出来」。

    两层，都是独立于「我们请求了什么」的证据：
      ① 列签名：报表本该有的列少一个，就不是这份报表（登录页、错误页、别的报表）；
      ② 月份：每一行的账期/入帐日期都必须落在请求的那个月里。
    两份真实月报都通过了这两条（收支账单 2974 行、聚核算 953 行，账期/入帐日期 100% 落在当月、
    无空值），所以它们不是纸上规则。
    """
    columns = flow.get('columns') or ()
    missing = [name for name in columns if name not in headers]
    if missing:
        shown = '、'.join(str(h)[:20] for h in headers[:6]) or '（空）'
        raise RuntimeError(
            f'{flow["name"]}{label}：这份明细缺少应有的列 {"、".join(missing)}'
            f'（实际列名：{shown}）。多半是下载到的不是该报表（例如登录页或错误页），已停止导出。')
    key = flow.get('monthColumn')
    if not key or key not in headers:
        return
    target = month_compact(month)
    index = headers.index(key)
    bad = []
    for row in rows:
        raw = row[index] if index < len(row) else ''
        digits = re.sub(r'\D', '', str(raw))
        if digits[:6] != target:
            bad.append(str(raw)[:20] or '（空）')
    if bad:
        examples = '、'.join(dict.fromkeys(bad))[:80]
        raise RuntimeError(
            f'{flow["name"]}{label}：明细里有 {len(bad)} 行的{key}不属于 {month}'
            f'（例如 {examples}）。月份不对的明细不能用，已停止导出。')


def require_row(browser, flow, target, hint, timeout=20000, month=None):
    """等待表格里出现目标行。

    真实站点是异步渲染，点完「查询/搜索」后表格不会立刻有数据；只查一次 count
    会在真机上偶发失败，而仿真页永远是同步的、测不出来。

    month 给定时，失败信息里会附上「这一页到底有哪些月份」的诊断——见 observed_months。
    """
    row = browser.page.locator('tr', has_text=target)
    try:
        row.first.wait_for(state='attached', timeout=timeout)
    except Exception:
        reason = missing_row_reason(browser, flow, month) if month else ''
        raise RuntimeError(f'{flow["name"]}：{hint}（未找到 {target}）{reason}') from None
    return row


MONTH_TOKEN = re.compile(r'(?<!\d)(20\d{4})(?!\d)')


def verify_download_name(browser, flow, month):
    """用平台自己的文件名交叉校验月份。

    两份真实导出的命名都带月份：交易货款_202608_202608.csv、2099141102_202608_0.xlsx。
    平台下错期时它的文件名会写着另一期，这是独立于我们请求参数的证据。

    只在文件名里**确实出现了 6 位年月且与目标不符**时才判定冲突；文件名里没有可识别的
    年月时不做判断，避免平台改命名就把正常下载判成失败。
    """
    name = getattr(browser, 'last_download_name', '') or ''
    if not name:
        return ''
    target = month_compact(month)
    tokens = set(MONTH_TOKEN.findall(name))
    if tokens and target not in tokens:
        raise RuntimeError(f'{flow["name"]}：平台返回的文件是「{name}」，'
                           f'里面写着 {"、".join(sorted(tokens))}，与请求的 {target} 不符，已停止导出')
    return name


def download_from_history(browser, flow, month, folder, notify=None, checkpoint=None):
    """收支账单：打开历史记录，定位该月那一行，用可信点击取文件。"""
    if checkpoint:
        checkpoint()
    for label in flow['history']:
        if click_text(browser, label, optional=True):
            break
    else:
        raise RuntimeError(f'{flow["name"]}：找不到历史记录入口 {list(flow["history"])}')
    browser.page.wait_for_timeout(1500)
    target = month_range_text(month)
    require_row(browser, flow, target,
                '历史记录里没有该月的导出，请先在页面上生成该月明细',
                month=month)
    path = browser.download(lambda: browser.click_native('下载', scope=target),
                            folder, f'{flow["key"]}-{month}')
    original = verify_download_name(browser, flow, month)
    if notify:
        notify(f'{flow["name"]}：已取到 {month} 明细 {original or path.name}')
    return path


def download_from_row(browser, flow, month, folder, notify=None, checkpoint=None):
    """聚核算：月汇总表格按自然月一行，点该行「下载明细」直接落盘。"""
    if checkpoint:
        checkpoint()
    target = month_compact(month)
    require_row(browser, flow, target,
                '月汇总表格里没有该月，请确认入账日期范围已覆盖该月并已点「搜索」',
                month=month)
    # 页面自己声明的数字：与下载到的文件核对用（见 engine 里的核对）。
    claims = read_row_claims(browser, flow, month)
    if claims:
        browser.last_detail_claim = {'key': flow.get('key', ''), 'month': month, **claims}
    action = first_present(browser, flow['action'])
    path = browser.download(lambda: browser.click_native(action, scope=target),
                            folder, f'{flow["key"]}-{month}')
    original = verify_download_name(browser, flow, month)
    if notify:
        notify(f'{flow["name"]}：已取到 {month} 明细 {original or path.name}')
    return path


def fetch_month(browser, key, month, folder, notify=None, checkpoint=None):
    """导出来源 key 的某个自然月明细，返回落盘文件路径。"""
    flow = dict(FLOWS[key], key=key)
    open_flow(browser, flow, month, notify, checkpoint)
    if flow['mode'] == 'history':
        trigger = first_present(browser, flow['trigger'])
        if checkpoint:
            checkpoint()
        # 实测「下载全量明细」不直接下载，只弹「已生成完成」对话框，须再走历史记录。
        browser.click_native(trigger)
        browser.page.wait_for_timeout(2500)
        return download_from_history(browser, flow, month, folder, notify, checkpoint)
    if flow['mode'] == 'row_action':
        return download_from_row(browser, flow, month, folder, notify, checkpoint)
    raise RuntimeError(f'{flow["name"]}：未知的下载模式 {flow["mode"]}')


def plan(months):
    """把 export_period 给出的月份列表转成逐月任务描述。"""
    return [{'key': key, 'month': month} for month in months for key in FLOWS]
