"""
Bank Slip Parser — อ่าน "รายละเอียดการชำระเงิน" (K-BIZ Payment Detail Report)
ของธนาคารกสิกรไทย แล้วแยกเป็นรายการจ่ายทีละบรรทัด

ไฟล์นี้เป็น digital PDF มี text layer จริง → ใช้ pdfplumber อ่านตรง ๆ
ไม่ต้องใช้ OCR (แม่นยำ 100% ฟรี)

ผลลัพธ์: list ของ dict
{
    "page":        1,
    "seq":         "1",
    "pay_date":    "2026-05-15",     # วันที่มีผล/หักเงิน (ISO)
    "ref":         "2026051514300276",
    "recv_acct":   "3602463213",     # บัญชีผู้รับเงิน
    "recv_name":   "นาย นฤพนธ์ เชิดชิด",
    "bank":        "KBANK",
    "amount":      5000.0,
    "detail":      "เบิกล่วงหน้า",
    "success":     True,             # ดำเนินการสำเร็จ / หักเงินไม่สำเร็จ
}
"""
import re
from datetime import datetime

try:
    import account_memory as _acctmem   # ระบบเรียนรู้เลขบัญชี (อัตโนมัติ)
except Exception:
    _acctmem = None

# ── ช่วงตำแหน่ง x ของแต่ละคอลัมน์ (จากการวัดไฟล์จริง) ──
X_SEQ      = (0,   30)     # ลำดับที่
X_DATE     = (30,  92)     # วันที่มีผล / วันที่ทำรายการ
X_REF      = (140, 210)    # เลขที่อ้างอิงรายการ (2 บรรทัด)
X_DEDUCT   = (315, 375)    # บัญชีหักเงิน (ของบริษัท)
X_RECVACCT = (375, 433)    # บัญชีผู้รับเงิน
X_NAME     = (433, 512)    # ชื่อผู้รับเงิน
X_BANK     = (512, 600)    # ธนาคาร/สาขา
X_AMOUNT   = (595, 670)    # จำนวนเงิน
X_STATUS   = (700, 900)    # สถานะ

_AMOUNT_RE = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")
_ACCT_RE   = re.compile(r"^\d{9,}$")
_DATE_RE   = re.compile(r"^(\d{1,2})-(.+?)-(\d{4})$")

# เดือนไทยแบบย่อ → เลขเดือน
_THAI_MON = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}


def _thai_date_to_iso(s: str) -> str:
    """'15-พ.ค.-2569' → '2026-05-15' (พ.ศ. → ค.ศ.)"""
    m = _DATE_RE.match(s.strip())
    if not m:
        return ""
    day, mon, year = m.groups()
    month = _THAI_MON.get(mon.strip())
    if not month:
        return ""
    try:
        y = int(year) - 543
        return f"{y:04d}-{month:02d}-{int(day):02d}"
    except ValueError:
        return ""


def _in(x, rng) -> bool:
    return rng[0] <= x < rng[1]


def parse_payment_report(pdf_path: str) -> list:
    """อ่านไฟล์ PDF รายงานการชำระเงิน คืน list ของรายการจ่าย"""
    import pdfplumber

    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for pidx, page in enumerate(pdf.pages, 1):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)

            # หา "จุดยึด" = คำที่เป็นจำนวนเงินในคอลัมน์ amount
            anchors = [w for w in words
                       if _in(w["x0"], X_AMOUNT) and _AMOUNT_RE.match(w["text"])]
            anchors.sort(key=lambda w: w["top"])

            for i, a in enumerate(anchors):
                top = a["top"]
                # ขอบล่างของรายการ = ก่อนจุดยึดถัดไป (หรือท้ายหน้า)
                bottom = anchors[i + 1]["top"] - 5 if i + 1 < len(anchors) else top + 40
                band = [w for w in words if top - 6 <= w["top"] < bottom]

                rec = _build_record(band, a, pidx)
                if rec:
                    # เก็บตำแหน่งแนวตั้งของแถว (สำหรับตัดบิลเป็นรูป)
                    rec["y_top"] = float(top - 6)
                    rec["y_bottom"] = float(bottom)
                    records.append(rec)

    return records


