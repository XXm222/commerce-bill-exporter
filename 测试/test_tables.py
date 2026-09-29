"""平台明细读取器测试：长数字不丢精度、不转科学计数法，日期与编码正确处理。

xlsx 用标准库手工构造，好处是能精确控制单元格的原始存储形式
（共享字符串 / 内联字符串 / 数值原文写成科学计数法 / 空单元格），
这些正是"订单号变成科学计数法"这类线上问题的真实来源。
"""
import datetime as dt, json, os, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0, os.environ.get('COMMERCE_SOURCE', str(Path(__file__).parent / 'source')))
from tables import read_table, read_xlsx, read_csv, sheet_names, format_number, format_date, column_index

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''

WORKBOOK = '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="明细" sheetId="1" r:id="rId1"/></sheets></workbook>'''

WORKBOOK_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''

# xf[0] 普通，xf[1] 日期(numFmtId=14)，xf[2] 自定义日期格式
STYLES = '''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="200" formatCode="yyyy&quot;年&quot;mm&quot;月&quot;dd&quot;日&quot;"/></numFmts>
<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="200"/></cellXfs>
</styleSheet>'''

SHARED = '''<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">
<si><t>店铺名称</t></si><si><t>订单号</t></si><si><t>测试店铺</t></si>
</sst>'''


def build_xlsx(path, sheet_xml):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', CONTENT_TYPES)
        archive.writestr('_rels/.rels', ROOT_RELS)
        archive.writestr('xl/workbook.xml', WORKBOOK)
        archive.writestr('xl/_rels/workbook.xml.rels', WORKBOOK_RELS)
        archive.writestr('xl/styles.xml', STYLES)
        archive.writestr('xl/sharedStrings.xml', SHARED)
        archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)


SERIAL = (dt.date(2026, 9, 1) - dt.date(1899, 12, 30)).days
SHEET = f'''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="inlineStr"><is><t>金额</t></is></c><c r="D1" t="inlineStr"><is><t>发货时间</t></is></c></row>
<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="inlineStr"><is><t>123456789012345678901</t></is></c><c r="C2"><v>39.9</v></c><c r="D2" s="1"><v>{SERIAL}</v></c></row>
<row r="3"><c r="A3" t="s"><v>2</v></c><c r="B3"><v>1.2345678901234568E+17</v></c><c r="C3"><v>0.1</v></c><c r="D3" s="2"><v>{SERIAL + 1}</v></c></row>
<row r="4"><c r="A4" t="s"><v>2</v></c><c r="C4"><v>5</v></c></row>
</sheetData></worksheet>'''


