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
# รองรับ OCR อ่านเพี้ยน: ช่องว่างแทรกในเลข + ตัวอักษรที่มักสับสนกับตัวเลข (O/o→0 ฯลฯ)
_AMT_RE = re.compile(r"([0-9][0-9,]*\.[0-9]{2})")


def _norm_exp_digits(s: str) -> str:
    """แปลงตัวอักษรที่ OCR มักอ่านสับสนให้เป็นตัวเลข (เฉพาะส่วนเลขหลัง EXP)"""
    table = {"O": "0", "o": "0", "D": "0", "Q": "0",
             "I": "1", "l": "1", "|": "1",
             "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2"}
    return "".join(table.get(ch, ch) for ch in s)
# วันที่แบบ '29 Jun 26' หรือ '29 Jun 2026'
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _extract_exp(text: str) -> str:
    """ดึงเลขเอกสาร EXP จากข้อความ OCR — เอาช่องว่างออก + แก้ตัวอักษรที่อ่านเพี้ยน
    'ai#EXP 021 5444' / 'EXPO215444' → 'EXP0215444'"""
    # เอาช่องว่างในตัวเลขออกก่อน (OCR ชอบแทรก) แล้วค้น EXP ตามด้วยเลข/ตัวอักษรที่ปนได้
    t = re.sub(r"\s+", "", text or "")
    m = re.search(r"EXP([0-9OoDQIl\|SsBZz]{3,})", t, re.IGNORECASE)
    if not m:
        return ""
    return "EXP" + _norm_exp_digits(m.group(1))


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


def _parse_slip_image_legacy(path: str) -> dict:
    """(สำรอง — ใช้เมื่อไม่มี OCR ไทย) อ่านรูปสลิป 1 รูป → คืน record รูปแบบเดียวกับที่ parse_payment_report ใช้
    keys: recv_name, amount, pay_date, detail, ref, recv_acct, _img_path, _source
    detail จะมีแท็ก 'ai#EXP...' เพื่อให้ตัวจับคู่เดิม (กฎเลขอ้างอิงตรง) จับได้เลย"""
    text = ocr_image_text(path)
    exp = _extract_exp(text)
    amount = _extract_amount(text)
    pay_date = _extract_date(text)
    txn_ref, ref_date = _extract_txn_ref(text)
    if not pay_date:
        pay_date = ref_date
    accts = _find_masked_accts(text)
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
        # *** ใช้เฉพาะเลข EXP เป็นกุญแจจับคู่ *** — ไม่ใส่ข้อความ OCR ทั้งก้อน
        # (ยอด/transfer/เลขธุรกรรม ฯลฯ จะไปกวนตัวจับคู่ ทำให้จับผิด/ไม่เจอ)
        "detail": (f"ai#{exp}" if exp else ""),
        "ref": "",
        "txn_ref": txn_ref,
        "recv_acct": accts[-1] if len(accts) >= 2 else "",
        "_img_path": path,
        "_ocr_exp": exp,
        "_ocr_text": text,      # เก็บข้อความเต็มไว้ดู/ดีบั๊ก (ไม่ใช้จับคู่)
        "_source": "image",
        "_name_unsure": True,   # OCR อังกฤษอ่านชื่อไทยไม่ได้ → ให้ผู้ใช้ตรวจ/เลือกเอง
    }


# ═════════════════ อ่านสลิป KBIZ ภาษาไทย (โอนเงินสำเร็จ) ด้วย OCR ไทย (RapidOCR) ═════════════════
# Windows OCR บนเครื่องส่วนใหญ่มีแค่ภาษาอังกฤษ → ชื่อ/เดือนภาษาไทยอ่านไม่ออก
# จึงแนบ OCR ไทย (ONNX, ทำงานออฟไลน์) ไว้ในโปรแกรม — อ่านชื่อผู้รับ/ยอด/วันที่/เลขรายการ
# fail-safe: ถ้า OCR ไทยใช้ไม่ได้ → ถอยกลับไปใช้ Windows OCR (อ่านได้แค่ยอด+วันที่จากเลขรายการ)

import sys

_THAI_MONTHS = {"มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6,
                "กค": 7, "สค": 8, "กย": 9, "ตค": 10, "พย": 11, "ธค": 12}
