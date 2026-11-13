"""
Rejected — เก็บรายการที่ "ไม่อนุมัติ" (requirement 25/06/2569 ข้อ 2)
รายการที่อยู่ในนี้: ไม่ถูกนำไปจัดคิว/คิดยอด/Export จนกว่าจะกด "อนุมัติ"
เก็บข้าง config (พกพา): kcash_rejected.json

โครงสร้าง:
{
  "EXP037864": {"at": "2026-06-25T15:00", "user": "...", "vendor": "...",
                "amount": 5000, "reason": ""},
  ...
}
"""
import os
import json
from datetime import datetime
from config import CONFIG_FILE


def _path() -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_rejected.json")


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


def is_rejected(exp_id) -> bool:
    return str(exp_id) in load()


def reject(exp_id, info: dict = None) -> None:
    d = load()
    rec = {"at": datetime.now().isoformat(timespec="seconds")}
    if info:
        rec.update(info)
    d[str(exp_id)] = rec
    _save(d)
    _push(str(exp_id), rec)


def approve(exp_id) -> None:
    """ยกเลิกการไม่อนุมัติ (อนุมัติกลับ)"""
    d = load()
    d.pop(str(exp_id), None)
    _save(d)
    _push(str(exp_id), deleted=True)


def all_ids() -> set:
    return set(load().keys())


def _push(key, value=None, deleted=False):
    """ส่งขึ้น cloud เบื้องหลัง (ซิงก์ทุกเครื่อง) — ไม่มี cloud/ผิดพลาด = ข้ามเงียบ"""
    try:
        import cloud_sync
        cloud_sync.push_async("rejected", key, value, deleted)
    except Exception:
        pass