def crop_row_png(pdf_path: str, record: dict, scale: float = 2.5) -> bytes:
    """ตัด 'แถว' ของรายการจากหน้า PDF ออกมาเป็นรูป PNG (คืน bytes)
    ใช้ pypdfium2 render หน้า แล้วครอปตามตำแหน่ง y ของแถว
    """
    import io
    import pypdfium2 as pdfium

    page_no = int(record.get("page", 1)) - 1
    y_top = float(record.get("y_top", 0))
    y_bottom = float(record.get("y_bottom", y_top + 40))

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[page_no]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        W, H = pil.size
        y0 = max(0, int((y_top - 4) * scale))
        y1 = min(H, int((y_bottom + 4) * scale))
        if y1 <= y0:
            y1 = min(H, y0 + int(40 * scale))
        crop = pil.crop((0, y0, W, y1))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


def crop_all_rows(pdf_path: str, records: list, scale: float = 2.5) -> list:
    """ตัดทุกแถวเป็น PNG อย่างมีประสิทธิภาพ — render แต่ละหน้าครั้งเดียว
    คืน list ของ bytes (เรียงตาม records) — ตัวที่ตัดไม่ได้เป็น None
    """
    import io
    from collections import defaultdict

    out = [None] * len(records)

    # (ก) รายการที่มาจาก 'ไฟล์รูปภาพ' (มี _img_path) → ใช้รูปนั้นเป็นหลักฐานโดยตรง
    #     (แปลงเป็น PNG ให้เรียบร้อย) — ไม่ต้องตัดจาก PDF
    pdf_idxs = []
    for idx, r in enumerate(records):
        ip = r.get("_img_path")
        if not ip:
            pdf_idxs.append(idx)
            continue
        try:
            from PIL import Image
            im = Image.open(ip)
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            out[idx] = buf.getvalue()
        except Exception:
            try:                       # อ่านด้วย PIL ไม่ได้ → อ่านไฟล์ดิบ (เผื่อเป็น PNG อยู่แล้ว)
                with open(ip, "rb") as _f:
                    out[idx] = _f.read()
            except Exception:
                out[idx] = None

    # (ข) รายการจาก PDF → ตัดทีละแถวจากหน้า PDF (เหมือนเดิม)
    if pdf_idxs and pdf_path:
        import pypdfium2 as pdfium
        by_page = defaultdict(list)
        for idx in pdf_idxs:
            by_page[int(records[idx].get("page", 1)) - 1].append(idx)
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            for pno, idxs in by_page.items():
                try:
                    pil = pdf[pno].render(scale=scale).to_pil()
                except Exception:
                    continue
                W, H = pil.size
                for idx in idxs:
                    r = records[idx]
                    yt = float(r.get("y_top", 0))
                    yb = float(r.get("y_bottom", yt + 40))
                    y0 = max(0, int((yt - 4) * scale))
                    y1 = min(H, int((yb + 4) * scale))
                    if y1 <= y0:
                        y1 = min(H, y0 + int(40 * scale))
                    buf = io.BytesIO()
                    pil.crop((0, y0, W, y1)).save(buf, format="PNG")
                    out[idx] = buf.getvalue()
        finally:
            pdf.close()
    return out


def _collect(band, xrng, ytop=None, ybot=None):
    """รวมคำในช่วง x (และ y ถ้าระบุ) เรียงตามตำแหน่งอ่าน"""
    ws = [w for w in band if _in(w["x0"], xrng)]
    if ytop is not None:
        ws = [w for w in ws if ytop <= w["top"] < ybot]
    ws.sort(key=lambda w: (round(w["top"] / 6), w["x0"]))
    return ws


