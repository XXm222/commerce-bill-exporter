"""流式写出 xlsx：内存与行数无关。

**为什么要自己写**：原先用 `@oai/artifact-tool` 在内存里建整本工作簿，实测约 0.5 MB/行——
2000 行 1.5 GB、8000 行 4.6 GB、16000 行直接 OOM（即便给了 --max-old-space-size=4096）。
而按月的聚水潭导出远超这个规模（真实整月约 4 万单），也就是说那条路根本走不到头：
平台那边下载成功了，工作簿却生不出来。这里改成直接写 SpreadsheetML 并流式压缩，
内存只与单行有关；同时不再需要那个约 150MB 的依赖。

产物必须保持这些形态：长数字是文本、金额是数字、日期是真日期、冻结窗格在 D2，
并且能被项目自己的读取器读回。

本模块**只作为库被 import 调用**（engine 与 app 都是进程内调用），没有子进程出入口：
旧实现是 node 子进程按 stdout 打 `{"type":"sheet"}` 行来报进度，那套协议随 @oai/artifact-tool
一起删掉了，别再照旧样子加回一个 `__main__`——它既不会被走到，还会让人以为还有子进程协议。
"""
import json, zipfile
from datetime import datetime
from pathlib import Path

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
EPOCH = datetime(1899, 12, 30)          # Excel 1900 日期系统的起算点
HEADER_STYLE = 1
# Excel 每个工作表的上限。写入器自己守着这两条：调用方各自检查容易漏（例如天猫链路只查了列数），
# 而超限的后果不是报错而是**产出打不开的文件**——所以守在最靠近产物的地方。
MAX_ROWS = 1048576
MAX_COLUMNS = 16384
DATE_STYLE = 2                          # numFmtId 22 = 内置的「日期+时间」，读取器认它
# XML 1.0 不允许这些控制字符，平台数据里偶有脏字符，写进去会让工作簿打不开。
ILLEGAL = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}


def column_name(index):
    """0 基列号 -> A、B、…、AA。"""
    name = ''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def escape(text):
    """转义 XML 里需要转义的字符。

    双引号也必须转：工作表名是写进**属性** name="…" 的，而 Excel 允许店铺名里出现双引号
    （clean_sheet_name 只处理 : \\ / ? * [ ]），不转就会把属性提前闭合，整个工作簿损坏、打不开。
    """
    return (text.translate(ILLEGAL)
                .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                .replace('"', '&quot;'))


def serial(moment):
    """ISO 时间 -> Excel 序列号。用整数秒换算，避免浮点误差把 23:59:59 变成 23:59:58.999…"""
    value = datetime.strptime(moment, '%Y-%m-%dT%H:%M:%SZ')
    delta = value - EPOCH
    return delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6


def cell(reference, value):
    """按 JSON 里的取值类型决定单元格形态；空值直接不写（读取器按空串处理）。"""
    if value is None:
        return ''
    if isinstance(value, dict):
        moment = value.get('date')
        if moment:
            return f'<c r="{reference}" s="{DATE_STYLE}"><v>{serial(moment):.12f}</v></c>'
        return ''
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value!r}</v></c>'
    text = str(value)
    if text == '':
        return ''
    # inlineStr + xml:space：不做共享字符串表，内存才不会随行数涨；保留前后空格。
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _header_xml(headers, width):
    parts = ['<row r="1">']
    for index, name in enumerate(headers):
        parts.append(f'<c r="{column_name(index)}1" s="{HEADER_STYLE}" t="inlineStr">'
                     f'<is><t xml:space="preserve">{escape(str(name))}</t></is></c>')
    parts.append('</row>')
    return ''.join(parts)


def _cols_xml(width):
    return (f'<cols><col min="1" max="{width}" width="22" customWidth="1"/></cols>')


