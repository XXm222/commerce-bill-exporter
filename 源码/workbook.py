import csv, datetime as dt, json, re, zipfile
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET
import jst_export as jst
NS = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
# workbook.xml 里每个 sheet 用 r:id 指向 _rels/workbook.xml.rels 里的一条关系，
# 再由那条关系指向真正的工作表部件。名字与内容的绑定走的就是这两跳。
REL_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
NUMERIC = {'商品数量','实发数量','出库单数量','单位用量','总用量','商品金额','买家运费','商家优惠',
           '商家运费','其中分销订单运费','其中分销订单优惠金额','实发金额','商品成本','付款金额',
           '已核销到账金额','【商品资料】：基本售价','【商品资料】：市场|吊牌价',
           '【商品资料】：长(cm)','【商品资料】：宽(cm)','【商品资料】：高(cm)',
           '【商品资料】：体积(cm3)','【商品资料】：重量(kg)'}
DATE_FIELDS = {'订单时间','支付时间','发货时间','订单完成时间'}
DATE_COLUMN = {'send_date':'发货时间','pay_date':'支付时间','soendtime':'订单完成时间'}


def clean_sheet_name(name, shop_id, used):
    value = re.sub(r'[\[\]:*?/\\\x00-\x1f]','_',name).strip(" '") or f'店铺_{shop_id}'
    def cut(text, limit):
        while len(text.encode('utf-16-le'))//2 > limit:
            text = text[:-1]
        return text
    value = cut(value,31)
    if value.lower() in used or value.lower() == 'history':
        suffix = '~'+str(shop_id)
        value = cut(value,31-len(suffix))+suffix
    if value.lower() in used:
        raise RuntimeError('店铺 Sheet 名称冲突，请核查店铺编码')
    used.add(value.lower())
    return value


def parse_date(value):
    value = value.strip()
    for pattern in ('%Y/%m/%d %H:%M:%S','%Y-%m-%d %H:%M:%S','%Y/%m/%d','%Y-%m-%d'):
        try:
            return dt.datetime.strptime(value,pattern)
        except ValueError:
            pass
    raise ValueError(f'无法识别平台日期：{value[:40]}')


def month_count_gaps(folder, task):
    """跨月失败时从保留的各月原始文件定位缺口，不重新请求平台。"""
    gaps=[]
    for month, segment in sorted((task.get('segments') or {}).items()):
        source=folder/f'source-{month}.csv'
        expected=segment.get('orderCount')
        if not source.is_file() or not isinstance(expected,int):
            continue
        with source.open('r',encoding='utf-8-sig',newline='') as handle:
            reader=csv.reader(handle)
            headers=next(reader,None)
            if not headers or '内部订单号' not in headers:
                continue
            order_index=headers.index('内部订单号')
            actual={row[order_index].strip() for row in reader if len(row)>order_index and row[order_index].strip()}
        if len(actual)!=expected:
            gaps.append(f'{month} 查询 {expected:,} 单、商品文件 {len(actual):,} 单（少 {expected-len(actual):,} 单）')
    return '；'.join(gaps)


