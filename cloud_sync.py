"""
Cloud Sync — ซิงก์ข้อมูลที่เดิมเก็บแค่ในเครื่อง ให้ทุกเครื่อง/ทุกผู้ใช้เห็นตรงกัน (Supabase REST)

ซิงก์ 4 อย่าง (เก็บในตารางเดียว kcash_sync แยกด้วยคอลัมน์ ns):
  remarks       หมายเหตุต่อรายการ          key = เลขรายการ   value = ข้อความ
  rejected      รายการไม่อนุมัติ            key = เลขรายการ   value = {at,user,...}
  paid_pending  จ่ายแล้วรออัพเดต           key = เลขรายการ   value = {at,user,...}
  queue_plan    แผนจัดคิว (ก้อนเดียว)       key = "plan"      value = {saved_at,days,dates}

หลักการ
- เขียนในเครื่องก่อนเสมอ (ใช้งานได้แม้ออฟไลน์) แล้วส่งขึ้น cloud เบื้องหลัง (รวบเป็นชุด)
- ส่งไม่สำเร็จ (ออฟไลน์) → จดไว้ใน kcash_sync_pending.json แล้วลองใหม่รอบซิงก์ถัดไป (ของเครื่องนี้ชนะ)
- sync_all(): ส่งของค้าง → ดึงทั้งหมดจาก cloud มาทับไฟล์ในเครื่อง (cloud ชนะ)
  → ของที่มีแต่ในเครื่อง (ครั้งแรก) ส่งขึ้น cloud
- ลบ = tombstone (deleted=true) เพื่อให้การลบไปถึงเครื่องอื่นด้วย
- แผนจัดคิว: เทียบ saved_at ใครใหม่กว่าชนะ + ข้ามแผนที่เก่ากว่า 3 วัน (ตรงกับกติกาเดิม)
- fail-safe: ไม่มี cloud/เน็ตหลุด/ตารางยังไม่สร้าง → เงียบ ใช้ข้อมูลในเครื่องต่อไป
"""
import os
import json
import threading
import time
from datetime import datetime, timezone

import requests

from config import CONFIG_FILE, load_config

TABLE = "kcash_sync"
NAMESPACES = ("remarks", "rejected", "paid_pending", "queue_plan")
PLAN_MAX_AGE_DAYS = 3

_qlock = threading.Lock()         # คิวส่งขึ้น cloud
_plock = threading.Lock()         # ไฟล์ pending
_sync_lock = threading.Lock()     # กันซิงก์ซ้อนกัน
_queue = []                 # [(ns, key, value, deleted)]
_worker = None
_last_sync = 0.0


# ── config / http ──────────────────────────────────────────────

def _cfg():
    try:
        c = load_config()
        url = (c.get("supabase_url") or "").rstrip("/")
        key = c.get("supabase_key") or ""
        return (url, key) if url and key else (None, None)
    except Exception:
        return None, None


def is_configured() -> bool:
    return _cfg()[0] is not None


