"""
อ่านสลิปโอนเงินจาก 'รูปภาพ' (PNG/JPG) ด้วย Windows OCR (winrt)
— ไม่ต้องติดตั้ง OCR engine ภายนอก (ใช้ตัวที่มีในตัว Windows 10/11)

ใช้กรณีบัญชีเขียนแท็ก 'ai#EXP<เลข>' ลงในสลิป (เช่นช่องบันทึกช่วยจำตอนโอน KBIZ)
→ ระบบอ่านเลข EXP ออกมาแล้วเอาไปจับคู่กับเอกสารแบบ 'เลขตรงเป๊ะ' (กุญแจเฉพาะ)

หลักการปลอดภัย (fail-safe): ถ้า OCR อ่านไม่ได้/ไม่เจอเลข EXP → คืน record ที่มี
detail/amount ว่าง ให้ผู้ใช้พิมพ์/จับคู่เอง — ไม่มีการเดา
"""
import os
import re
import asyncio


def ocr_available() -> bool:
    """เช็คว่าเครื่องนี้ใช้ Windows OCR ได้ไหม (Windows 10/11 มีในตัว)"""
    try:
        import winrt.windows.media.ocr  # noqa: F401
        return True
    except Exception:
        return False


async def _ocr_async(path: str) -> str:
    from winrt.windows.storage import StorageFile, FileAccessMode
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language

    f = await StorageFile.get_file_from_path_async(os.path.abspath(path))
    stream = await f.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bmp = await decoder.get_software_bitmap_async()
    # เลขบิลเป็น ASCII (EXP+ตัวเลข) → ใช้ engine อังกฤษ; ถ้าไม่มีใช้ของโปรไฟล์ผู้ใช้
    engine = None
    try:
        engine = OcrEngine.try_create_from_language(Language("en-US"))
    except Exception:
        engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    result = await engine.recognize_async(bmp)
    return result.text or ""


def ocr_image_text(path: str) -> str:
    """อ่านข้อความทั้งหมดจากรูปด้วย Windows OCR — คืน '' ถ้าอ่านไม่ได้"""
    try:
        return asyncio.run(_ocr_async(path))
    except Exception:
        return ""


# แท็กเลขเอกสาร: ai#EXP0215444 / #EXP038977 / EXP2026060148 ฯลฯ
_EXP_RE = re.compile(r"(?:ai\s*#?\s*)?#?\s*(EXP\s*[0-9]{3,})", re.IGNORECASE)
_AMT_RE = re.compile(r"([0-9][0-9,]*\.[0-9]{2})")
# วันที่แบบ '29 Jun 26' หรือ '29 Jun 2026'
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _extract_exp(text: str) -> str:
    """ดึงเลขเอกสาร EXP จากข้อความ OCR (รวมช่องว่างที่ OCR แทรกออก)"""
    m = _EXP_RE.search(text or "")
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(1)).upper()   # 'EXP 0215444' → 'EXP0215444'


def _extract_amount(text: str) -> float:
    """ดึงยอดเงิน (ตัวเลขที่มีทศนิยม 2 ตำแหน่ง ตัวที่มากสุด = ยอดโอนหลัก)"""
    best = 0.0
    for m in _AMT_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
            if v > best:
                best = v
        except ValueError:
            pass
    return best


def _extract_date(text: str) -> str:
    m = _DATE_RE.search(text or "")
    if not m:
        return ""
    dd, mon, yy = m.group(1), m.group(2).title(), m.group(3)
    mo = _MONTHS.get(mon)
    if not mo:
        return ""
    y = int(yy)
    if y < 100:            # '26' → 2026 (ค.ศ.)
        y += 2000
    try:
        return f"{y:04d}-{mo:02d}-{int(dd):02d}"
    except Exception:
        return ""


def parse_slip_image(path: str) -> dict:
    """อ่านรูปสลิป 1 รูป → คืน record รูปแบบเดียวกับที่ parse_payment_report ใช้
    keys: recv_name, amount, pay_date, detail, ref, recv_acct, _img_path, _source
    detail จะมีแท็ก 'ai#EXP...' เพื่อให้ตัวจับคู่เดิม (กฎเลขอ้างอิงตรง) จับได้เลย"""
    text = ocr_image_text(path)
    exp = _extract_exp(text)
    amount = _extract_amount(text)
    pay_date = _extract_date(text)
    # ชื่อผู้รับ (best-effort): คำหลัง 'To' จนถึงคำว่า Bank/ธนาคาร (ถ้าหาไม่เจอใช้ชื่อไฟล์)
    recv_name = ""
    mto = re.search(r"\bTo\b\s+(.+?)\s+(?:Bank|Kasikorn|Bangkok|SCB|Krung|ธนาคาร|xxx)",
                    text, re.IGNORECASE)
    if mto:
        recv_name = mto.group(1).strip()
    if not recv_name:
        recv_name = os.path.splitext(os.path.basename(path))[0]
    return {
        "recv_name": recv_name,
        "amount": amount,
        "pay_date": pay_date,
        # ใส่เลข EXP ให้เด่น + ข้อความ OCR ทั้งหมด (ตัวจับคู่ค้นเลขอ้างอิงในนี้)
        "detail": (f"ai#{exp} " if exp else "") + (text or ""),
        "ref": "",
        "recv_acct": "",
        "_img_path": path,
        "_ocr_exp": exp,
        "_ocr_text": text,
        "_source": "image",
    }