class Reader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.xlsx = Path(cls.folder.name) / 'detail.xlsx'
        build_xlsx(cls.xlsx, SHEET)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_number_formatting(self):
        # 这是"订单号变科学计数法"的正面防线。
        self.assertEqual(format_number('1.23E+18'), '1230000000000000000')
        self.assertEqual(format_number('1.2345678901234568E+17'), '123456789012345680')
        self.assertEqual(format_number('39.9'), '39.9')
        self.assertEqual(format_number('0.1'), '0.1')
        self.assertEqual(format_number('0'), '0')
        self.assertEqual(format_number(''), '')
        self.assertEqual(format_number('不是数字'), '不是数字')
        # 不得出现科学计数法字样
        for raw in ('1E+20', '9.9E-5', '12345678901234567890'):
            self.assertNotIn('E', format_number(raw).upper())

    def test_date_formatting(self):
        self.assertEqual(format_date(str(SERIAL)), '2026-09-01')
        self.assertEqual(format_date(str(SERIAL + 0.5)), '2026-09-01 12:00:00')

    def test_column_index(self):
        self.assertEqual([column_index(r) for r in ('A1', 'B1', 'Z1', 'AA1', 'AB3')], [0, 1, 25, 26, 27])

    def test_read_xlsx(self):
        headers, rows = read_xlsx(self.xlsx)
        self.assertEqual(headers, ['店铺名称', '订单号', '金额', '发货时间'])
        self.assertEqual(len(rows), 3)
        # 文本型长订单号原样保留
        self.assertEqual(rows[0][1], '123456789012345678901')
        # 数值原文为科学计数法 -> 还原为完整数字，而不是浮点近似
        self.assertEqual(rows[1][1], '123456789012345680')
        self.assertEqual(rows[1][2], '0.1')
        self.assertEqual(rows[0][2], '39.9')
        # 内置日期格式与自定义日期格式都要识别
        self.assertEqual(rows[0][3], '2026-09-01')
        self.assertEqual(rows[1][3], '2026-09-02')
        # 缺失单元格补空，列数对齐
        self.assertEqual(rows[2], ['测试店铺', '', '5', ''])
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_read_csv_encodings(self):
        plain = Path(self.folder.name) / 'a.csv'
        plain.write_text('店铺名称,订单号\n店铺甲,123456789012345678901\n', encoding='utf-8-sig')
        headers, rows = read_csv(plain)
        self.assertEqual(headers, ['店铺名称', '订单号'])
        self.assertEqual(rows[0][1], '123456789012345678901')
        # 中文平台常见 GB18030 导出
        legacy = Path(self.folder.name) / 'b.csv'
        legacy.write_bytes('店铺名称,订单号\n测试店铺,99\n'.encode('gb18030'))
        headers, rows = read_csv(legacy)
        self.assertEqual(headers[0], '店铺名称')
        self.assertEqual(rows[0][0], '测试店铺')

    def test_read_table_dispatch(self):
        self.assertEqual(read_table(self.xlsx)[0][0], '店铺名称')
        unsupported = Path(self.folder.name) / 'c.xls'
        unsupported.write_bytes(b'\xd0\xcf\x11\xe0')
        with self.assertRaisesRegex(RuntimeError, '旧版 .xls'):
            read_table(unsupported)

    def test_no_scientific_notation_end_to_end(self):
        """整表遍历：任何单元格都不允许出现科学计数法。"""
        _, rows = read_xlsx(self.xlsx)
        for row in rows:
            for value in row:
                self.assertNotRegex(value, r'[0-9][eE][+-][0-9]', f'出现科学计数法: {value}')

    def test_trailing_tabs_stripped(self):
        """真实天猫货款收入明细的每个数据字段都带尾随 \\t（表头行没有），必须去掉。"""
        raw = Path(self.folder.name) / 'tabbed.csv'
        raw.write_bytes('账期,订单号,数量\n20260803\t,1234567890123456789\t,1\t\n'.encode('gb18030'))
        headers, rows = read_csv(raw)
        self.assertEqual(headers, ['账期', '订单号', '数量'])
        self.assertEqual(rows[0], ['20260803', '1234567890123456789', '1'])
        self.assertNotIn('\t', ''.join(rows[0]))

    def test_roundtrip_with_project_writer(self):
        """用项目自己的 Excel 写入器产出真实 xlsx 再读回，验证长数字与中文都无损。"""
        import xlsx_writer
        folder = Path(self.folder.name) / 'roundtrip'
        folder.mkdir(exist_ok=True)
        headers = ['店铺名称', '订单号', '金额', '发货时间']
        shop = folder / 'shop-1.jsonl'
        shop.write_text(json.dumps(['测试店铺', '123456789012345678901', 39.9,
                                    {'date': '2026-09-01T00:00:00Z'}], ensure_ascii=False) + '\n', encoding='utf8')
        index = {'headers': headers, 'dateColumns': [3], 'numericColumns': [2], 'rows': 1, 'orders': 1,
                 'shops': [{'id': '1', 'name': '测试店铺', 'rows': 1, 'path': str(shop), 'sheetName': '测试店铺'}]}
        (folder / 'sheets.json').write_text(json.dumps(index, ensure_ascii=False), encoding='utf8')
        out = folder / 'out.xlsx'
        xlsx_writer.write(out, folder / 'sheets.json')
        heads, rows = read_xlsx(out)
        self.assertEqual(heads, headers)
        self.assertEqual(rows[0][0], '测试店铺')
        # 关键：21 位订单号经写入再读出必须逐字一致，且仍是文本
        self.assertEqual(rows[0][1], '123456789012345678901')
        self.assertEqual(rows[0][2], '39.9')
        self.assertTrue(rows[0][3].startswith('2026-09-01'), rows[0][3])

    def test_a_login_page_saved_as_xlsx_is_reported_in_plain_chinese(self):
        """登录态失效时「下载」常下到一个登录页，后缀却还是 .xlsx。

        那时 zipfile 的原话是英文的「File is not a zip file」——既没说发生了什么，
        也没说该怎么办；而这条消息会直接进任务记录显示给用户。这里要求换成中文，
        并指出最可能的原因（下到的是登录页/错误页）。
        """
        path = Path(self.folder.name) / 'login-page.xlsx'
        path.write_bytes('<html><body>请先登录</body></html>'.encode('utf8'))
        for read in (read_xlsx, sheet_names, read_table):
            with self.assertRaises(RuntimeError) as caught:
                read(path)
            message = str(caught.exception)
            self.assertIn('不是有效的 Excel', message, f'{read.__name__} 的提示不明确')
            self.assertIn('登录页', message, '要指出最可能的原因')
            self.assertNotIn('zip file', message, '不要把库的英文原文丢给用户')

    def test_a_real_xlsx_is_unaffected(self):
        """对照：正常文件照读，新包装不能把成功路径弄坏。"""
        self.assertEqual(sheet_names(self.xlsx), ['明细'])
        headers, rows = read_xlsx(self.xlsx)
        self.assertTrue(headers and rows, '正常工作簿必须照常读出内容')
        self.assertEqual(rows[0][1], '123456789012345678901')


if __name__ == '__main__':
    unittest.main()
