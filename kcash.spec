# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for KCash Queue System (PyQt6 Desktop App).
Build: pyinstaller kcash.spec
Output: dist\KCash Queue System\KCash Queue System.exe
"""

block_cipher = None

from PyInstaller.utils.hooks import collect_all

# bundle pdfplumber + pdfminer.six (ใช้ในระบบตัดบิล/อ่านสลิป PDF)
# + winrt (Windows OCR — อ่านสลิปจากรูปภาพที่มีแท็ก ai#EXP)
_slip_datas, _slip_binaries, _slip_hidden = [], [], []
for _pkg in ('pdfplumber', 'pdfminer', 'pypdfium2', 'pypdfium2_raw', 'reportlab', 'PIL',
             'winrt'):
    try:
        d, b, h = collect_all(_pkg)
        _slip_datas += d; _slip_binaries += b; _slip_hidden += h
    except Exception:
        pass

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=_slip_binaries,
    datas=[('icon/icon.ico', 'icon'), ('icon/icon.png', 'icon')] + _slip_datas,
    hiddenimports=[
        'account_memory',
        'rejected',
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.sip',
        'requests',
        'difflib',
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        'et_xmlfile',
        'pdfplumber',
        'pdfminer',
        'pdfminer.high_level',
        'reportlab',
        'reportlab.pdfbase.ttfonts',
        'reportlab.platypus',
        'psutil',
        'PIL',
        'PIL.Image',
        'slip_ocr',
        'winrt',
        'winrt.runtime',
        'winrt.windows.media.ocr',
        'winrt.windows.graphics.imaging',
        'winrt.windows.storage',
        'winrt.windows.storage.streams',
        'winrt.windows.globalization',
        'winrt.windows.foundation',
        'winrt.windows.foundation.collections',
        'winrt.system',
    ] + _slip_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['flask', 'werkzeug', 'jinja2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KCash Queue System',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon\\icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KCash Queue System',
)
