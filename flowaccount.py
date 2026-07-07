"""
FlowAccount API client.
Docs: https://openapi.flowaccount.com/
Auth: OAuth2 Client Credentials — POST /oauth/token
"""
import time
import requests
from typing import Optional

BASE_URL = "https://openapi.flowaccount.com/v1"
# v1 อ่าน/สร้าง/ลบ/จ่ายได้ แต่ 'แก้ไขเอกสาร' ไม่มีใน v1 — ต้องใช้ v3 (prod server)
V3_BASE = "https://openapi.flowaccount.com/v3-alpha"
TOKEN_URL = "https://openapi.flowaccount.com/v1/token"

_token_cache: dict = {"access_token": None, "expires_at": 0, "client_id": None}

# ใช้ session ร่วม (reuse connection) → ดึงหลายหน้าพร้อมกันเร็วขึ้นมาก
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


class FlowAccountError(Exception):
    pass


def _get_token(api_key: str, secret_key: str) -> str:
    now = time.time()
    # ใช้ token ที่ cache ไว้ได้ ต่อเมื่อ "เป็น key เดียวกัน" และยังไม่หมดอายุ
    # (กันเคสสลับบริษัทแล้วได้ token ของบริษัทเก่า → ข้อมูลผิดบริษัท)
    if (_token_cache["access_token"]
            and _token_cache.get("client_id") == api_key
            and now < _token_cache["expires_at"] - 60):
        return _token_cache["access_token"]

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
            "scope": "flowaccount-api",
        },
        timeout=15,
    )
    if not resp.ok:
        raise FlowAccountError(f"ไม่สามารถขอ token ได้: {resp.status_code} {resp.text}")

    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"]   = now + data.get("expires_in", 3600)
    _token_cache["client_id"]    = api_key
    return _token_cache["access_token"]


def _headers(api_key: str, secret_key: str) -> dict:
    """Headers for GET requests — no Content-Type to avoid server JSON parse error."""
    token = _get_token(api_key, secret_key)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _headers_json(api_key: str, secret_key: str) -> dict:
    """Headers for POST/PATCH requests with JSON body."""
    token = _get_token(api_key, secret_key)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


_PAGE_SIZE = 200   # FlowAccount รับสูงสุด 200 ต่อหน้า


def _fetch_expense_page(api_key, secret_key, page, status=None, limit=_PAGE_SIZE):
    """ดึง 1 หน้า → คืน (items, total_document)"""
    params = {"currentPage": page, "pageSize": limit}
    if status and status != "all":
        params["status"] = status
    resp = _session.get(
        f"{BASE_URL}/expenses",
        headers=_headers(api_key, secret_key),
        params=params, timeout=25,
    )
    if not resp.ok:
        raise FlowAccountError(f"GET /expenses ล้มเหลว: {resp.status_code} {resp.text[:120]}")
    inner = resp.json().get("data", {})
    if isinstance(inner, dict):
        items = inner.get("list") or inner.get("expenseEntries") or []
        try:
            total = int(inner.get("totalDocument") or 0)
        except (TypeError, ValueError):
            total = len(items)
        return items, total
    return (inner or []), len(inner or [])


def get_expenses(api_key, secret_key, status=None, page=1, limit=_PAGE_SIZE):
    """ดึงค่าใช้จ่าย 1 หน้า (เรียงใหม่→เก่า)"""
    return _fetch_expense_page(api_key, secret_key, page, status, limit)[0]


def get_all_expenses(api_key: str, secret_key: str, status: str = "all",
                     max_records: int = None) -> list[dict]:
    """ดึงค่าใช้จ่าย (ใหม่→เก่า) สูงสุด max_records รายการ — ดึงหลายหน้า "พร้อมกัน" เพื่อความเร็ว
    max_records: None = อ่านจาก config (fetch_limit, default 1000)
    """
    if max_records is None:
        try:
            from config import load_config
            max_records = int(load_config().get("fetch_limit", 1000) or 1000)
        except Exception:
            max_records = 1000
    max_records = max(_PAGE_SIZE, max_records)

    # หน้าแรก (cache token + รู้ยอดรวม)
    first_items, total = _fetch_expense_page(api_key, secret_key, 1, status)
    if not first_items:
        return []
    target = min(total or len(first_items), max_records)
    last_page = (target + _PAGE_SIZE - 1) // _PAGE_SIZE
    if last_page <= 1:
        return first_items[:max_records]

    # หน้า 2..last_page → ดึงพร้อมกัน (parallel)
    from concurrent.futures import ThreadPoolExecutor
    results = {1: first_items}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_expense_page, api_key, secret_key, p, status): p
                for p in range(2, last_page + 1)}
        for f, p in futs.items():
            try:
                results[p] = f.result()[0]
            except Exception:
                results[p] = []
    out = []
    for p in range(1, last_page + 1):
        out.extend(results.get(p, []))
    return out[:max_records]