def _headers(key, prefer=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def _user() -> str:
    try:
        import activity_log
        return activity_log.current_user() or ""
    except Exception:
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post_rows(rows: list, timeout=15) -> bool:
    url, key = _cfg()
    if not url or not rows:
        return False
    try:
        r = requests.post(f"{url}/rest/v1/{TABLE}", params={"on_conflict": "ns,k"},
                          headers=_headers(key, "resolution=merge-duplicates,return=minimal"),
                          json=rows, timeout=timeout)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def _row(ns, key, value, deleted):
    return {"ns": ns, "k": str(key), "v": None if deleted else value, "deleted": bool(deleted),
            "updated_by": _user(), "updated_at": _now_iso()}


# ── pending (ส่งขึ้น cloud ไม่สำเร็จ) ──────────────────────────

def _pending_path():
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_sync_pending.json")


def _load_pending() -> dict:
    try:
        with open(_pending_path(), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_pending(d: dict):
    try:
        if d:
            with open(_pending_path(), "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
        elif os.path.exists(_pending_path()):
            os.remove(_pending_path())
    except Exception:
        pass


def _mark_pending(rows_src: list):
    with _plock:
        d = _load_pending()
        for ns, key, _v, _del in rows_src:
            d.setdefault(ns, {})[str(key)] = 1
        _save_pending(d)


# ── ส่งขึ้น cloud เบื้องหลัง (รวบเป็นชุด) ─────────────────────

def push_async(ns: str, key, value=None, deleted: bool = False):
    """เรียกหลังบันทึกในเครื่องแล้ว — ส่งขึ้น cloud เบื้องหลัง (ไม่บล็อก UI)"""
    global _worker
    if not is_configured():
        return
    with _qlock:
        _queue.append((ns, str(key), value, deleted))
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, daemon=True)
            _worker.start()


def _drain():
    time.sleep(0.4)                     # รอรวบการแก้ที่ติดกัน
    while True:
        with _qlock:
            batch = list(_queue)
            _queue.clear()
        if not batch:
            return
        latest = {}
        for item in batch:              # key ซ้ำ → เอาอันหลังสุด (กัน upsert ซ้ำแถวเดียวในชุดเดียว)
            latest[(item[0], item[1])] = item
        items = list(latest.values())
        ok = _post_rows([_row(ns, k, v, d) for ns, k, v, d in items])
        if not ok:
            _mark_pending(items)
        time.sleep(0.2)


# ── ตัวอ่าน/เขียนไฟล์ในเครื่องของแต่ละอย่าง ───────────────────

def _local_get(ns: str) -> dict:
    if ns == "remarks":
        import remarks
        return remarks.load()
    if ns == "rejected":
        import rejected
        return rejected.load()
    if ns == "paid_pending":
        import paid_pending
        return paid_pending.load()
    if ns == "queue_plan":
        import queue_plan
        p = queue_plan.load_plan()
        return {"plan": p} if p and p.get("days") else {}
    return {}


def _local_save(ns: str, d: dict):
    if ns == "remarks":
        import remarks
        remarks._save(d)
    elif ns == "rejected":
        import rejected
        rejected._save(d)
    elif ns == "paid_pending":
        import paid_pending
        paid_pending._save(d)
    elif ns == "queue_plan":
        import queue_plan
        p = d.get("plan")
        if p:
            queue_plan.save_plan(p.get("days") or [], p.get("saved_at") or "",
                                 p.get("dates"), push=False)
        else:
            queue_plan.clear_plan(push=False)


# ── ดึงจาก cloud ───────────────────────────────────────────────

def _pull_all():
    """คืน {ns: {k: (v, deleted, updated_at)}} หรือ None ถ้าดึงไม่ได้"""
    url, key = _cfg()
    if not url:
        return None
    out = {ns: {} for ns in NAMESPACES}
    offset = 0
    try:
        while True:
            r = requests.get(f"{url}/rest/v1/{TABLE}", headers=_headers(key),
                             params={"select": "ns,k,v,deleted,updated_at", "order": "ns,k",
                                     "limit": "1000", "offset": str(offset)}, timeout=20)
            if r.status_code != 200:
                return None
            rows = r.json()
            for row in rows:
                if row.get("ns") in out:
                    out[row["ns"]][row["k"]] = (row.get("v"), bool(row.get("deleted")),
                                                row.get("updated_at") or "")
            if len(rows) < 1000:
                return out
            offset += 1000
    except Exception:
        return None


def _plan_age_days(saved_at: str):
    try:
        return (datetime.now() - datetime.fromisoformat(str(saved_at)[:19])).days
    except Exception:
        return 0


def _utc_to_local_naive(iso: str):
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None)
    except Exception:
        return None


def sync_all() -> bool:
    """ซิงก์ครบทุกอย่าง (เรียกจากเบื้องหลัง) — คืน True ถ้า 'ข้อมูลในเครื่องเปลี่ยน' จาก cloud"""
    global _last_sync
    if not is_configured():
        return False
    if not _sync_lock.acquire(blocking=False):
        return False
    try:
        # 1) ส่งของค้าง (ของเครื่องนี้ชนะ)
        pend = _load_pending()
        if pend:
            rows, done = [], []
            for ns, keys in pend.items():
                loc = _local_get(ns) if ns in NAMESPACES else {}
                for k in keys:
                    if k in loc:
                        rows.append(_row(ns, k, loc[k], False))
                    else:
                        rows.append(_row(ns, k, None, True))
                    done.append((ns, k))
            if rows and _post_rows(rows):
                _save_pending({})

        # 2) ดึง cloud → รวมเข้าไฟล์ในเครื่อง
        cloud = _pull_all()
        if cloud is None:
            return False
        changed_any = False
        upload = []
        for ns in NAMESPACES:
            loc = _local_get(ns)
            cl = cloud.get(ns, {})
            if ns == "queue_plan":
                changed_any |= _merge_plan(loc, cl, upload)
                continue
            changed = False
            for k, (v, deleted, _ts) in cl.items():
                if deleted:
                    if k in loc:
                        loc.pop(k, None)
                        changed = True
                elif v is not None and loc.get(k) != v:
                    loc[k] = v
                    changed = True
            for k, v in loc.items():               # มีแต่ในเครื่อง (ครั้งแรก/ยังไม่เคยส่ง) → ส่งขึ้น
                if k not in cl:
                    upload.append(_row(ns, k, v, False))
            if changed:
                _local_save(ns, loc)
                changed_any = True
        if upload:
            for i in range(0, len(upload), 500):
                _post_rows(upload[i:i + 500])
        _last_sync = time.time()
        return changed_any
    finally:
        _sync_lock.release()


def _merge_plan(local: dict, cl: dict, upload: list) -> bool:
    lp = local.get("plan")
    cv, deleted, ts = cl.get("plan", (None, False, ""))
    if "plan" not in cl:
        if lp:
            upload.append(_row("queue_plan", "plan", lp, False))
        return False
    if deleted:
        # มีคนล้างแผน: ล้างของเครื่องนี้ถ้าแผนในเครื่องเก่ากว่าเวลาที่ล้าง ไม่งั้นส่งของเครื่องนี้ขึ้น
        if lp:
            t = _utc_to_local_naive(ts)
            try:
                ls = datetime.fromisoformat(str(lp.get("saved_at"))[:19])
            except Exception:
                ls = None
            if t is not None and ls is not None and ls > t:
                upload.append(_row("queue_plan", "plan", lp, False))
                return False
            _local_save("queue_plan", {})
            return True
        return False
    if not cv or not cv.get("days") or _plan_age_days(cv.get("saved_at")) >= PLAN_MAX_AGE_DAYS:
        return False                                    # แผนบน cloud เก่าเกิน 3 วัน = ไม่ใช้
    if not lp or str(cv.get("saved_at"))[:19] > str(lp.get("saved_at"))[:19]:
        if lp != cv:
            _local_save("queue_plan", {"plan": cv})
            return True
        return False
    if str(lp.get("saved_at"))[:19] > str(cv.get("saved_at"))[:19]:
        upload.append(_row("queue_plan", "plan", lp, False))   # ของเครื่องนี้ใหม่กว่า → ส่งขึ้น
    return False


def sync_if_stale(max_age_sec: int = 20) -> bool:
    """ซิงก์ถ้าครั้งก่อนเกิน max_age_sec — ใช้ก่อนเปิดหน้าจัดคิว"""
    if time.time() - _last_sync < max_age_sec:
        return False
    try:
        return sync_all()
    except Exception:
        return False
