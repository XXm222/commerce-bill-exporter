"""支付宝官方月资金账单：浏览器下载 ZIP，核对并整理两份 CSV。"""
import csv
import datetime as dt
import io
import json
import re
import time
import zipfile
from decimal import Decimal
from pathlib import Path

TRUST_URL = 'https://enterpriseportal.alipay.com/portal/trustRedirect.htm?sign_from=3000'
BILL_URL = 'https://b.alipay.com/page/mbillexprod/bill/download/fundBill'
YEAR_SELECT = '[class*="monthPicker___"] .ant-select-selector'
ACCOUNT_JS = '''(()=>{const e=document.querySelector('[class*="switcher___"] .ant-select-selection-item');
 return e?(e.innerText||'').trim():'';})()'''


def _month_state(browser, month):
    return browser.js('''(()=>{const cells=[...document.querySelectorAll('td[title='+JSON.stringify(MONTH)+']')]
      .filter(e=>e.getClientRects().length);if(cells.length!==1)return null;
      const e=cells[0];return {text:e.innerText,ready:[...e.querySelectorAll('a')]
        .some(a=>a.textContent.trim()==='账单已生成')};})()'''.replace('MONTH', json.dumps(month)))


def _select_year(browser, year):
    read = "(document.querySelector('[class*=monthPicker___] .ant-select-selection-item')?.innerText||'').trim()"
    if browser.js(read) != year + '年':
        browser.click(YEAR_SELECT, '支付宝查询年份')
        browser.click_text(year + '年', scope_css='.ant-select-dropdown')
    if not browser.wait(lambda: browser.js(read) == year + '年', 15):
        raise RuntimeError(f'支付宝查询年份未切换到 {year} 年')
    def loaded():
        state = _month_state(browser, year + '-01')
        if not state:
            return False
        return (int(year) > dt.date.today().year or
                bool(re.search(r'账单已生成|账单未生成|生成中|正在生成|处理中', state['text'])))
    if not browser.wait(loaded, 30):
        raise RuntimeError(f'支付宝 {year} 年的账单状态尚未加载，任务已保留，可稍后继续')