def get_expenses_until(api_key, secret_key, since_iso, hard_cap=20000):
    """ดึงค่าใช้จ่าย (ใหม่→เก่า) จนถึงวันที่ since_iso (yyyy-mm-dd) — สำหรับจับคู่สลิป
    หยุดเมื่อเจอเอกสารเก่ากว่า since หรือถึง hard_cap"""
    PS = _PAGE_SIZE
    out, page = [], 1
    while len(out) < hard_cap:
        items, _ = _fetch_expense_page(api_key, secret_key, page, None, PS)
        if not items:
            break
        out.extend(items)
        oldest = min((str(e.get("publishedOn") or e.get("createdDate") or "9999"))[:10]
                     for e in items)
        if oldest < since_iso or len(items) < PS:
            break
        page += 1
    return out


def _fetch_all_parallel(page_fn, max_records=None, page_size=_PAGE_SIZE):
    """ดึงหลายหน้าพร้อมกัน — page_fn(page) ต้องคืน (items, total)"""
    if max_records is None:
        try:
            from config import load_config
            max_records = int(load_config().get("fetch_limit", 1000) or 1000)
        except Exception:
            max_records = 1000
    max_records = max(page_size, max_records)
    first, total = page_fn(1)
    if not first:
        return []
    target = min(total or len(first), max_records)
    last_page = (target + page_size - 1) // page_size
    if last_page <= 1:
        return first[:max_records]
    from concurrent.futures import ThreadPoolExecutor
    results = {1: first}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(page_fn, p): p for p in range(2, last_page + 1)}
        for f, p in futs.items():
            try:
                results[p] = f.result()[0]
            except Exception:
                results[p] = []
    out = []
    for p in range(1, last_page + 1):
        out.extend(results.get(p, []))
    return out[:max_records]


def _fetch_purchases_page(api_key, secret_key, page, limit=_PAGE_SIZE):
    resp = _session.get(
        f"{BASE_URL}/purchases",
        headers=_headers(api_key, secret_key),
        params={"currentPage": page, "pageSize": limit}, timeout=25,
    )
    if not resp.ok:
        raise FlowAccountError(f"GET /purchases ล้มเหลว: {resp.status_code}")
    inner = resp.json().get("data", {})
    if isinstance(inner, dict):
        items = inner.get("list") or []
        try:
            total = int(inner.get("totalDocument") or 0)
        except (TypeError, ValueError):
            total = len(items)
        return items, total
    return (inner or []), len(inner or [])


def get_purchases(api_key, secret_key, page=1, limit=_PAGE_SIZE):
    return _fetch_purchases_page(api_key, secret_key, page, limit)[0]


def get_all_purchases(api_key: str, secret_key: str, max_records: int = None) -> list[dict]:
    """ดึงใบรับสินค้า (ใหม่→เก่า) สูงสุด max_records — ดึงหลายหน้าพร้อมกัน"""
    from functools import partial
    return _fetch_all_parallel(partial(_fetch_purchases_page, api_key, secret_key),
                               max_records)


_bank_cache: dict = {}   # api_key -> list ของบัญชีธนาคาร


def get_bank_accounts(api_key: str, secret_key: str) -> list:
    """ดึงรายการบัญชีธนาคารของบริษัท (มี bankAccountId) — cache ต่อ key"""
    if api_key in _bank_cache:
        return _bank_cache[api_key]
    resp = _session.get(f"{BASE_URL}/bank-accounts",
                        headers=_headers(api_key, secret_key), timeout=20)
    accs = []
    if resp.ok:
        accs = resp.json().get("data") or []
    _bank_cache[api_key] = accs
    return accs


def resolve_bank_account_id(api_key: str, secret_key: str, acct_number: str = None):
    """หา bankAccountId ที่ตรงกับเลขบัญชีหักเงิน (จากสลิป) ไม่งั้นใช้บัญชีแรก"""
    accs = get_bank_accounts(api_key, secret_key)
    if not accs:
        return None
    if acct_number:
        digits = "".join(ch for ch in str(acct_number) if ch.isdigit())
        for a in accs:
            num = "".join(ch for ch in str(a.get("bankAccountNumber") or "") if ch.isdigit())
            if digits and (num.endswith(digits) or digits.endswith(num)):
                return a.get("bankAccountId")
    return accs[0].get("bankAccountId")


# วิธีจ่าย (ตัวเลขตาม FlowAccount): 1=เงินสด 3=เช็ค 5=โอนเงิน 7=บัตรเครดิต
_PAYMENT_METHOD_MAP = {"เงินสด": 1, "เช็ค": 3, "โอนเงิน": 5, "เงินโอน": 5,
                       "โอน": 5, "บัตรเครดิต": 7, "transfer": 5, "cash": 1}


def mark_expense_paid(api_key: str, secret_key: str, expense_id: str, payment_info: dict) -> dict:
    """บันทึกการจ่ายเงิน (เปลี่ยนสถานะเป็นจ่ายแล้ว)
    POST /expenses/{id}/payment  body PaymentDocument
    payment_info: { paymentDate(yyyy-MM-dd), amount(float), paymentMethod(int|str) }
    """
    method = payment_info.get("paymentMethod", 5)
    if isinstance(method, str):
        method = _PAYMENT_METHOD_MAP.get(method.strip(), 5)
    method = int(method or 5)
    collected = payment_info.get("amount", payment_info.get("collected", 0))
    body = {
        "documentId": int(expense_id),
        "paymentDate": payment_info.get("paymentDate"),
        "collected": float(collected or 0),
        "paymentMethod": method,
    }
    # โอนเงิน/บัตรเครดิต ต้องมี bankAccountId
    if method in (5, 7):
        bid = payment_info.get("bankAccountId")
        if not bid:
            bid = resolve_bank_account_id(api_key, secret_key,
                                          payment_info.get("payAccount"))
        if bid:
            body["bankAccountId"] = int(bid)
            body["transferBankAccountId"] = int(bid)
        else:
            body["paymentMethod"] = 1   # ไม่มีบัญชี → ใช้เงินสด (กันพลาด)
    resp = requests.post(
        f"{BASE_URL}/expenses/{expense_id}/payment",
        headers=_headers_json(api_key, secret_key),
        json=body, timeout=20,
    )
    if not resp.ok:
        raise FlowAccountError(f"บันทึกจ่ายเงินล้มเหลว: {resp.status_code} {resp.text[:160]}")
    return resp.json()


def get_expense_detail(api_key: str, secret_key: str, expense_id: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/expenses/{expense_id}",
        headers=_headers(api_key, secret_key),  # GET — no Content-Type
        timeout=15,
    )
    if not resp.ok:
        raise FlowAccountError(f"GET /expenses/{expense_id} ล้มเหลว: {resp.status_code}")
    return resp.json().get("data", {})


# field ที่ SimpleDocument (body สำหรับ PUT แก้เอกสาร) ยอมรับ — จาก FlowAccount OpenAPI
# (additionalProperties:false → ส่ง field นอกนี้ไม่ได้ ต้องคัดเฉพาะนี้)
_EXPENSE_DOC_FIELDS = (
    "recordId", "contactCode", "contactName", "contactAddress", "contactTaxId",
    "contactBranch", "contactPerson", "contactEmail", "contactNumber", "contactZipCode",
    "contactGroup", "publishedOn", "creditType", "creditDays", "dueDate", "salesName",
    "projectName", "reference", "isVatInclusive", "useReceiptDeduction", "subTotal",
    "discountPercentage", "discountAmount", "totalAfterDiscount", "isVat", "vatAmount",
    "grandTotal", "documentShowWithholdingTax", "documentWithholdingTaxPercentage",
    "documentWithholdingTaxAmount", "documentDeductionType", "documentDeductionAmount",
    "remarks", "internalNotes", "showSignatureOrStamp", "documentStructureType",
    "externalId", "saleAndPurchaseChannel", "rowIndex", "items", "documentReference",
    "exemptAmount",
)


