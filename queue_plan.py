"""
Queue Plan — เก็บการจัดเรียงคิวจ่าย (รายการไหนอยู่วันไหน) ที่ผู้ใช้ย้าย/บันทึก
เพื่อให้ปิด-เปิดตารางคิวใหม่แล้วการจัดเรียงยังอยู่ (requirement ข้อ 5)
เก็บข้าง config (พกพา): kcash_queue_plan.json

โครงสร้าง:
{
  "saved_at": "2026-06-17T15:30:00",
  "days": [ ["EXP001","EXP002"], ["EXP010"], ... ]   # index = วันที่ 1,2,3...
}
"""
import os
import json
from config import CONFIG_FILE


def _path() -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_queue_plan.json")


def load_plan() -> dict:
    p = _path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def save_plan(days_of_ids: list, saved_at: str, dates: list = None) -> None:
    """dates: list วันที่ (iso yyyy-mm-dd) ต่อวัน — วันที่ผู้ใช้กำหนดเอง (ถ้ามี)"""
    p = _path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = {"saved_at": saved_at, "days": days_of_ids}
        if dates:
            data["dates"] = dates
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def clear_plan() -> None:
    p = _path()
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass
