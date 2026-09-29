"""读取平台导出的明细文件（xlsx / csv），供各平台整理成按店铺分 Sheet 的工作簿。

只使用标准库，与 workbook.py 的 xlsx 校验方式一致，不把 openpyxl 引入为生产依赖。

硬约束：订单号等长数字必须保持文本，绝不输出科学计数法。
xlsx 的数值单元格即使原文写成 1.2345678901234568E+17，也按 Decimal 还原为完整数字；
文本单元格（共享字符串 / 内联字符串）一律原样保留。
"""
import csv, datetime as dt, re, zipfile
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
# Excel 内置的日期/时间格式编号（含中日韩日期格式），自定义格式另行按 formatCode 判断。
DATE_BUILTIN = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
CELL_REF = re.compile(r'([A-Z]+)(\d+)')
DATE_TOKENS = re.compile(r'[yYmMdDhHsS]')


def column_index(reference):
    """A1 -> 0, B1 -> 1, AA1 -> 26。"""
    letters = CELL_REF.match(reference).group(1)
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - 64)
    return value - 1


def format_number(raw):
    """把 Excel 数值原文还原成完整十进制字符串，避免科学计数法丢精度。"""
    text = (raw or '').strip()
    if not text:
        return ''
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if not number.is_finite():
        return text
    if number == number.to_integral_value():
        return format(number.quantize(Decimal(1)), 'f')
    return format(number.normalize(), 'f')


def format_date(raw):
    """Excel 序列号（1899-12-30 起算）转成 YYYY-MM-DD[ HH:MM:SS]。"""
    try:
        serial = float(raw)
    except (TypeError, ValueError):
        return raw or ''
    if serial <= 0:
        return raw or ''
    moment = dt.datetime(1899, 12, 30) + dt.timedelta(days=serial)
    if moment.hour or moment.minute or moment.second:
        return moment.strftime('%Y-%m-%d %H:%M:%S')
    return moment.strftime('%Y-%m-%d')


def _date_styles(archive):
    """返回 xf 序号集合，这些样式代表日期或时间。"""
    try:
        root = ET.fromstring(archive.read('xl/styles.xml'))
    except (KeyError, ET.ParseError):
        return set()
    custom = {}
    for node in root.findall('s:numFmts/s:numFmt', NS):
        code = node.attrib.get('formatCode', '')
        # 去掉引号内的字面量与颜色/条件段，只留格式占位符。
        cleaned = re.sub(r'"[^"]*"|\[[^\]]*\]', '', code)
        custom[int(node.attrib['numFmtId'])] = bool(DATE_TOKENS.search(cleaned))
    styles = set()
    for index, node in enumerate(root.findall('s:cellXfs/s:xf', NS)):
        fmt = int(node.attrib.get('numFmtId', 0))
        if custom.get(fmt, fmt in DATE_BUILTIN):
            styles.add(index)
    return styles


def _shared_strings(archive):
    try:
        root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
    except (KeyError, ET.ParseError):
        return []
    return [''.join(node.itertext()) for node in root.findall('s:si', NS)]


def _sheet_entries(archive):
    """按工作簿里的顺序返回 [(Sheet 名, 归档内路径)]。

    原来只取第一个 Sheet 的路径，核对多 Sheet 工作簿（聚水潭每个店铺一个 Sheet）时
    只能读到第一张，验收工具因此没法逐张比对。
    """
    try:
        root = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    except (KeyError, ET.ParseError):
        return []
    targets = {rel.attrib.get('Id'): rel.attrib.get('Target', '') for rel in rels}
    entries = []
    for node in root.findall('s:sheets/s:sheet', NS):
        relationship = node.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target = targets.get(relationship, '')
        entries.append((node.attrib.get('name') or '', 'xl/' + target.lstrip('/').replace('xl/', '')))
    return entries


def open_xlsx(path):
    """打开 xlsx（它其实是个 zip 容器）；不是有效 xlsx 时给一句能指向原因的提示。

    平台下载回来的文件**未必是明细**：登录态失效时点「下载」，常常下到一个登录页或错误页，
    而文件名后缀仍然是 .xlsx。那时 zipfile 的原话是「File is not a zip file」——英文，
    而且完全没说发生了什么、该怎么办。这里换成中文并指出最可能的原因。
    """
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise RuntimeError(f'这个文件不是有效的 Excel（xlsx）：{path}。'
                           f'如果它是刚从平台下载的，多半下到的是登录页或错误页——'
                           f'请确认登录态仍然有效，然后重新导出。') from None


def sheet_names(path):
    """工作簿里的 Sheet 名，按工作簿顺序。"""
    with open_xlsx(path) as archive:
        return [name for name, _ in _sheet_entries(archive)]


def read_xlsx(path, sheet=None):
    """读取工作簿的一个 Sheet，返回 (headers, rows)，全部为字符串。

    sheet 为 None 时读第一个 Sheet（与原行为一致）；也可以给 Sheet 名或 1 起的序号，
    用于逐张核对多 Sheet 工作簿。
    """
    table = []
    with open_xlsx(path) as archive:
        shared = _shared_strings(archive)
        dates = _date_styles(archive)
        entries = _sheet_entries(archive) or [('', 'xl/worksheets/sheet1.xml')]
        if sheet is None:
            target = entries[0][1]
        elif isinstance(sheet, int):
            if not 1 <= sheet <= len(entries):
                raise KeyError(f'工作簿只有 {len(entries)} 个 Sheet，取不到第 {sheet} 个')
            target = entries[sheet - 1][1]
        else:
            match = next((path_ for name, path_ in entries if name == sheet), None)
            if match is None:
                raise KeyError(f'工作簿里没有名为 {sheet!r} 的 Sheet')
            target = match
        with archive.open(target) as stream:
            for _, element in ET.iterparse(stream, events=('end',)):
                if element.tag != '{' + NS['s'] + '}row':
                    continue
                cells = {}
                for cell in element.findall('s:c', NS):
                    reference = cell.attrib.get('r')
                    if not reference:
                        continue
                    kind = cell.attrib.get('t', 'n')
                    if kind == 'inlineStr':
                        value = ''.join(cell.itertext())
                    elif kind == 's':
                        node = cell.find('s:v', NS)
                        index = int(node.text) if node is not None and node.text else -1
                        value = shared[index] if 0 <= index < len(shared) else ''
                    else:
                        node = cell.find('s:v', NS)
                        raw = node.text if node is not None and node.text else ''
                        if kind in ('str', 'e', 'b'):
                            value = raw
                        elif int(cell.attrib.get('s', 0)) in dates:
                            value = format_date(raw)
                        else:
                            value = format_number(raw)
                    cells[column_index(reference)] = value
                width = max(cells) + 1 if cells else 0
                table.append([cells.get(i, '') for i in range(width)])
                element.clear()
    if not table:
        return [], []
    width = max(len(row) for row in table)
    table = [row + [''] * (width - len(row)) for row in table]
    return table[0], table[1:]


def clean(value):
    """去掉平台导出常见的尾随制表符/换行。

    真实天猫货款收入明细的每个字段都带尾随 \\t（表头行没有），
    与 workbook.py 既有的 strip('\\t\\r\\n') 约定保持一致。
    """
    return value.strip('\t\r\n') if isinstance(value, str) else value


def read_csv(path):
    """读取 CSV；中文平台导出常见 GB18030 编码，按 utf-8-sig 优先回退。"""
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(path, 'r', encoding=encoding, newline='') as handle:
                table = [row for row in csv.reader(handle)]
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f'无法识别文件编码：{path}')
    table = [[clean(cell) for cell in row] for row in table]
    table = [row for row in table if any(cell.strip() for cell in row)]
    if not table:
        return [], []
    width = max(len(row) for row in table)
    table = [row + [''] * (width - len(row)) for row in table]
    return table[0], table[1:]


def read_table(path):
    """按扩展名分派；xls（旧二进制格式）不支持，明确报错而不是静默出错。"""
    suffix = path.suffix.lower()
    if suffix == '.xlsx' or suffix == '.xlsm':
        return read_xlsx(path)
    if suffix in ('.csv', '.txt'):
        return read_csv(path)
    if suffix == '.xls':
        raise RuntimeError('平台返回的是旧版 .xls 格式，请改为导出 xlsx 或 csv')
    raise RuntimeError(f'不支持的明细文件类型：{path.name}')