def update_expense_dates(api_key: str, secret_key: str, expense_id,
                         new_date: str, culture: str = "th") -> dict:
    """แก้ 'วันที่เอกสาร + ครบกำหนด' = new_date และ 'เครดิต(วัน) = 0'
    วิธี: ดึงเอกสารเดิม → เปลี่ยนเฉพาะ 3 ช่องวันที่/เครดิต → ส่งกลับทั้งใบ (คงยอด/รายการเดิม)
    new_date: 'yyyy-MM-dd'.  *** แก้เอกสารเงินจริง — ทดสอบก่อนใช้จริงเสมอ ***
    ถ้า schema ไม่ตรง API จะปฏิเสธ (ไม่เขียนข้อมูลมั่ว) → โยน error ให้ผู้เรียกรายงาน"""
    detail = get_expense_detail(api_key, secret_key, expense_id)
    if not detail:
        raise FlowAccountError("ดึงเอกสารเดิมไม่ได้ (ว่าง)")

    # ส่งเอกสารกลับทั้งใบ (round-trip GET→PUT บน resource เดียวกัน = คงทุกอย่างครบ)
    # แล้วเปลี่ยนเฉพาะ 3 ช่อง: วันที่เอกสาร / ครบกำหนด / เครดิตวัน
    body = dict(detail)
    body["publishedOn"] = new_date        # 'yyyy-MM-dd' (รูปแบบที่ v1 ExpenseDocument รับ)
    body["dueDate"] = new_date            # ครบกำหนด = วันเดียวกับวันที่เอกสาร
    body["creditDays"] = 0                # เครดิต 0 วัน

    url = f"{BASE_URL}/expenses/{expense_id}"   # PUT /expenses/{id} = แก้เอกสาร (path เดียวกับ GET)
    last_err = ""
    # v1 ต้องระบุ expenseStructureType — ลองแบบ Simple ก่อน (ทั่วไป) แล้วค่อย Inline
    for structure in ("UpdateExpenseSimpleDocument", "UpdateExpenseInlineDocument"):
        body["expenseStructureType"] = structure
        try:
            resp = requests.put(url, headers=_headers_json(api_key, secret_key),
                                json=body, timeout=25)
        except requests.RequestException as ex:
            last_err = str(ex)
            continue
        if resp.ok:
            try:
                return resp.json()
            except Exception:
                return {}
        # ถ้า error เรื่อง 'ชนิดเอกสาร' → ลองอีกชนิด, error อื่น → รายงานทันที
        if resp.status_code == 400 and "structuretype" in resp.text.lower():
            last_err = f"{resp.status_code} {resp.text[:200]}"
            continue
        raise FlowAccountError(
            f"แก้วันที่เอกสารล้มเหลว: {resp.status_code} {resp.text[:220]}")
    raise FlowAccountError(f"แก้วันที่เอกสารล้มเหลว (ทั้ง simple/inline): {last_err}")


# path ขอลิงก์แชร์ตามประเภทเอกสาร
_SHARE_PATH = {
    "expense": "expenses",
    "po":      "purchases-orders",
    "gr":      "purchases",
}


def get_share_link(api_key: str, secret_key: str, document_id,
                   culture: str = "th", doctype: str = "expense") -> str:
    """ขอลิงก์แชร์ (เปิดดูเอกสารสาธารณะ) — รองรับ ค่าใช้จ่าย/ใบสั่งซื้อ/ใบรับสินค้า
    POST /{path}/sharedocument  body {documentId, culture}
    → {"status":true,"data":{"link":"https://share.flowaccount.com/.../xxxx"}}
    """
    seg = _SHARE_PATH.get(doctype, "expenses")
    resp = requests.post(
        f"{BASE_URL}/{seg}/sharedocument",
        headers=_headers_json(api_key, secret_key),
        json={"documentId": int(document_id), "culture": culture},
        timeout=20,
    )
    if not resp.ok:
        raise FlowAccountError(f"ขอลิงก์แชร์ล้มเหลว: {resp.status_code} {resp.text[:120]}")
    data = resp.json()
    if not data.get("status"):
        raise FlowAccountError(f"ขอลิงก์แชร์ไม่สำเร็จ: {data.get('message') or data.get('code')}")
    return (data.get("data") or {}).get("link") or ""


# ─────────────────── Purchase Orders ───────────────────

_PO_ENDPOINT = None   # จำ endpoint ที่ใช้ได้ (กันลองซ้ำทุกหน้า)


