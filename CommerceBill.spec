# -*- mode: python ; coding: utf-8 -*-
"""电商账单打包配置：浏览器运行端由 APP 首次使用时从官方安装。"""
import sys
from pathlib import Path

SOURCE = Path(SPECPATH)

a = Analysis(
    ['app.py'],
    pathex=[str(SOURCE)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['websocket', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='CommerceBill',
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name='CommerceBill')

# macOS 交付形态：.app 包。Windows 上 PyInstaller 会忽略 BUNDLE。
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='电商账单.app',
        bundle_identifier='com.commercebill.export',
        info_plist={
            'CFBundleName': '电商账单',
            'CFBundleDisplayName': '电商账单',
            'CFBundleShortVersionString': '0.6.3',
            'CFBundleVersion': '0.6.3',
            'LSMinimumSystemVersion': '11.0',
            'NSHighResolutionCapable': True,
        },
    )
