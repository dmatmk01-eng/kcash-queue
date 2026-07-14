"""
Activity Log — เก็บประวัติว่าใครทำอะไรเมื่อไหร่ (สำหรับ requirement ข้อ 2, 8)
เก็บข้าง config (พกพา): kcash_activity_log.json และ kcash_queue_log.json

โครงสร้าง 1 รายการ:
{
    "time":   "2026-06-17T15:30:45",
    "user":   "It Support M",
    "action": "จัดคิว",
    "detail": "จัดคิว 3 วัน 90 รายการ",
    "company":"Hollywood88"
}
"""
import os
import json
import getpass
from datetime import datetime
from config import CONFIG_FILE

_MAX = 5000   # เก็บไม่เกินกี่รายการ (กันไฟล์โต)


def _path(name: str) -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), name)


_session_user = None   # ชื่อเล่นของผู้ใช้ที่ login อยู่ (ตั้งหลัง login)


def set_session_user(nickname: str) -> None:
    global _session_user
    _session_user = nickname or None


def current_user() -> str:
    if _session_user:
        return _session_user
    try:
        return getpass.getuser() or "user"
    except Exception:
        return "user"


def _load(fname: str) -> list:
    p = _path(fname)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return []


def _save(fname: str, rows: list) -> None:
    p = _path(fname)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rows[-_MAX:], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Activity Log (ใครทำอะไรเมื่อไหร่) ──
def log(action: str, detail: str = "", company: str = "") -> None:
    rows = _load("kcash_activity_log.json")
    rows.append({
        "time":    datetime.now().isoformat(timespec="seconds"),
        "user":    current_user(),
        "action":  action,
        "detail":  detail,
        "company": company,
    })
    _save("kcash_activity_log.json", rows)


def load_activity() -> list:
    return list(reversed(_load("kcash_activity_log.json")))   # ใหม่สุดก่อน


# ── Queue Log (ประวัติการจัดคิว — ข้อ 5/8) ──
def log_queue(detail: str, items: list = None, company: str = "") -> None:
    rows = _load("kcash_queue_log.json")
    rows.append({
        "time":    datetime.now().isoformat(timespec="seconds"),
        "user":    current_user(),
        "detail":  detail,
        "company": company,
        "count":   len(items) if items else 0,
        "items":   items or [],   # [{doc, vendor, amount, day}]
    })
    _save("kcash_queue_log.json", rows)


def load_queue_log() -> list:
    return list(reversed(_load("kcash_queue_log.json")))


def update_queue_log(time_iso: str, detail: str, items: list = None) -> bool:
    """แก้ไขประวัติการจัดคิว 'อันเดิม' (ตาม time_iso) — ไม่สร้างอันใหม่
    ใช้ตอนดับเบิลคลิกเข้าไปแก้ไขประวัติแล้วบันทึก. คืน True ถ้าเจอ+อัปเดต"""
    rows = _load("kcash_queue_log.json")
    for r in rows:
        if r.get("time") == time_iso:
            r["detail"] = detail
            r["count"] = len(items) if items else 0
            r["items"] = items or []
            r["edited_at"] = datetime.now().isoformat(timespec="seconds")
            _save("kcash_queue_log.json", rows)
            return True
    return False


def delete_queue_log(time_iso: str) -> None:
    """ลบรายการประวัติการจัดคิวที่ตรงกับเวลา (ข้อ 9)"""
    rows = [r for r in _load("kcash_queue_log.json") if r.get("time") != time_iso]
    _save("kcash_queue_log.json", rows)


def clear_queue_log() -> None:
    _save("kcash_queue_log.json", [])


def search(rows: list, query: str) -> list:
    """ค้นหาจาก วันที่ / ผู้ใช้ / action / รายละเอียด"""
    q = (query or "").strip().lower()
    if not q:
        return rows
    out = []
    for r in rows:
        blob = " ".join(str(r.get(k, "")) for k in
                        ("time", "user", "action", "detail", "company")).lower()
        if q in blob:
            out.append(r)
    return out


def export_csv(rows: list, path: str, cols: list) -> None:
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c[1] for c in cols])
        for r in rows:
            w.writerow([r.get(c[0], "") for c in cols])