def fetch_source(browser, months, folder, notify=None, checkpoint=None, *, authenticate=None):
    """每家店先走天猫官方 SSO，再逐年定位月账单，等待生成中的月份。"""
    notify = notify or (lambda text: None)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    notify('支付宝月资金账单：从当前天猫店铺进入官方支付宝入口')
    browser.open_url(TRUST_URL)
    def arrival():
        host=browser.js('location.hostname')
        return host if host in ('b.alipay.com','login.taobao.com') else None
    state=browser.wait(arrival,30)
    if state=='login.taobao.com':
        notify('支付宝官方入口要求再次登录淘宝，正在使用保存的账号密码')
        filled=authenticate() if authenticate else {'submitted':False,'reason':'missing_credentials'}
        if not filled.get('submitted'):
            reasons={'missing_credentials':'没有保存完整的淘宝账号密码',
                     'form_missing':'没有找到淘宝登录表单',
                     'consent_failed':'登录协议未能确认勾选',
                     'password_not_retained':'密码框没有完整保留输入内容'}
            reason=reasons.get(filled.get('reason'),'自动登录未能提交')
            raise RuntimeError(f'支付宝入口需要再次登录淘宝：{reason}；任务已保留')
        if not browser.wait(lambda: browser.js('location.hostname')=='b.alipay.com',45):
            # 某些登录页提交后停在淘宝首页，需要再次进入原来的官方入口。
            browser.open_url(TRUST_URL)
            state=browser.wait(arrival,30)
        else:
            state='b.alipay.com'
    if state!='b.alipay.com':
        raise RuntimeError('当前天猫账号未能进入支付宝商家平台；请检查该店铺支付宝权限或浏览器里的滑块/短信验证提示。任务已保留')
    browser.open_url(BILL_URL)
    if not browser.wait(lambda: browser.js("!!document.querySelector('input[type=radio][value=monthly]')"), 30):
        raise RuntimeError('支付宝月账单页面未就绪；请检查该账号的账单权限')
    browser.click_text('月账单')
    if not browser.wait(lambda: browser.js("document.querySelector('input[type=radio][value=monthly]')?.checked"), 10):
        raise RuntimeError('支付宝尚未切换为月账单')
    account = browser.wait(lambda: browser.js(ACCOUNT_JS), 15)
    if not account:
        raise RuntimeError('支付宝账单页未读到当前账户，停止下载')
    paths = {}
    current_year = None
    for month in months:
        checkpoint()
        year = month[:4]
        if year != current_year:
            _select_year(browser, year)
            current_year = year
        deadline = time.monotonic() + 300
        refresh_at = time.monotonic() + 10
        while True:
            checkpoint()
            if browser.js(ACCOUNT_JS) != account:
                raise RuntimeError('支付宝账单账户在导出期间发生变化，已停止以避免归错店铺')
            state = _month_state(browser, month)
            if not state:
                raise RuntimeError(f'支付宝没有找到唯一的 {month} 月账单单元格')
            if state['ready']:
                break
            text = state['text']
            if re.search(r'生成中|正在生成|处理中', text):
                notify(f'支付宝 {month}：账单生成中，等待完成（可停止后继续）')
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'支付宝 {month} 月账单仍在生成中；任务已保留，可稍后继续')
                if time.monotonic() >= refresh_at:
                    browser.open_url(BILL_URL)
                    if not browser.wait(lambda: browser.js("!!document.querySelector('input[type=radio][value=monthly]')"), 20):
                        raise RuntimeError('支付宝等待账单期间页面失去登录或账单权限')
                    browser.click_text('月账单')
                    _select_year(browser, year)
                    refresh_at = time.monotonic() + 10
                time.sleep(1)
                continue
            if month >= dt.date.today().strftime('%Y-%m'):
                notify(f'支付宝 {month}：尚未到月账单生成时间，跳过（通常次月 1 日 9:00 生成）')
                break
            if not re.search(r'账单未生成', text):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'支付宝 {month} 月账单状态未返回，任务已保留，可继续')
                time.sleep(1)
                continue
            raise RuntimeError(f'支付宝 {month} 月账单尚未生成；如页面提示需激活账单，请在支付宝激活后继续。已下载文件保留')
        if not state['ready']:
            continue
        notify(f'支付宝月资金账单：下载 {month}')
        selector = f'td[title="{month}"] a'
        path = browser.download(lambda: browser.click(selector, f'{month} 账单已生成'),
                                folder, f'alipay_month-{month}', suffixes={'.zip'})
        read_package(path, month)  # 只接受官方账单结构与对应的完整自然月。
        paths[month] = path
        notify(f'支付宝月资金账单：已下载 {month}')
    return paths


def _decode(raw):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise RuntimeError('支付宝 CSV 编码无法识别')


