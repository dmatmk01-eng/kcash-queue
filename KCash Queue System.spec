# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# OCR ไทยสำหรับอ่านสลิปรูปภาพ (RapidOCR + onnxruntime) — โมเดลไทยอยู่ใน ocr_models/
_ro_datas, _ro_bins, _ro_hidden = collect_all('rapidocr')
# ไม่เอาโมเดลจีนที่มากับแพ็กเกจ (ใหญ่/ไม่ได้ใช้) — ใช้โมเดลใน ocr_models/ แทน
_ro_datas = [d for d in _ro_datas
             if (os.sep + 'rapidocr' + os.sep + 'models' + os.sep) not in d[0]]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_ro_bins,
    datas=[('icon', 'icon'), ('ocr_models', 'ocr_models')] + _ro_datas,
    hiddenimports=['cloud_log', 'cloud_sync', 'expense_tiers', 'slip_ocr', 'onnxruntime', 'cv2',
                   'shapely', 'pyclipper', 'omegaconf', 'colorlog', 'yaml'] + _ro_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# ตัดไฟล์วิดีโอของ OpenCV (ffmpeg ~30MB) ออก — OCR อ่านแค่รูปภาพ ไม่ใช้
a.binaries = [b for b in a.binaries if 'opencv_videoio_ffmpeg' not in b[0]]
pyz = PYZ(a.pure)

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
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KCash Queue System',
)
