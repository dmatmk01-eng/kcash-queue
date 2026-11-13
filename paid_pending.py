"""
Paid-Pending — เก็บรายการที่ "Mark จ่ายแล้ว รออัพเดตใน FlowAccount" (requirement ข้อ 5)
รายการที่อยู่ในนี้: ไม่ถูกนำไปคำนวณยอด + แสดงสถานะ "จ่ายแล้วรออัพเดตใน flowaccount"
เก็บข้าง config (พกพา): kcash_paid_pending.json

โครงสร้าง:
{
  "EXP037864": {"at": "2026-06-17T15:00", "user": "...", "amount": 5000, "vendor": "..."},
  ...
}
"""
import os
import json
from datetime import datetime
from config import CONFIG_FILE


def _path() -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_paid_pending.json")


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


def is_pending(exp_id: str) -> bool:
    return str(exp_id) in load()


def mark(exp_id: str, info: dict = None) -> None:
    d = load()
    rec = {"at": datetime.now().isoformat(timespec="seconds")}
    if info:
        rec.update(info)
    d[str(exp_id)] = rec
    _save(d)
    _push(str(exp_id), rec)


def unmark(exp_id: str) -> None:
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
        cloud_sync.push_async("paid_pending", key, value, deleted)
    except Exception:
        pass
