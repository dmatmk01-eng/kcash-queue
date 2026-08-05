import os
import sys
import json
import shutil

DEFAULTS = {
    "flowaccount_api_key": "",
    "flowaccount_secret_key": "",
    "company_name": "บริษัทของฉัน",
    "bank_account": "",
    "line_token": "",
    "github_token": "",
    "weekly_off_days": [6],     # วันหยุดประจำสัปดาห์ (0=จ...6=อา) default หยุดอาทิตย์
    "holidays": [],             # วันหยุดพิเศษ/นักขัตฤกษ์ ["YYYY-MM-DD", ...]
    "fetch_limit": 1000,        # จำนวน expense ล่าสุดที่ดึง (ใหม่→เก่า) ดึงแบบ parallel
    "currency": "THB",
    "days_ahead": 30,
    "monday_api_token": "",
    "monday_board_id": "",
    "monday_sync_hour": 15,
    "monday_sync_minute": 0,
    "monday_auto_sync": False,
    "google_sheet_id": "",
    "audit_sheet_id": "",
    "supabase_url": "https://ylwwpeldaoqlwxvdbxwt.supabase.co",
    "supabase_key": "sb_publishable_Vn7DmfEmA9jX9vE1qALKKA_jKenWiXP",
    "web_password": "kcash1234",
    # mapping รหัสลูกค้า FlowAccount → ชื่อแบรนด์ (เลิกใช้แล้ว — เก็บไว้เพื่อความเข้ากันได้)
    "brand_map": {},
    # รายชื่อแบรนด์ที่เลือกได้ใน dropdown
    "brand_list": ["Hollywood", "D-MAT CNC", "DEKO", "Yellow kitchen", "DDCUT"],
    # วงเงินจ่ายต่อวัน (default) — 0 = ไม่จำกัด
    "daily_payment_limit": 150000.0,
    # ── หลายบริษัท (multi-company) ──
    # แต่ละตัว: {label, support_code, client_id, client_secret, color}
    "companies": [],
    "active_company": 0,
    # โหมดดึงทุกบริษัทรวมกันในหน้าคิวจ่ายเงิน (แบรนด์ขึ้นอัตโนมัติ)
    "queue_all_companies": False,
}

# ── หา config — เก็บข้าง .exe/ไฟล์โค้ด (portable, แต่ละชุดแยกกัน) ─────
# *** สำคัญ: เก็บข้าง exe ของแต่ละชุด ไม่ใช้ AppData กลาง ***
# เพราะถ้าใช้ AppData กลาง แอปหลายชุด (เช่นมีอีกชุดบน G:) จะเขียนทับ
# config กันจนบริษัทหาย — เก็บแยกข้าง exe = แต่ละชุดมี config ของตัวเอง
def _find_config_file() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)          # โฟลเดอร์เดียวกับ .exe
    else:
        base = os.path.dirname(os.path.abspath(__file__))  # โฟลเดอร์โค้ด (dev)
    local = os.path.join(base, "kcash_config.json")

    # ถ้าโฟลเดอร์ exe เขียนไม่ได้ (เช่นอยู่ Program Files) → fallback ไป AppData
    try:
        if not os.path.exists(local):
            with open(local, "a", encoding="utf-8"):
                pass
            if os.path.getsize(local) == 0:
                os.remove(local)
        return local
    except Exception:
        appdata = os.environ.get("APPDATA") or os.environ.get("USERPROFILE") or base
        d = os.path.join(appdata, "KCash")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "kcash_config.json")


CONFIG_FILE = _find_config_file()

try:
    import datetime as _dt
    _d = os.path.dirname(CONFIG_FILE)
    os.makedirs(_d, exist_ok=True)
    with open(os.path.join(_d, "kcash_debug.log"), "a", encoding="utf-8") as _f:
        _f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')}  "
                 f"CONFIG_FILE chosen = {CONFIG_FILE} | frozen={getattr(sys,'frozen',False)} | "
                 f"exe={getattr(sys,'executable','')}\n")
except Exception:
    pass


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        # utf-8-sig = ทน BOM (เผื่อไฟล์ถูกเขียนด้วยโปรแกรมที่ใส่ BOM)
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        cfg = {**DEFAULTS, **data}
    else:
        cfg = dict(DEFAULTS)
    raw_companies = len(cfg.get("companies") or [])
    _migrate_companies(cfg)
    apply_active_company(cfg)
    _debug_log(f"load_config: file={CONFIG_FILE} | companies_in_file={raw_companies} "
               f"| after_migrate={len(cfg.get('companies') or [])} | active={cfg.get('active_company')}")
    return cfg


def _debug_log(msg: str) -> None:
    try:
        import datetime
        d = os.path.dirname(CONFIG_FILE)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "kcash_debug.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── multi-company helpers ────────────────────────────────────────
def _migrate_companies(cfg: dict) -> None:
    """ถ้ายังไม่มีลิสต์บริษัท แต่มี key เดิม → สร้างบริษัทแรกจาก key เดิม"""
    if not cfg.get("companies"):
        if cfg.get("flowaccount_api_key"):
            cfg["companies"] = [{
                "label":         cfg.get("company_name") or "บริษัทหลัก",
                "support_code":  "",
                "client_id":     cfg.get("flowaccount_api_key", ""),
                "client_secret": cfg.get("flowaccount_secret_key", ""),
                "color":         "#eab308",
            }]
        else:
            cfg["companies"] = []
    if "active_company" not in cfg:
        cfg["active_company"] = 0


def apply_active_company(cfg: dict) -> None:
    """ก็อป client_id/secret ของบริษัทที่เลือกอยู่ → ช่อง flowaccount_api_key/secret
    เพื่อให้โค้ดเดิมที่อ่าน 2 ช่องนี้ทำงานต่อได้โดยไม่ต้องแก้"""
    comps = cfg.get("companies") or []
    idx = cfg.get("active_company", 0)
    if not (0 <= idx < len(comps)):
        idx = 0
        cfg["active_company"] = 0
    if comps:
        c = comps[idx]
        cfg["flowaccount_api_key"]    = c.get("client_id", "")
        cfg["flowaccount_secret_key"] = c.get("client_secret", "")


def set_active_company(idx: int) -> dict:
    """สลับบริษัทที่ใช้งาน แล้วบันทึก คืน config ใหม่"""
    cfg = load_config()
    comps = cfg.get("companies") or []
    if 0 <= idx < len(comps):
        cfg["active_company"] = idx
        apply_active_company(cfg)
        save_config(cfg)
    return cfg


def active_company_info() -> dict:
    """คืนข้อมูลบริษัทที่เลือกอยู่ (หรือ {} ถ้าไม่มี)"""
    cfg = load_config()
    comps = cfg.get("companies") or []
    idx = cfg.get("active_company", 0)
    if 0 <= idx < len(comps):
        return comps[idx]
    return {}