def _build_record(band, anchor, page):
    top = anchor["top"]

    # จำนวนเงิน
    try:
        amount = float(anchor["text"].replace(",", ""))
    except ValueError:
        return None

    # ชื่อผู้รับเงิน (อาจมีหลายบรรทัด) — เก็บเฉพาะที่อยู่แถวบน ๆ ของรายการ
    name_ws = _collect(band, X_NAME)
    recv_name = " ".join(w["text"] for w in name_ws).strip()

    # บัญชีผู้รับเงิน
    acct_ws = [w for w in band if _in(w["x0"], X_RECVACCT) and _ACCT_RE.match(w["text"])]
    recv_acct = acct_ws[0]["text"] if acct_ws else ""

    # บัญชีหักเงิน (บัญชีบริษัทที่จ่าย) — ใช้จับคู่ bankAccountId ตอน mark จ่าย
    deduct_ws = [w for w in band if _in(w["x0"], X_DEDUCT) and _ACCT_RE.match(w["text"])]
    pay_acct = deduct_ws[0]["text"] if deduct_ws else ""

    # ข้ามบรรทัดสรุปยอด (เช่น 'จำนวนทั้งหมด', 'รายการทั้งหมด')
    if "จำนวนทั้งหมด" in recv_name or "รายการทั้งหมด" in recv_name:
        return None

    # ธนาคาร
    bank = ""
    for w in band:
        if _in(w["x0"], X_BANK) and w["text"].isupper() and len(w["text"]) >= 3:
            bank = w["text"]
            break

    # แถวจริงต้องมีธนาคารผู้รับเสมอ — ถ้าไม่มีคือบรรทัดสรุป (ข้าม)
    if not bank:
        return None

    # วันที่มีผล (เอาตัวบนสุดในคอลัมน์วันที่)
    pay_date = ""
    date_ws = sorted(_collect(band, X_DATE), key=lambda w: w["top"])
    for w in date_ws:
        iso = _thai_date_to_iso(w["text"])
        if iso:
            pay_date = iso
            break

    # เลขที่อ้างอิง (2 บรรทัดต่อกัน)
    ref_ws = sorted([w for w in band if _in(w["x0"], X_REF) and w["text"].isdigit()],
                    key=lambda w: w["top"])
    ref = "".join(w["text"] for w in ref_ws)

    # รายละเอียด — บรรทัดที่ขึ้นต้นด้วย 'รายละเอียดของรายการ'
    detail = _extract_detail(band)

    # สถานะ — มองหา 'Reject' / 'ไม่สำเร็จ'
    status_text = " ".join(w["text"] for w in band if _in(w["x0"], X_STATUS))
    success = ("Reject" not in status_text) and ("ไม่สำเร็จ" not in status_text)

    return {
        "page":      page,
        "pay_date":  pay_date,
        "ref":       ref,
        "recv_acct": recv_acct,
        "pay_acct":  pay_acct,
        "recv_name": recv_name,
        "bank":      bank,
        "amount":    amount,
        "detail":    detail,
        "success":   success,
    }


def _extract_detail(band):
    """ดึงข้อความหลัง 'รายละเอียดของรายการ :'"""
    # หา marker
    marker_top = None
    for w in band:
        if "รายละเอียด" in w["text"]:
            marker_top = w["top"]
            break
    if marker_top is None:
        return ""
    # เก็บคำในบรรทัดเดียวกับ marker ที่อยู่หลังเครื่องหมาย :
    # จำกัด x < 445 เพื่อไม่ให้ชื่อสาขาธนาคาร (คอลัมน์ขวา) ปนเข้ามา
    line = [w for w in band if abs(w["top"] - marker_top) < 6 and 116 < w["x0"] < 445]
    line.sort(key=lambda w: w["x0"])
    txt = " ".join(w["text"] for w in line)
    return txt.replace(" :", "").strip(" :").strip()


# ───────────────────── จับคู่สลิป ↔ ค่าใช้จ่าย ─────────────────────

_TITLES = ["ห้างหุ้นส่วนจำกัด", "บริษัท", "บจก.", "หจก.", "บมจ.",
           "นางสาว", "น.ส.", "นาง", "นาย", "MR.", "MRS.", "MS.", "MISS", "MR", "MS"]


def _strip_titles(s: str) -> str:
    s = s or ""
    for t in _TITLES:
        s = s.replace(t, " ")
    s = s.replace("จำกัด", " ").replace("(มหาชน)", " ").replace("(สำนักงานใหญ่)", " ")
    return s


def _norm_name(s: str) -> str:
    """ตัดคำนำหน้า/ช่องว่าง/อักขระ เพื่อเทียบชื่อ (รวมเป็นสตริงเดียว)"""
    s = _strip_titles(s)
    s = re.sub(r"[\s\-\.\(\)/]", "", s)
    return s.strip().lower()


def _name_tokens(s: str) -> set:
    """แตกชื่อเป็นคำ ๆ (ตัดคำนำหน้า/อักขระ) — ใช้เทียบแบบมีคำตรงกัน"""
    s = _strip_titles(s)
    s = re.sub(r"[\-\.\(\)/]", " ", s)
    return {w.lower() for w in s.split() if len(w) >= 2}


def _name_score(a: str, b: str) -> float:
    """คะแนนความคล้ายชื่อ = มากสุดระหว่าง (เทียบทั้งสตริง) กับ (สัดส่วนคำที่ตรงกัน)"""
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()
    ta, tb = _name_tokens(a), _name_tokens(b)
    overlap = (len(ta & tb) / min(len(ta), len(tb))) if (ta and tb) else 0.0
    return max(ratio, overlap)


def _date_distance(slip, exp) -> int:
    """ระยะห่างวัน (วันจ่ายในสลิป vs วันที่/ครบกำหนดของเอกสาร) — ใช้เป็นตัวช่วยตัดสิน"""
    from datetime import date as _d
    sd = (slip.get("pay_date") or "")[:10]
    ed = (exp.get("publishedOn") or exp.get("dueDate") or exp.get("createdDate") or "")[:10]
    try:
        a = _d.fromisoformat(sd); b = _d.fromisoformat(ed)
        return abs((a - b).days)
    except Exception:
        return 9999


def _exp_amount(e: dict) -> float:
    for k in ("grandTotal", "totalAmount", "total", "amount", "amountDue"):
        v = e.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _exp_name(e: dict) -> str:
    return (e.get("contactName") or e.get("vendorName")
            or e.get("contactCode") or e.get("supplierName") or "")


def _exp_item_amounts(e: dict) -> list:
    """คืนยอดของ 'บรรทัดย่อย' ทุกบรรทัดในเอกสาร (เผื่อเอกสารยอดรวมที่ซอยย่อย
    เช่น 1 ใบมีหลายคน) — จับยอดสลิปกับบรรทัดย่อยได้ ไม่ใช่แค่ยอดรวม"""
    out = []
    for it in (e.get("items") or []):
        for k in ("total", "amount", "netAmount", "totalIncVat"):
            v = it.get(k)
            if v is not None:
                try:
                    out.append(float(v))
                    break
                except (TypeError, ValueError):
                    pass
    return out


# อัตราหัก ณ ที่จ่ายที่พบบ่อย (จ่ายจริง = ยอดเอกสาร − x%)
_WHT_RATES = (0.01, 0.02, 0.03, 0.05)


def _ref_tokens_from_detail(slip) -> list:
    """ดึง 'เลขบิล/เลขอ้างอิง/ทะเบียนรถ' ที่บัญชีพิมพ์ในช่อง 'รายละเอียดของรายการ'
    เช่น 'ใบวางบิลเดือน 5 เลขที่29440' → '29440'
         'ใบวางบิลเดือน 4 เลขที่RS69040402' → 'rs69040402'
         'ถ่ายน้ำมันเครื่อง 3ฒร1374' → '3ฒร1374' (ทะเบียนรถ)
    เพื่อเอาไปค้นในเอกสาร FlowAccount (สัญญาณแม่นสุด เพราะเป็นรหัสเฉพาะ)"""
    text = str(slip.get("detail", "") or "")
    out, seen = [], set()

    def _add(tl):
        if tl and tl not in seen:
            seen.add(tl); out.append(tl)

    # (1) เลขบิล/อ้างอิง: รัน A-Z0-9 ที่มีตัวเลข ยาว >=5 (เช่น 29440, rs69040402)
    for t in re.findall(r"[A-Za-z0-9]+", text):
        tl = t.lower()
        if len(tl) >= 5 and any(c.isdigit() for c in tl):
            _add(tl)
    # (2) ทะเบียนรถ/รหัสปนไทย: เลข-อักษรไทย-เลข เช่น 3ฒร1374, 2ฒร1065, 3ฒเฉ8243
    for t in re.findall(r"\d{1,2}[ก-๙]{1,3}\d{2,5}", text):
        _add(t.lower())
    return out


def _detail_keyphrase_hit(detail, blob, min_len=8):
    """หา 'คำบรรยายเฉพาะ' จากรายละเอียดสลิป ที่ยาวพอ (>=8 ตัว) แล้วไปตรงในเอกสาร
    เช่น 'สายแลนด์', 'ฟิล์มขาวผิวส้ม', 'ดอกสว่าน', '690501ks'
    ใช้ยืนยันคู่กับ 'ยอดตรง' (กันชนคำสั้น/คำโหลที่บังเอิญตรง) — คืน substring ที่ตรง/None"""
    d = re.sub(r"[\s\-\.\(\)/,]", "", str(detail or "")).lower()
    if len(d) < min_len:
        return None
    for i in range(len(d) - min_len + 1):
        seg = d[i:i + min_len]
        if seg in blob:
            return seg
    return None