def _fetch_po_page(api_key, secret_key, page, limit=_PAGE_SIZE):
    """ดึง PO 1 หน้า → (items, total). จำ endpoint ที่ใช้ได้ไว้"""
    global _PO_ENDPOINT
    params = {"currentPage": page, "pageSize": limit}
    endpoints = ([_PO_ENDPOINT] if _PO_ENDPOINT else [
        f"{BASE_URL}/purchases-orders",   # ตามเอกสารทางการ
        f"{BASE_URL}/purchase-orders",
        f"{BASE_URL}/purchaseorders",
        f"{BASE_URL}/purchase-order",
        f"{BASE_URL}/documents/purchase-orders",
    ])
    for ep in endpoints:
        if not ep:
            continue
        try:
            resp = _session.get(ep, headers=_headers(api_key, secret_key),
                                params=params, timeout=25)
            if resp.status_code == 404:
                continue
            if not resp.ok:
                raise FlowAccountError(f"GET {ep} ล้มเหลว: {resp.status_code} {resp.text[:160]}")
            inner = resp.json().get("data", {})
            if isinstance(inner, dict):
                items = (inner.get("list") or inner.get("purchaseOrders")
                         or inner.get("expenseEntries") or [])
                try:
                    total = int(inner.get("totalDocument") or 0)
                except (TypeError, ValueError):
                    total = len(items)
            else:
                items = inner or []; total = len(items)
            _PO_ENDPOINT = ep
            return items, total
        except FlowAccountError:
            raise
        except Exception:
            continue
    raise FlowAccountError(
        "ไม่พบ API Purchase Orders ใน FlowAccount plan นี้")


def get_purchase_orders(api_key, secret_key, page=1, limit=_PAGE_SIZE):
    """ดึงรายการใบสั่งซื้อ 1 หน้า"""
    return _fetch_po_page(api_key, secret_key, page, limit)[0]


def _unused_old_po(api_key, secret_key, page=1, limit=100):
    params = {"currentPage": page, "pageSize": limit}
    endpoints = []
    last_err = None
    for ep in endpoints:
        try:
            resp = requests.get(ep, headers=_headers(api_key, secret_key),
                                params=params, timeout=20)
            if resp.status_code == 404:
                last_err = f"404 ที่ {ep}"
                continue
            if not resp.ok:
                raise FlowAccountError(f"GET {ep} ล้มเหลว: {resp.status_code} {resp.text[:200]}")
            data = resp.json()
            inner = data.get("data", {})
            if isinstance(inner, dict):
                items = (inner.get("list") or inner.get("purchaseOrders")
                         or inner.get("expenseEntries") or [])
            else:
                items = inner or []
            return items
        except FlowAccountError:
            raise
        except Exception:
            continue
    raise FlowAccountError(
        "ไม่พบ API Purchase Orders ใน FlowAccount plan นี้\n"
        "กรุณาตรวจสอบว่า plan ของคุณรองรับ Purchase Orders\n"
        f"(ลองแล้ว: {', '.join(e.split('/')[-1] for e in endpoints)})"
    )


def get_all_purchase_orders(api_key: str, secret_key: str, max_records: int = None) -> list[dict]:
    """ดึงใบสั่งซื้อ (ใหม่→เก่า) สูงสุด max_records — ดึงหลายหน้าพร้อมกัน"""
    from functools import partial
    return _fetch_all_parallel(partial(_fetch_po_page, api_key, secret_key),
                               max_records)


def convert_po_to_expense(api_key: str, secret_key: str, po_id: str) -> dict:
    """
    เปลี่ยนสถานะใบสั่งซื้อ → ใบทำจ่าย
    ลอง endpoint ที่ FlowAccount น่าจะมี
    """
    # ลอง endpoint หลัก
    for endpoint in [
        f"{BASE_URL}/purchase-orders/{po_id}/convert-to-expense",
        f"{BASE_URL}/purchase-orders/{po_id}/approve",
    ]:
        try:
            resp = requests.post(endpoint, headers=_headers_json(api_key, secret_key),
                                 json={}, timeout=15)
            if resp.ok:
                return resp.json()
        except requests.RequestException:
            continue
    raise FlowAccountError(
        f"ไม่สามารถเปลี่ยนสถานะ PO {po_id} ได้ — กรุณาตรวจสอบกับทีมงาน FlowAccount"
    )


# path แนบไฟล์ตามประเภทเอกสาร (จาก FlowAccount OpenAPI — เอกพจน์ 'attachment')
_ATTACH_PATH = {
    "expense": "expenses",
    "po":      "purchases-orders",
    "gr":      "purchases",
}