def read_package(path, month):
    """不解压到文件系统；核对账期、账号、行数与官方收入/支出合计。"""
    start = dt.date.fromisoformat(month + '-01')
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    tables = {}
    try:
        with zipfile.ZipFile(path) as bundle:
            members = [m for m in bundle.infolist() if not m.is_dir() and m.filename.lower().endswith('.csv')]
            if len(members) > 100 or sum(m.file_size for m in members) > 512 * 1024 * 1024:
                raise RuntimeError('支付宝账单压缩包过大，无法安全整理')
            for member in members:
                text = _decode(bundle.read(member))
                kind = 'detail' if '#支付宝账务明细查询' in text else 'summary' if '#支付宝账务汇总查询' in text else None
                if not kind:
                    continue
                dates = re.search(r'#起始日期：\[(\d{4})年(\d{2})月(\d{2})日 00:00:00\]\s*终止日期：\[(\d{4})年(\d{2})月(\d{2})日 00:00:00\]', text)
                if not dates or tuple(map(int, dates.groups())) != (start.year,start.month,start.day,end.year,end.month,end.day):
                    raise RuntimeError(f'支付宝文件的账期不是 {month} 完整自然月，停止整理')
                account = re.search(r'#账号：\[([^\]]+)\]', text)
                if not account:
                    raise RuntimeError('支付宝账单缺少账号信息')
                content = [[v.strip('\t\r\n') for v in row] for row in csv.reader(io.StringIO(text))
                           if row and not row[0].startswith('#') and any(v.strip() for v in row)]
                if not content:
                    raise RuntimeError('支付宝账单缺少表头')
                headers, rows = content[0], content[1:]
                signature = ('账务流水号', '商户订单号', '发生时间', '收入金额（+元）', '支出金额（-元）') if kind == 'detail' else ('类型', '收入笔数', '收入金额（+元）', '支出笔数', '支出金额（-元）')
                if not set(signature).issubset(headers) or any(len(row) != len(headers) for row in rows):
                    raise RuntimeError('支付宝账单列结构不完整，停止整理')
                if kind in tables:
                    raise RuntimeError('支付宝月账单包含多份同类型 CSV，无法确定完整明细')
                if kind == 'detail':
                    time_index = headers.index('发生时间')
                    if any(not row[time_index].strip().startswith(month) for row in rows):
                        raise RuntimeError(f'支付宝明细包含 {month} 以外的发生时间')
                    counts = []
                    for label, column in [('收入','收入金额（+元）'), ('支出','支出金额（-元）')]:
                        footer = re.search(r'#' + label + r'合计：([\d,]+)笔，共([+-]?[\d,.]+)元', text)
                        if not footer:
                            raise RuntimeError(f'支付宝明细缺少官方{label}合计，无法核对完整性')
                        index = headers.index(column)
                        actual = sum((Decimal(row[index].strip().replace(',', '') or '0') for row in rows), Decimal(0))
                        if actual != Decimal(footer[2].replace(',', '')):
                            raise RuntimeError(f'支付宝明细{label}金额与官方合计不一致')
                        counts.append(int(footer[1].replace(',', '')))
                    if sum(counts) != len(rows):
                        raise RuntimeError('支付宝明细行数与官方收入/支出笔数不一致')
                tables[kind] = {'headers':headers,'rows':rows,'account':account[1]}
    except (zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, zipfile.BadZipFile):
            raise RuntimeError('支付宝下载文件不是有效的官方 ZIP 账单') from None
        raise
    if set(tables) != {'detail', 'summary'}:
        raise RuntimeError('支付宝 ZIP 缺少账务明细或账务汇总 CSV')
    if tables['detail']['account'] != tables['summary']['account']:
        raise RuntimeError('支付宝明细与汇总属于不同账号')
    return tables


def prepare_workbook(folder, source, stem, month):
    tables = read_package(source, month)
    shops = []
    for kind, name in [('detail','账务明细'), ('summary','账务汇总')]:
        table = tables[kind]
        headers = table['headers']
        numeric = {i for i, h in enumerate(headers) if '金额' in h or '余额' in h or '笔数' in h}
        path = Path(folder) / f'{stem}-{kind}.jsonl'
        with path.open('w', encoding='utf8') as handle:
            for row in table['rows']:
                values = list(row)
                for i in numeric:
                    raw = row[i].strip().replace(',', '')
                    if raw:
                        number = Decimal(raw)
                        if number.is_finite() and len(number.as_tuple().digits) <= 15:
                            values[i] = int(number) if number == int(number) else float(number)
                handle.write(json.dumps(values, ensure_ascii=False) + '\n')
        shops.append({'id':kind,'name':name,'sheetName':name,'headers':headers,'rows':len(table['rows']),'path':str(path)})
    return {'headers':tables['detail']['headers'],'shops':shops,'rows':len(tables['detail']['rows']),'orders':0}