def _exp_blob(e: dict) -> str:
    """รวมข้อความทั้งหมดของเอกสาร (ชื่อผู้จำหน่าย + รายละเอียดบรรทัด + หมายเหตุ)
    ไว้ค้นชื่อคนที่อยู่ใน line item/remark (ช่าง/แรงงาน ที่ชื่อไม่ตรง contactName)

    *** ไม่รวม projectName โดยตั้งใจ *** — รหัส/ชื่อโปรเจกต์ (เช่น HL-690101NH-BF2
    คุณนุช) ใช้ร่วมกันหลายบิล/หลายผู้ขาย ถ้าเอามาจับคู่จะทำให้จับผิดใบ (false match)
    โปรเจกต์ยังโชว์ในตารางตามปกติ (คนละส่วนกับการจับคู่)"""
    parts = [e.get("contactName", ""), e.get("remarks", ""),
             e.get("internalNotes", ""), e.get("reference", "")]
    for it in (e.get("items") or []):
        parts.append(it.get("description") or it.get("nameLocal")
                     or it.get("nameForeign") or "")
    s = " ".join(str(p) for p in parts if p)
    return re.sub(r"[\s\-\.\(\)/]", "", s).lower()


def _exp_ref_blob(e: dict) -> str:
    """ข้อความเฉพาะ 'เลขอ้างอิง/เลขบิล/PO/หมายเหตุ' ของเอกสาร (กุญแจเฉพาะ)
    — ไม่รวมชื่อโปรเจกต์และรายละเอียดสินค้า เพราะสองอย่างนั้นใช้ร่วมหลายบิล
    ใช้จับคู่แบบ 'มั่นใจ (เขียว)' เฉพาะเมื่อสลิปพิมพ์เลขบิล/PO จริงตรงกัน"""
    parts = [_exp_serial(e), e.get("reference", ""), e.get("externalDocumentId", ""),
             e.get("documentNumber", ""), e.get("referenceNumber", ""),
             e.get("remarks", ""), e.get("internalNotes", "")]
    s = " ".join(str(p) for p in parts if p)
    return "".join(ch for ch in s if ch.isalnum()).lower()


def _exp_serial(e: dict) -> str:
    return (e.get("documentSerial") or e.get("documentNumber")
            or e.get("referenceNumber") or str(e.get("recordId") or ""))