def upload_attachment(api_key: str, secret_key: str, doctype: str,
                      doc_id: str, file_bytes: bytes, filename: str,
                      mime: str = "application/octet-stream") -> dict:
    """อัปโหลดไฟล์แนบเข้าเอกสาร (ค่าใช้จ่าย/ใบสั่งซื้อ/ใบรับสินค้า)
    POST /{path}/{id}/attachment  (multipart field 'file')
    """
    seg = _ATTACH_PATH.get(doctype, "expenses")
    token = _get_token(api_key, secret_key)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    files = {"file": (filename, file_bytes, mime)}
    resp = requests.post(
        f"{BASE_URL}/{seg}/{doc_id}/attachment",
        headers=headers, files=files, timeout=40,
    )
    if not resp.ok:
        raise FlowAccountError(f"แนบไฟล์ล้มเหลว: {resp.status_code} {resp.text[:160]}")
    return resp.json()


def upload_po_attachment(api_key, secret_key, po_id, file_bytes, filename):
    """(คงไว้เพื่อความเข้ากันได้) แนบไฟล์เข้าใบสั่งซื้อ"""
    return upload_attachment(api_key, secret_key, "po", po_id, file_bytes, filename)


def upload_expense_attachment(api_key, secret_key, expense_id, file_bytes,
                              filename, mime="image/png"):
    """(คงไว้เพื่อความเข้ากันได้) แนบสลิปเข้าค่าใช้จ่าย — ใช้ใน 'ตัดบิล Phase 2'"""
    return upload_attachment(api_key, secret_key, "expense", expense_id,
                             file_bytes, filename, mime)


# ─────────── ลบ EXP + คืนสถานะ PO (ฟีเจอร์ "ไม่อนุมัติ" ตามที่พี่เบิร์ดอธิบาย) ───────────

# สถานะใบสั่งซื้อ (PO): 1=รออนุมัติ, 3=อนุมัติ, 7=ยกเลิก, 9=ดำเนินการแล้ว
PO_STATUS_APPROVED = 3


_DELETE_SEG = {"expense": "expenses", "po": "purchases-orders", "gr": "purchases"}


def delete_document(api_key: str, secret_key: str, doctype: str, doc_id) -> dict:
    """ลบเอกสารออกจาก FlowAccount จริง — DELETE /{seg}/{id}
    doctype: expense=/expenses, po=/purchases-orders, gr=/purchases
    *** ลบจริง กู้คืนยาก — ต้องยืนยันก่อนเรียกเสมอ ***"""
    seg = _DELETE_SEG.get(doctype, "expenses")
    resp = _session.delete(
        f"{BASE_URL}/{seg}/{doc_id}",
        headers=_headers(api_key, secret_key),
        timeout=20,
    )
    if not resp.ok:
        raise FlowAccountError(
            f"ลบเอกสารไม่สำเร็จ: {resp.status_code} {resp.text[:160]}")
    try:
        return resp.json()
    except Exception:
        return {}


def delete_expense(api_key: str, secret_key: str, expense_id) -> dict:
    """(คงไว้เพื่อความเข้ากันได้) ลบ EXP"""
    return delete_document(api_key, secret_key, "expense", expense_id)


def set_purchase_order_status(api_key: str, secret_key: str, po_id, status_id: int) -> dict:
    """เปลี่ยนสถานะใบสั่งซื้อ — POST /purchases-orders/{id}/status-key/{statusId}
    status_id: 3=อนุมัติ (PO_STATUS_APPROVED)"""
    resp = _session.post(
        f"{BASE_URL}/purchases-orders/{po_id}/status-key/{int(status_id)}",
        headers=_headers_json(api_key, secret_key),
        json={},
        timeout=20,
    )
    if not resp.ok:
        raise FlowAccountError(
            f"เปลี่ยนสถานะ PO ไม่สำเร็จ: {resp.status_code} {resp.text[:160]}")
    try:
        return resp.json()
    except Exception:
        return {}


def find_linked_po(exp: dict):
    """หา PO ที่ผูกกับ EXP (จาก referencedByMe ที่ referenceDocumentType=='1')
    คืน (po_document_id, po_serial) หรือ (None, None)"""
    for ref in (exp.get("referencedByMe") or []):
        if str(ref.get("referenceDocumentType")) == "1":
            return ref.get("referenceId"), ref.get("referenceDocumentSerial")
    # เผื่อบางเคสมีแค่ field reference (เลข PO ตรงๆ) — ไม่มี id ก็คืน serial
    rser = exp.get("reference")
    if rser and str(rser).upper().startswith("PO"):
        return None, rser
    return None, None