def source_order_counts(source, pdd_shops):
    """只读统计平台原始文件里的订单归属；不把商品行数误当订单数。"""
    with source.open('r',encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        if not reader.fieldnames or not {'内部订单号','店铺编码'}.issubset(reader.fieldnames):
            raise RuntimeError('平台商品文件缺少内部订单号或店铺编码')
        pdd,other=set(),set()
        for row in reader:
            order=(row.get('内部订单号') or '').strip()
            shop=(row.get('店铺编码') or '').strip()
            if not order or not shop:raise RuntimeError('平台商品文件缺少内部订单号或店铺编码')
            (pdd if shop in pdd_shops else other).add(order)
    if pdd & other:raise RuntimeError('同一内部订单号同时属于拼多多和其他店铺')
    return len(pdd),len(other)


def prepare_sheets(source, folder, task, pdd_warnings=None):
    """一次读取全部店铺 CSV，按店铺编码归组。订单号始终保留为文本。"""
    groups, handles, order_ids = {}, {}, set()
    rows = 0
    try:
        with source.open('r',encoding='utf-8-sig',newline='') as file:
            reader = csv.reader(file)
            headers = next(reader)
            required = ('店铺名称','店铺编码','内部订单号',DATE_COLUMN[task['dateField']])
            if not set(required).issubset(headers):
                raise RuntimeError('平台文件缺少店铺、订单或日期列，无法分 Sheet')
            name_i, shop_i, order_i, time_i = [headers.index(x) for x in required]
            date_columns = [i for i,name in enumerate(headers) if name in DATE_FIELDS]
            numeric_columns = [i for i,name in enumerate(headers) if name in NUMERIC]
            start,end = dt.date.fromisoformat(task['start']),dt.date.fromisoformat(task['end'])
            for row in reader:
                if len(row) != len(headers):
                    raise RuntimeError('平台 CSV 列数不一致，停止生成 Excel')
                shop_id,name,order = row[shop_i].strip(),row[name_i].strip(),row[order_i].strip()
                # 供销店铺使用负数编码，属于全部店铺的真实范围。
                if not re.fullmatch(r'-?\d+',shop_id) or not name or not order:
                    raise RuntimeError('明细缺少有效店铺编码、店铺名称或订单号')
                if not start <= parse_date(row[time_i]).date() <= end:
                    raise RuntimeError('平台返回日期范围之外的数据，停止生成 Excel')
                if shop_id not in groups:
                    path = folder/f'shop-{shop_id}.jsonl'
                    groups[shop_id] = {'id':shop_id,'name':name,'rows':0,'path':str(path)}
                    handles[shop_id] = path.open('w',encoding='utf-8')
                if groups[shop_id]['name'] != name:
                    raise RuntimeError('同一店铺编码出现不同店铺名称，请核查平台数据')
                values = [value.strip('\t\r\n') for value in row]
                for i in date_columns:
                    if values[i].strip():
                        values[i] = {'date':parse_date(values[i]).strftime('%Y-%m-%dT%H:%M:%SZ')}
                for i in numeric_columns:
                    value = values[i].strip()
                    if value:
                        try:
                            number = Decimal(value)
                        except InvalidOperation:
                            raise RuntimeError(f'数值列「{headers[i]}」出现非数值') from None
                        if not number.is_finite():
                            raise RuntimeError('数值列包含无效金额或数量')
                        # Excel 仅支持 15 位有效数字。超长数值保留文本。
                        if len(number.as_tuple().digits) <= 15:
                            values[i] = int(number) if number == int(number) else float(number)
                handles[shop_id].write(json.dumps(values,ensure_ascii=False)+'\n')
                groups[shop_id]['rows'] += 1
                if groups[shop_id]['rows'] > 1048575:
                    raise RuntimeError(f'店铺「{name}」超过一个 Sheet 的 Excel 行数上限，请缩短日期范围')
                order_ids.add(order)
                rows += 1
    finally:
        for handle in handles.values():
            handle.close()
    pdd_warnings=pdd_warnings or []
    accepted_gap=sum(item['missing'] for item in pdd_warnings)
    if not rows and not accepted_gap:
        raise RuntimeError('查询有订单但导出文件没有商品明细，请核查平台口径')
    if len(order_ids)+accepted_gap != task['orderCount']:
        details=month_count_gaps(folder,task)
        detail=f' 分月差额：{details}。' if details else ''
        raise RuntimeError(f"完整性检查未通过：查询 {task['orderCount']:,} 单，商品文件 {len(order_ids):,} 单。"
                           f'{detail}原始文件已保留；平台商品导出与查询结果不一致，不能标记为全量。')
    used = {'导出说明'} if pdd_warnings else set()
    result = sorted(groups.values(),key=lambda shop:(shop['name'],shop['id']))
    for shop in result:
        shop['sheetName'] = clean_sheet_name(shop['name'],shop['id'],used)
    index = {'headers':headers,'dateColumns':date_columns,'numericColumns':numeric_columns,
             'shops':result,'rows':rows,'orders':len(order_ids)}
    if pdd_warnings:
        note=folder/'export-notice.jsonl'
        lines=[['导出状态','部分明细：拼多多订单商品文件少于页面查询结果'],
               ['用户选择日期',f"{task['start']} 至 {task['end']}"],
               ['查询订单数',task['orderCount']],['文件已有订单数',len(order_ids)],
               ['拼多多缺失订单数',accepted_gap],
               ['使用提醒','文件仅包含平台已提供的明细；缺失的拼多多历史订单未补入，不能把本文件当全量账单。']]
        for item in pdd_warnings:
            lines.append([item['month'],f"拼多多查询 {item['pddQuery']:,} 单，商品文件 {item['pddExported']:,} 单，少 {item['missing']:,} 单"])
        note.write_text(''.join(json.dumps(line,ensure_ascii=False)+'\n' for line in lines),encoding='utf8')
        index['notice']={'sheetName':'导出说明','name':'导出说明','path':str(note),
                         'headers':['项目','内容'],'rows':len(lines)}
        index['pddWarnings']=pdd_warnings
    jst.write_json(folder/'sheets.json',index)
    return index


def verify_xlsx(path,index):
    """核对产出工作簿：Sheet 名与店铺一一对应、行数一致、没有意外公式。

    **名字到内容要按关系文件解析，不能按位置猜**（2026-09-28 改）。用户点开某个 Sheet 时，
    Excel 看到的是 workbook.xml.rels 指向的那份内容。原先这里只比较名字列表、再按序号去开
    xl/worksheets/sheet{i}.xml，于是「名字对、内容装的却是另一家店」这种情况会**照样通过**——
    而三处编号（workbook.xml 的 r:id、rels 的 Target、写文件时的 sheetN）本来就必须一致。
    现在按 r:id → Target 取部件，把这条一致性也验出来。
    """
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():
            raise RuntimeError('Excel 文件校验失败')
        root = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = root.findall('s:sheets/s:sheet',NS)
        names = [sheet.attrib['name'] for sheet in sheets]
        entries=index['shops']+([index['notice']] if index.get('notice') else [])
        expected = [shop['sheetName'] for shop in entries]
        if names != expected:
            raise RuntimeError('Excel Sheet 与店铺对应不一致')
        try:
            rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        except KeyError:
            raise RuntimeError('Excel 缺少工作表关系文件') from None
        targets = {rel.attrib.get('Id'): rel.attrib.get('Target','') for rel in rels}
        parts = set(archive.namelist())
        for position, (shop, sheet) in enumerate(zip(entries, sheets, strict=True), 1):
            name = sheet.attrib['name']
            target = targets.get(sheet.attrib.get(REL_ID,''), '')
            if not target:
                raise RuntimeError(f'Sheet「{name}」没有对应的工作表')
            part = 'xl/' + target.lstrip('/').replace('xl/','')
            if part not in parts:
                raise RuntimeError(f'Sheet「{name}」指向的工作表不存在：{part}')
            # 第 i 家店铺的行必须落在第 i 张工作表里。只比行数是不够的：两家店铺行数相同时，
            # 把两处关系对调（名字不动、内容互换）行数照样一一对上，而用户点开的 Sheet 里装的
            # 就是别人家的数据。三处编号（workbook.xml 的 r:id、rels 的 Target、写文件时的
            # sheetN）本来就必须一致，这里把最后这一跳也验出来。
            if part != f'xl/worksheets/sheet{position}.xml':
                raise RuntimeError(f'Sheet「{name}」对应的不是第 {position} 张工作表（{part}）')
            rows = 0
            notice_text=[]
            with archive.open(part) as stream:
                for _,element in ET.iterparse(stream,events=('end',)):
                    if element.tag == '{'+NS['s']+'}f':
                        raise RuntimeError('原始数据导出出现了意外公式')
                    if shop is index.get('notice') and element.tag == '{'+NS['s']+'}t':
                        notice_text.append(element.text or '')
                    if element.tag == '{'+NS['s']+'}row':
                        rows += 1
                        element.clear()
            if rows != shop['rows']+1:
                raise RuntimeError('Excel 明细行数与原始 CSV 不一致')
            if shop is index.get('notice') and ('拼多多缺失订单数' not in notice_text or '部分明细' not in ''.join(notice_text)):
                raise RuntimeError('Excel 导出说明缺少拼多多缺失提醒')