def match_slip_to_expenses(slip_records: list, expenses: list,
                           amount_tol: float = 0.01,
                           name_threshold: float = 0.55,
                           maybe_threshold: float = 0.50) -> list:
    """
    จับคู่แต่ละแถวสลิป กับค่าใช้จ่าย โดยใช้ ยอดเงิน (ต้องตรง) + ชื่อ (คล้าย)
    คืน list ของ:
      {slip, expense, score, status}
      status: 'matched' (ยอดตรง+ชื่อคล้ายมาก) /
              'amount_only' (ยอดตรง+ชื่อคล้ายพอควร — น่าจะใช่) /
              'unmatched' (ไม่เจอ หรือ ยอดตรงแต่ชื่อต่างกันมาก = บังเอิญ)
    """
    results = []
    used = set()   # กัน expense ถูกจับซ้ำ (เฉพาะการจับด้วยยอด)

    # เตรียมล่วงหน้า: blob ข้อความ + blob เฉพาะเลขอ้างอิง + index เลขอ้างอิง (ทำครั้งเดียว)
    blobs = [_exp_blob(e) for e in expenses]
    ref_blobs = [_exp_ref_blob(e) for e in expenses]   # เลขบิล/PO เท่านั้น (กุญแจเฉพาะ)
    by_ref = {}
    for i, e in enumerate(expenses):
        for key in (_exp_serial(e), e.get("reference"), e.get("externalDocumentId")):
            k = "".join(ch for ch in str(key or "") if ch.isalnum()).lower()
            if len(k) >= 5:
                by_ref.setdefault(k, i)

    def _learn_green(slip, exp):
        """จำ เลขบัญชีผู้รับ → ชื่อผู้จำหน่าย จากคู่ที่มั่นใจ (สีเขียว)"""
        if _acctmem is not None:
            try:
                _acctmem.learn(slip.get("recv_acct"), _exp_name(exp))
            except Exception:
                pass

    def _mem_match(slip, amt):
        """จับด้วย 'เลขบัญชีที่เคยจำ' — แม่นสุด ไม่เกี่ยวภาษา/ชื่อเล่น
        คืน index ของ expense ที่ ยอดตรง + ชื่อเอกสารตรงกับชื่อที่เคยผูกกับเลขบัญชีนี้"""
        if _acctmem is None:
            return None
        learned = _acctmem.lookup_all(slip.get("recv_acct"))
        if not learned:
            return None
        learned_norm = [_acctmem._norm_name(n) for n in learned if n]
        learned_norm = [n for n in learned_norm if len(n) >= 3]
        if not learned_norm:
            return None
        # กันบัญชี 'คนกลาง': ถ้าเลขบัญชีนี้เคยผูกกับผู้ขายหลายรายที่ต่างกันจริง
        # → ถือว่ากำกวม ห้ามจับให้เขียวอัตโนมัติ (จะไปเข้าเส้นทางยอด/ชื่อ = ต้องยืนยันแทน)
        if len(set(learned_norm)) > 1:
            return None
        # กันยอดชนกัน: ถ้ามีเอกสารยอดตรง+ชื่อตรงมากกว่า 1 ใบ → กำกวม ไม่จับเขียว
        matches = []
        for i, e in enumerate(expenses):
            if i in used or abs(_exp_amount(e) - amt) > amount_tol:
                continue
            en = _acctmem._norm_name(_exp_name(e))
            if not en:
                continue
            if any(en == ln or en in ln or ln in en for ln in learned_norm):
                matches.append((-_date_distance(slip, e), i))
        if len(matches) != 1:
            return None      # 0 = ไม่เจอ, ≥2 = กำกวม → ไม่เดา
        return matches[0][1]

    for s in slip_records:
        amt = s["amount"]
        s_tokens = _name_tokens(s["recv_name"])

        # 0) มีเลขเอกสาร/อ้างอิงในรายละเอียดสลิป → จับเป๊ะ
        sblob = "".join(ch for ch in (str(s.get("detail", "")) + str(s.get("ref", ""))).lower()
                        if ch.isalnum())
        exact_i = next((idx for k, idx in by_ref.items()
                        if idx not in used and k in sblob), None)
        if exact_i is not None:
            used.add(exact_i)
            _learn_green(s, expenses[exact_i])
            results.append({"slip": s, "expense": expenses[exact_i],
                            "score": 1.0, "status": "matched"})
            continue

        # 0.6) ค้น "เลขบิล/เลขอ้างอิง" ที่พิมพ์ในรายละเอียดสลิป → ในข้อความเอกสารทั้งใบ
        #      (รวมเลขที่อ้างอิง/หมายเหตุ/บรรทัดย่อย) — แม่นมาก เพราะเป็นเลขเฉพาะ
        d_tokens = _ref_tokens_from_detail(s)
        if d_tokens:
            ref_cands = []
            for i, e in enumerate(expenses):
                if i in used:
                    continue
                # ค้นเฉพาะใน 'blob เลขอ้างอิง' (เลขบิล/PO จริง) — ไม่เอา projectName/สินค้า
                # เพื่อไม่ให้รหัสโปรเจกต์ (ที่ใช้ร่วมหลายบิล) ทำให้จับผิดใบ
                hits = [t for t in d_tokens if t in ref_blobs[i]]
                if hits:
                    ref_cands.append((max(len(t) for t in hits),
                                      -_date_distance(s, e), i, e, hits))
            if ref_cands:
                ref_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
                _, _, bi, be, hits = ref_cands[0]
                used.add(bi)
                _learn_green(s, be)
                results.append({"slip": s, "expense": be, "score": 1.0,
                                "status": "matched", "via": "reference",
                                "match_note": f"ตรงเลขอ้างอิงในบิล: {hits[0]}"})
                continue

        # 0.65) ยอดตรง + ข้อความบรรยายในรายละเอียดสลิป ไปตรงในเอกสาร
        #       (เช่น 'สายแลนด์' / 'ฟิล์มขาวผิวส้ม' / '690501ks') → จับเป๊ะ
        #       ใช้ยอดตรงเป็นหลักยึด + คำเฉพาะ >=8 ตัว กันมั่ว
        s_detail = s.get("detail", "")
        if s_detail:
            dk_cands = []
            for i, e in enumerate(expenses):
                if i in used or abs(_exp_amount(e) - amt) > amount_tol:
                    continue
                seg = _detail_keyphrase_hit(s_detail, blobs[i])
                if seg:
                    dk_cands.append((-_date_distance(s, e), i, e, seg))
            if dk_cands:
                dk_cands.sort(reverse=True)
                _, bi, be, seg = dk_cands[0]
                # ถ้าคำบรรยาย+ยอดตรงกับหลายใบ = คำนั้นไม่เฉพาะพอ → ไม่เดา ให้ยืนยันเอง
                if len(dk_cands) >= 2:
                    used.add(bi)
                    results.append({"slip": s, "expense": be, "score": 0.9,
                                    "status": "amount_only", "via": "detail",
                                    "match_note": f"ยอด+ข้อความตรงหลายใบ ({seg}) — ต้องยืนยัน"})
                    continue
                used.add(bi)
                _learn_green(s, be)
                results.append({"slip": s, "expense": be, "score": 0.97,
                                "status": "matched", "via": "detail",
                                "match_note": f"ตรงข้อความในรายละเอียด: {seg}"})
                continue

        # 0.5) เลขบัญชีที่ระบบเคยจำ (อัตโนมัติ) → จับให้เลยแม้ชื่อต่างด้าว/ชื่อเล่น
        mem_i = _mem_match(s, amt)
        if mem_i is not None:
            used.add(mem_i)
            results.append({"slip": s, "expense": expenses[mem_i],
                            "score": 1.0, "status": "matched", "via": "account"})
            continue

        # 1) ยอดตรง + ชื่อคล้าย (เทียบชื่อผู้จำหน่าย และค้นในรายละเอียด)
        cands = []
        for i, e in enumerate(expenses):
            if i in used or abs(_exp_amount(e) - amt) > amount_tol:
                continue
            score = _name_score(s["recv_name"], _exp_name(e))
            if s_tokens:
                hit = sum(1 for t in s_tokens if len(t) >= 2 and t in blobs[i])
                score = max(score, hit / len(s_tokens))
            cands.append((score, -_date_distance(s, e), i, e))
        cands.sort(reverse=True)
        if cands and cands[0][0] >= name_threshold:
            best_score, _, bi, be = cands[0]
            # กันยอดชนกัน: ถ้ามีเอกสาร 'ยอดเท่ากัน' หลายใบ และคะแนนชื่อของอันดับ 2
            # ไล่จี้มา (ต่างกัน < 0.2) = ไม่ชัวร์ว่าใบไหน → ห้ามเดา ให้เป็น 'ต้องยืนยัน'
            second = cands[1][0] if len(cands) > 1 else -1.0
            ambiguous = len(cands) >= 2 and (best_score - second) < 0.2
            if ambiguous:
                used.add(bi)
                results.append({"slip": s, "expense": be,
                                "score": round(best_score, 2), "status": "amount_only",
                                "match_note": f"ยอดเท่ากัน {len(cands)} ใบ — ต้องเลือกใบให้ถูก"})
                continue
            used.add(bi)
            _learn_green(s, be)
            results.append({"slip": s, "expense": be,
                            "score": round(best_score, 2), "status": "matched"})
            continue
        if cands and cands[0][0] >= maybe_threshold:
            best_score, _, bi, be = cands[0]
            used.add(bi)
            note = (f"ยอดเท่ากัน {len(cands)} ใบ — ต้องเลือกใบให้ถูก"
                    if len(cands) >= 2 else "ยอดตรงแต่ชื่อไม่ชัด — ต้องยืนยัน")
            results.append({"slip": s, "expense": be,
                            "score": round(best_score, 2), "status": "amount_only",
                            "match_note": note})
            continue

        # 1.5) ยอดรวมไม่ตรง แต่ "ชื่อตรง" + ยอดตรงกับ (ก)บรรทัดย่อยในเอกสาร
        #      หรือ (ข)ยอดหลังหัก ณ ที่จ่าย → ถือว่าเจอคู่ (เขียว) เพราะมี 2 สัญญาณ
        #      ไม่ใส่ used เพราะเอกสารยอดรวม 1 ใบ อาจคู่กับหลายสลิป (ซอยย่อย)
        ms_cands = []
        for i, e in enumerate(expenses):
            nm = _name_score(s["recv_name"], _exp_name(e))
            if s_tokens:
                hit = sum(1 for t in s_tokens if len(t) >= 2 and t in blobs[i])
                nm = max(nm, hit / len(s_tokens))
            if nm < name_threshold:          # ชื่อต้องตรงพอควร กันมั่ว
                continue
            note = ""
            # (ก) ตรงกับยอดบรรทัดย่อยแบบเป๊ะ (เอกสารยอดรวมซอยย่อยหลายคน)
            if any(abs(a - amt) <= max(amount_tol, 0.5)
                   for a in _exp_item_amounts(e)):
                note = "ตรงรายการย่อยในเอกสาร"
            else:
                # (ข) ตรงกับยอดหลังหัก ณ ที่จ่าย แบบเป๊ะ (เผื่อปัดเศษ ≤0.6 บาท
                #     เท่านั้น — กันจับยอดที่บังเอิญใกล้กันแต่ไม่ใช่หัก ณ ที่จ่ายจริง)
                tot = _exp_amount(e)
                for p in _WHT_RATES:
                    if tot > 0 and abs(tot * (1 - p) - amt) <= 0.6:
                        note = f"ยอดหลังหัก ณ ที่จ่าย {int(p*100)}%"
                        break
            if note:
                ms_cands.append((nm, -_date_distance(s, e), i, e, note))
        if ms_cands:
            ms_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
            nm, _, bi, be, note = ms_cands[0]
            results.append({"slip": s, "expense": be, "score": round(max(nm, 0.9), 2),
                            "status": "matched", "via": "amount_detail",
                            "match_note": note})
            continue

        # 2) ยอดไม่ตรง (เช่นเอกสารยอดรวม/ประกันสังคม) → ค้น "ชื่อครบทุกคำ"
        #    ในรายละเอียดเอกสารทุกใบ (ไม่สนยอด) → เสนอเป็น "น่าจะใช่"
        #    เงื่อนไขกันมั่ว: ต้องมีคำที่ "เฉพาะเจาะจง" (ยาว >=4) อย่างน้อย 1 คำ
        #    และรวมความยาวชื่อ >=5 (กันชื่อเล่นสั้น/คำโหลเช่น 'กรุ๊ป')
        long_tokens = [t for t in s_tokens if len(t) >= 4]
        if long_tokens and len("".join(s_tokens)) >= 5:
            best = None; best_key = (-1, 1)
            for i, e in enumerate(expenses):
                # ต้องเจอ "คำเฉพาะ" ครบ + ชื่อครบทุกคำ
                if not all(t in blobs[i] for t in long_tokens):
                    continue
                hit = sum(1 for t in s_tokens if len(t) >= 2 and t in blobs[i])
                if hit == len(s_tokens):
                    key = (hit, -_date_distance(s, e))
                    if key > best_key:
                        best_key = key; best = e
            if best is not None:
                results.append({"slip": s, "expense": best,
                                "score": 0.9, "status": "amount_only"})
                continue

        results.append({"slip": s, "expense": None, "score": None,
                        "status": "unmatched"})

    # ── รอบสอง: ใช้เลขบัญชีที่ "เพิ่งเรียนรู้" จากคู่สีเขียวในรอบนี้
    #    มาช่วยแถวที่ยังไม่เจอ/น่าจะใช่ ในไฟล์เดียวกัน (ทันทีไม่ต้องรอรอบหน้า)
    if _acctmem is not None:
        for r in results:
            if r["status"] == "matched":
                continue
            mem_i = _mem_match(r["slip"], r["slip"]["amount"])
            if mem_i is not None:
                used.add(mem_i)
                r["expense"] = expenses[mem_i]
                r["score"] = 1.0
                r["status"] = "matched"
                r["via"] = "account"
        try:
            _acctmem.save()
        except Exception:
            pass
    return results


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else "samples/Payment_Detail_Report_160526161759.pdf"
    recs = parse_payment_report(path)
    total = sum(r["amount"] for r in recs)
    print(f"พบ {len(recs)} รายการ • รวม {total:,.2f} บาท")
    with open("samples/_parsed.json", "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=2)
    print("เขียนผลไป samples/_parsed.json")
