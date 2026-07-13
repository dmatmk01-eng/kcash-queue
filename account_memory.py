# -*- coding: utf-8 -*-
"""
ระบบเรียนรู้เลขบัญชี (อัตโนมัติ ไม่ต้องสอน)
------------------------------------------------
แนวคิด: สลิป PDF กับเอกสาร FlowAccount ไม่มี "เลขที่ร่วม" ให้จับ
มีแค่ ยอด+ชื่อ+วันที่ — ซึ่งพังกับชื่อต่างด้าว/ชื่อเล่น

วิธีแก้: ใช้ "เลขบัญชีผู้รับ" (recv_acct) ในสลิปเป็นกุญแจ
- เลขบัญชีไม่ซ้ำ + ไม่เกี่ยวภาษา → แม่นที่สุด
- ทุกครั้งที่ระบบจับคู่ได้ "มั่นใจ" (สีเขียว) → แอบจำ recv_acct → contactName ลงไฟล์
- ครั้งต่อไปเจอเลขบัญชีเดิม → จับให้เลยแม้ชื่อจะเป็นอังกฤษ/ชื่อเล่น
- บัญชีไม่ต้องทำอะไรเลย ระบบสอนตัวเองจากคู่ที่มั่นใจ

ไฟล์จำเก็บข้าง .exe (เหมือน config) ชื่อ account_memory.json
โครงสร้าง:
{
  "3602463213": {
     "names": {"นายนฤพนธ์เชิดชิด": 4, "MR.AUNGKOKO": 1},
     "updated": "2026-06-23"
  },
  ...
}
"""
import os
import json
import re
import datetime as _dt

try:
    from config import CONFIG_FILE
    _BASE_DIR = os.path.dirname(CONFIG_FILE)
except Exception:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MEMORY_FILE = os.path.join(_BASE_DIR, "account_memory.json")

# cache ในหน่วยความจำ
_mem = None


def _norm_acct(acct) -> str:
    """เหลือแต่ตัวเลขของเลขบัญชี (กัน -, ช่องว่าง, xxx)"""
    return "".join(ch for ch in str(acct or "") if ch.isdigit())


def _norm_name(name) -> str:
    """ตัดช่องว่าง/สัญลักษณ์/คำนำหน้า ให้เทียบง่าย"""
    s = str(name or "")
    for t in ("นาย", "นาง", "นางสาว", "น.ส.", "บริษัท", "หจก.", "ห้างหุ้นส่วน",
              "MR.", "MRS.", "MISS", "MR", "MS", "จำกัด", "(มหาชน)"):
        s = s.replace(t, "")
    return re.sub(r"[\s\-\.\(\)/,]", "", s).lower()


def load() -> dict:
    global _mem
    if _mem is not None:
        return _mem
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8-sig") as f:
            _mem = json.load(f)
            if not isinstance(_mem, dict):
                _mem = {}
    except Exception:
        _mem = {}
    return _mem


def save() -> None:
    if _mem is None:
        return
    try:
        os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_mem, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MEMORY_FILE)
    except Exception:
        pass


def learn(acct, contact_name) -> bool:
    """จำว่า เลขบัญชีนี้ = ผู้จำหน่ายคนนี้ (เรียกตอนจับคู่ได้มั่นใจ)
    คืน True ถ้ามีการบันทึกใหม่/เพิ่มน้ำหนัก"""
    a = _norm_acct(acct)
    name = str(contact_name or "").strip()
    if len(a) < 6 or not name:
        return False
    m = load()
    entry = m.setdefault(a, {"names": {}, "updated": ""})
    names = entry.setdefault("names", {})
    names[name] = names.get(name, 0) + 1
    entry["updated"] = _dt.date.today().isoformat()
    return True


def lookup(acct) -> str:
    """คืนชื่อผู้จำหน่ายที่เคยจำไว้สำหรับเลขบัญชีนี้ (ชื่อที่เจอบ่อยสุด)
    คืน "" ถ้าไม่เคยจำ"""
    a = _norm_acct(acct)
    if len(a) < 6:
        return ""
    entry = load().get(a)
    if not entry:
        return ""
    names = entry.get("names") or {}
    if not names:
        return ""
    # ชื่อที่ถูกจำบ่อยสุด = น่าเชื่อถือสุด
    return max(names.items(), key=lambda kv: kv[1])[0]


def lookup_all(acct) -> list:
    """คืนชื่อทั้งหมดที่เคยผูกกับเลขบัญชีนี้ (เรียงตามความถี่)"""
    a = _norm_acct(acct)
    if len(a) < 6:
        return []
    entry = load().get(a)
    if not entry:
        return []
    names = entry.get("names") or {}
    return [n for n, _ in sorted(names.items(), key=lambda kv: -kv[1])]


def lookup_account_by_name(name) -> str:
    """reverse: หา 'เลขบัญชี' จากชื่อผู้จำหน่าย (ที่ระบบเคยจำจากสลิป)
    คืน "" ถ้าไม่เจอ — เลือกเลขบัญชีที่ผูกกับชื่อนี้บ่อยสุด"""
    target = _norm_name(name)
    if len(target) < 3:
        return ""
    best_acct, best_weight = "", 0
    for acct, entry in load().items():
        for nm, w in (entry.get("names") or {}).items():
            nn = _norm_name(nm)
            if nn and (nn == target or nn in target or target in nn):
                if w > best_weight:
                    best_weight, best_acct = w, acct
    return best_acct


def stats() -> dict:
    m = load()
    return {"accounts": len(m),
            "names": sum(len(v.get("names") or {}) for v in m.values())}