def _write_sheet(handle, headers, rows_path):
    # zipfile 的流只接受 bytes
    write = lambda text: handle.write(text.encode('utf8'))
    if len(headers) > MAX_COLUMNS:
        raise RuntimeError(f'列数 {len(headers)} 超过 Excel 上限 {MAX_COLUMNS}')
    write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    write(f'<worksheet xmlns="{NS}">')
    # 冻结前三列与表头行：与之前用库产出的一致（openpyxl 读出来是 D2）
    write('<sheetViews><sheetView workbookViewId="0">'
          '<pane xSplit="3" ySplit="1" topLeftCell="D2" activePane="bottomRight" state="frozen"/>'
          '</sheetView></sheetViews>')
    write(_cols_xml(max(len(headers), 1)))
    write('<sheetData>')
    write(_header_xml(headers, len(headers)))
    number = 1
    with Path(rows_path).open(encoding='utf8') as lines:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            number += 1
            if number > MAX_ROWS:
                raise RuntimeError(f'行数超过 Excel 上限 {MAX_ROWS}，请缩短日期范围')
            row = json.loads(line)
            parts = [f'<row r="{number}">']
            for index, value in enumerate(row):
                part = cell(f'{column_name(index)}{number}', value)
                if part:
                    parts.append(part)
            parts.append('</row>')
            write(''.join(parts))
    write('</sheetData></worksheet>')


def _workbook_xml(shops):
    parts = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>']
    for index, shop in enumerate(shops, 1):
        name = escape(str(shop['sheetName']))
        parts.append(f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>')
    parts.append('</sheets></workbook>')
    return ''.join(parts)


def _rels_xml(count):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for index in range(1, count + 1):
        parts.append(f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" Target="worksheets/sheet{index}.xml"/>')
    parts.append(f'<Relationship Id="rId{count + 1}" Type="{REL_NS}/styles" Target="styles.xml"/>')
    parts.append('</Relationships>')
    return ''.join(parts)


def _content_types(count):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
             '<Default Extension="xml" ContentType="application/xml"/>',
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for index in range(1, count + 1):
        parts.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    parts.append('</Types>')
    return ''.join(parts)


ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
             '</Relationships>')

# 三个样式：0 普通、1 表头加粗、2 日期（numFmtId 22 = 内置的日期+时间，读取器认它）
STYLES = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{NS}">'
          '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
          '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="2"><fill><patternFill patternType="none"/></fill>'
          '<fill><patternFill patternType="gray125"/></fill></fills>'
          '<borders count="1"><border/></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="3">'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
          '<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '</cellXfs>'
          # 默认命名样式：缺了它 openpyxl 会警告「Workbook contains no default style」，
          # 而 Excel 对这种不完整的 styles.xml 可能弹「发现内容有问题」的修复提示。
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
          '</styleSheet>')


def write(destination, index_path, progress=None):
    """按 sheets.json 与各店铺 jsonl 写出工作簿。progress(done, total, name) 可选。"""
    index = json.loads(Path(index_path).read_text('utf8'))
    headers = index['headers']
    shops = index['shops'] + ([index['notice']] if index.get('notice') else [])
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix('.part')
    try:
        return _write_all(partial, destination, index, headers, shops, progress)
    except Exception:
        # 写失败（含超限中断）时不要留下半截文件：只有成功才改名成正式产物。
        partial.unlink(missing_ok=True)
        raise


def _write_all(partial, destination, index, headers, shops, progress):
    with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('[Content_Types].xml', _content_types(len(shops)))
        archive.writestr('_rels/.rels', ROOT_RELS)
        archive.writestr('xl/workbook.xml', _workbook_xml(shops))
        archive.writestr('xl/_rels/workbook.xml.rels', _rels_xml(len(shops)))
        archive.writestr('xl/styles.xml', STYLES)
        for number, shop in enumerate(shops, 1):
            with archive.open(f'xl/worksheets/sheet{number}.xml', 'w') as handle:
                _write_sheet(handle, shop.get('headers',headers), shop['path'])
            if progress:
                progress(number, len(shops), shop.get('name', ''))
    partial.replace(destination)
    return len(shops)
