"""
Remarks — เก็บหมายเหตุที่ผู้ใช้พิมพ์เองต่อรายการ (ลูกค้าขอเพิ่ม)
เก็บข้าง config (พกพา): kcash_remarks.json

โครงสร้าง:
{
  "EXP037864": "โอนเช้า / รอเอกสารเพิ่ม",
  ...
}
"""
import os
import json
from config import CONFIG_FILE


def _path() -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_remarks.json")


def load() -> dict:
    p = _path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: dict) -> None:
    p = _path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get(doc_id: str) -> str:
    return load().get(str(doc_id), "")


def set(doc_id: str, text: str) -> None:
    d = load()
    text = (text or "").strip()
    if text:
        d[str(doc_id)] = text
    else:
        d.pop(str(doc_id), None)   # ลบทิ้งถ้าเว้นว่าง
    _save(d)
    _push(str(doc_id), text or None, deleted=not text)


def _push(key, value=None, deleted=False):
    """ส่งขึ้น cloud เบื้องหลัง (ซิงก์ทุกเครื่อง) — ไม่มี cloud/ผิดพลาด = ข้ามเงียบ"""
    try:
        import cloud_sync
        cloud_sync.push_async("remarks", key, value, deleted)
    except Exception:
        pass