_TH_DATE_RE = re.compile(r"(\d{1,2})\s*([ก-๙]{1,3}\.?\s?[ก-๙]{1,3}\.?)\s*(\d{2,4})")
_TXN_REF_RE = re.compile(r"([A-Z]{3,5})(\d{2})(\d{2})(\d{2})(\d{6,14})")
_MASKED_ACCT_RE = re.compile(r"[xX×]{2,3}\W{0,3}[xX×]\W{0,3}[xX×]?(\d{4})\W{0,3}[xX×]")
_AMT_BAHT_RE = re.compile(r"([0-9][0-9,]*\.[0-9]{2})\s*บาท")
_NAME_PREFIX_RE = re.compile(r"(น\.ส\.|นางสาว|นาย|นาง|บจก\.|หจก\.|บมจ\.|บริษัท|ร้าน|ห้างหุ้นส่วน)")


def _models_dir() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ocr_models")


_TH_ENGINE = None
_TH_ENGINE_ERR = None


def _get_thai_engine():
    """สร้าง OCR ไทย (ครั้งแรกใช้เวลา ~1-2 วินาที แล้วเก็บไว้ใช้ซ้ำ) — คืน None ถ้าใช้ไม่ได้"""
    global _TH_ENGINE, _TH_ENGINE_ERR
    if _TH_ENGINE is not None:
        return _TH_ENGINE
    if _TH_ENGINE_ERR is not None:
        return None
    try:
        from rapidocr import RapidOCR, OCRVersion, LangRec, ModelType
        md = _models_dir()
        det = os.path.join(md, "PP-OCRv6_det_small.onnx")
        rec = os.path.join(md, "th_PP-OCRv5_rec_mobile.onnx")
        if not (os.path.isfile(det) and os.path.isfile(rec)):
            raise FileNotFoundError("ไม่พบไฟล์โมเดล OCR ไทย (ocr_models)")
        _TH_ENGINE = RapidOCR(params={
            "Global.use_cls": False,
            "Global.log_level": "error",
            "Global.model_root_dir": md,
            "Det.model_path": det,
            "Rec.model_path": rec,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": LangRec.TH,
            "Rec.model_type": ModelType.MOBILE,
        })
    except Exception as exc:                       # noqa: BLE001
        _TH_ENGINE_ERR = str(exc)
        _TH_ENGINE = None
    return _TH_ENGINE


def thai_ocr_available() -> bool:
    return _get_thai_engine() is not None


def _run_lines(engine, img) -> list:
    """OCR → list ของบรรทัด {t, s, x0, y0, x1, y1} (เรียงบน→ล่าง)"""
    r = engine(img)
    out = []
    if r is None or r.txts is None:
        return out
    for box, txt, sc in zip(r.boxes, r.txts, r.scores):
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        out.append({"t": str(txt).strip(), "s": float(sc),
                    "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)})
    out.sort(key=lambda d: (round(d["y0"] / 12), d["x0"]))
    return out


def _thai_date(text: str):
    """'12 พ.ย. 69' / '2 พ.ย.69' → 'YYYY-MM-DD' | ''   (ปี พ.ศ. 2 หลัก/4 หลัก → ค.ศ.)"""
    for m in _TH_DATE_RE.finditer(text or ""):
        mon_key = re.sub(r"[\s\.]", "", m.group(2))
        mo = _THAI_MONTHS.get(mon_key)
        if not mo:
            continue
        try:
            dd = int(m.group(1))
            y = int(m.group(3))
        except ValueError:
            continue
        if y < 100:
            y += 2500
        if y >= 2400:
            y -= 543
        if 1 <= dd <= 31 and 2000 <= y <= 2100:
            return f"{y:04d}-{mo:02d}-{dd:02d}"
    return ""


def _extract_txn_ref(text: str):
    """เลขที่รายการ KBIZ เช่น TRBS261112142275939 → (ref, 'YYYY-MM-DD')
    หลังตัวอักษร 4 ตัวคือวันที่ทำรายการ YYMMDD (ค.ศ.) — ใช้เป็นวันที่สำรองที่แม่นมาก"""
    t = re.sub(r"\s+", "", text or "")
    for m in _TXN_REF_RE.finditer(t):
        yy, mm, dd = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 1 <= mm <= 12 and 1 <= dd <= 31 and 20 <= yy <= 60:
            return m.group(0), f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"
    return "", ""


def _find_masked_accts(text: str) -> list:
    return [f"xxx-x-x{m.group(1)}-x" for m in _MASKED_ACCT_RE.finditer(text or "")]


def _join_row(segs: list) -> str:
    """ต่อชิ้นข้อความในแถวเดียวกัน (ซ้าย→ขวา) + เก็บกวาดจุดที่ OCR ทำเกิน (เช่น 'น.ส. . ชัช')"""
    s = " ".join(d["t"] for d in sorted(segs, key=lambda d: d["x0"]))
    s = re.sub(r"(น\.ส\.|บจก\.|หจก\.|บมจ\.)\s*\.\s*", r"\1 ", s)
    s = re.sub(r"^\s*\.\s*", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _name_from_lines(lines: list):
    """ประกอบชื่อจากบรรทัด OCR ในช่วงที่กำหนด → (ชื่อ, ความมั่นใจเฉลี่ย 0-1)"""
    cand = [d for d in lines
            if d["t"] and "ธนาคาร" not in d["t"] and "ไปยัง" not in d["t"]
            and not _MASKED_ACCT_RE.search(d["t"]) and "บาท" not in d["t"]
            and re.search(r"[ก-๙A-Za-z]", d["t"])]
    if not cand:
        return "", 0.0
    # จัดเป็นแถว (y ใกล้กัน = แถวเดียวกัน) แล้วเลือกแถวที่ 'น่าเป็นชื่อ' ที่สุด
    cand.sort(key=lambda d: d["y0"])
    rows, cur = [], [cand[0]]
    for d in cand[1:]:
        if abs(d["y0"] - cur[-1]["y0"]) <= 18:
            cur.append(d)
        else:
            rows.append(cur)
            cur = [d]
    rows.append(cur)
    best, best_key = ("", 0.0), -1.0
    for row in rows:
        txt = _join_row(row)
        tot = sum(len(d["t"]) for d in row) or 1
        conf = sum(d["s"] * len(d["t"]) for d in row) / tot
        key = conf * min(len(txt), 30) + (3 if _NAME_PREFIX_RE.search(txt) else 0)
        if key > best_key:
            best, best_key = (txt, conf), key
    return best


def _recipient_name(img, lines: list, engine):
    """หาชื่อผู้รับ: ใช้ตำแหน่ง 'ไปยัง' → ชื่อ → ธนาคาร → เลขบัญชี (เลย์เอาต์ KBIZ คงที่)
    แล้วลอง OCR ซ้ำเฉพาะแถบชื่อ (ขยาย/เพิ่มคอนทราสต์) เลือกผลที่มั่นใจสุด
    คืน (ชื่อ, ความมั่นใจ, ชื่อธนาคาร)"""
    label = next((d for d in lines if "ไปยัง" in d["t"] and d["s"] >= 0.8), None)
    accts = [d for d in lines if _MASKED_ACCT_RE.search(d["t"])]
    accts.sort(key=lambda d: d["y0"])
    recv_acct_line = accts[-1] if len(accts) >= 2 else (
        accts[0] if accts and (label is None or accts[0]["y0"] > label["y0"]) else None)
    bank = None
    if recv_acct_line is not None:
        bank = next((d for d in lines if "ธนาคาร" in d["t"]
                     and 0 < recv_acct_line["y0"] - d["y0"] < 70), None)
    if label is not None:
        y_top = label["y1"]
    elif len(accts) >= 2:
        y_top = accts[0]["y1"] + 40
    else:
        y_top = None
    if y_top is None or recv_acct_line is None:
        return "", 0.0, ""
    y_bot = (bank["y0"] if bank else recv_acct_line["y0"]) + 4

    band_lines = [d for d in lines if y_top - 4 <= d["y0"] <= y_bot]
    best = _name_from_lines(band_lines)
    if best[0] and best[1] >= 0.93:      # มั่นใจมากแล้ว ไม่ต้องลองซ้ำ (เร็วขึ้น)
        return best[0], best[1], (bank["t"] if bank else "")

    # OCR ซ้ำเฉพาะแถบชื่อ — ลองหลายแบบ ได้ผลมั่นใจกว่าก็ใช้
    try:
        from PIL import Image, ImageOps, ImageEnhance
        import numpy as np
        W, H = img.size
        box = (int(max(0, (label["x0"] if label else 200) - 30)),
               int(max(0, y_top - 2)), W - 4, int(min(H, y_bot)))
        if box[3] - box[1] >= 24:
            band = img.crop(box)

            def v_x2(b):
                return b.resize((b.width * 2, b.height * 2), Image.LANCZOS)

            def v_x2c(b):
                g = ImageOps.autocontrast(b.convert("L"), cutoff=2)
                g = ImageEnhance.Contrast(g).enhance(1.6)
                return g.resize((g.width * 2, g.height * 2), Image.LANCZOS).convert("RGB")

            for fn in (lambda b: b, v_x2, v_x2c):
                ls = _run_lines(engine, np.array(fn(band).convert("RGB")))
                n2, c2 = _name_from_lines(ls)
                if n2 and (c2 > best[1] + 0.02 or not best[0]):
                    best = (n2, c2)
    except Exception:
        pass
    return best[0], best[1], (bank["t"] if bank else "")


def _parse_with_thai_ocr(path: str):
    """อ่านสลิป KBIZ ด้วย OCR ไทย → record | None ถ้า OCR ไทยใช้ไม่ได้"""
    engine = _get_thai_engine()
    if engine is None:
        return None
    from PIL import Image
    import numpy as np
    img = Image.open(path).convert("RGB")
    lines = _run_lines(engine, np.array(img))
    if not lines:
        return None
    text = "\n".join(d["t"] for d in lines)

    # ── ยอดโอน: บรรทัดแรก (บน→ล่าง) ที่เป็น 'x,xxx.xx บาท' และ > 0 (ค่าธรรมเนียมอยู่ถัดลงมา)
    amount = 0.0
    for d in lines:
        m = _AMT_BAHT_RE.search(d["t"])
        if m:
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if v > 0:
                amount = v
                break
    if not amount:
        amount = _extract_amount(text)

    # ── วันที่/เวลา: หัวสลิป (มีเวลา) → วันที่เงินเข้าบัญชี → วันที่จากเลขรายการ
    pay_date, pay_time = "", ""
    for d in lines:
        tm = re.search(r"\d{1,2}:\d{2}", d["t"])
        if tm:
            dt = _thai_date(d["t"])
            if dt:
                pay_date, pay_time = dt, tm.group(0)
                break
    credit_date = ""
    for d in lines:
        if "เข้าบัญชี" in d["t"]:
            credit_date = _thai_date(d["t"])
            if credit_date:
                break
    txn_ref, ref_date = _extract_txn_ref(text)
    pay_date = pay_date or credit_date or ref_date

    recv_name, name_conf, recv_bank = _recipient_name(img, lines, engine)
    accts = _find_masked_accts(text)
    exp = _extract_exp(text)
    if not exp and ocr_available() and ("EXP" in text.upper() or "#" in text):
        exp = _extract_exp(ocr_image_text(path))      # แท็ก ai#EXP ที่บัญชีพิมพ์ (ถ้ามี) — อ่านซ้ำด้วย Windows OCR

    return {
        "recv_name": recv_name,
        "amount": amount,
        "pay_date": pay_date,
        "pay_time": pay_time,
        "detail": (f"ai#{exp}" if exp else ""),
        "ref": "",
        "txn_ref": txn_ref,             # เลขที่รายการธนาคาร (ไม่ใส่ใน ref กันไปชนกุญแจจับคู่เลขบิล)
        "recv_acct": accts[-1] if len(accts) >= 2 else "",
        "recv_bank": recv_bank,
        "_img_path": path,
        "_ocr_exp": exp,
        "_ocr_text": text,
        "_source": "image",
        "_name_conf": round(name_conf, 2),
        "_name_unsure": (not recv_name) or name_conf < 0.85,
    }


def parse_slip_image(path: str) -> dict:
    """อ่านรูปสลิป 1 รูป → record รูปแบบเดียวกับที่ parse_payment_report ใช้
    ลำดับ: OCR ไทย (ชื่อ+ยอด+วันที่+เลขรายการ) → ถ้าใช้ไม่ได้/อ่านยอดไม่ได้ ถอยไป Windows OCR"""
    try:
        rec = _parse_with_thai_ocr(path)
        if rec and rec.get("amount"):
            return rec
    except Exception:
        pass
    return _parse_slip_image_legacy(path)
