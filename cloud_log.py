"""
Cloud Audit Log — Supabase (PostgreSQL REST API)
เก็บประวัติการใช้งานจากทุกเครื่องขึ้น cloud
ใช้แค่ requests (มีอยู่แล้ว) — ไม่ต้องลง SDK เพิ่ม

Tables:
  - activity_logs : ใครทำอะไรเมื่อไหร่
  - queue_logs    : ประวัติการจัดคิวโดยละเอียด
"""
import json
import platform
import threading
from datetime import datetime
from typing import Optional

import requests

_machine = platform.node() or "unknown"


def _headers(api_key: str) -> dict:
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _headers_select(api_key: str) -> dict:
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
    }


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("supabase_url") and cfg.get("supabase_key"))


# ── INSERT (append-only) ─────────────────────────────────────────

def append_activity(cfg: dict, user: str, action: str,
                    detail: str = "", company: str = "") -> bool:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return False
    try:
        r = requests.post(
            f"{url}/rest/v1/activity_logs",
            headers=_headers(key),
            json={
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "username": user,
                "action": action,
                "detail": detail,
                "company": company,
                "machine": _machine,
            },
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def append_queue(cfg: dict, user: str, detail: str,
                 items: list = None, company: str = "") -> bool:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return False
    try:
        r = requests.post(
            f"{url}/rest/v1/queue_logs",
            headers=_headers(key),
            json={
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "username": user,
                "detail": detail,
                "company": company,
                "item_count": len(items) if items else 0,
                "items": json.dumps(items or [], ensure_ascii=False),
                "machine": _machine,
            },
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


# ── Async wrappers (ไม่ block UI) ────────────────────────────────

def append_activity_async(cfg: dict, user: str, action: str,
                          detail: str = "", company: str = ""):
    if not is_configured(cfg):
        return
    t = threading.Thread(target=append_activity,
                         args=(cfg, user, action, detail, company),
                         daemon=True)
    t.start()


def append_queue_async(cfg: dict, user: str, detail: str,
                       items: list = None, company: str = ""):
    if not is_configured(cfg):
        return
    t = threading.Thread(target=append_queue,
                         args=(cfg, user, detail, items, company),
                         daemon=True)
    t.start()


# ── SELECT (read logs) ───────────────────────────────────────────

def fetch_activity_logs(cfg: dict, user: str = "",
                        date_from: str = "", date_to: str = "",
                        limit: int = 500) -> list:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return []
    try:
        params = {
            "select": "*",
            "order": "timestamp.desc",
            "limit": str(limit),
        }
        if user:
            params["username"] = f"eq.{user}"
        if date_from:
            params["timestamp"] = f"gte.{date_from} 00:00:00"
        if date_to:
            if "timestamp" in params:
                params["timestamp"] = f"gte.{date_from} 00:00:00"
                params["and"] = f"(timestamp.lte.{date_to} 23:59:59)"
            else:
                params["timestamp"] = f"lte.{date_to} 23:59:59"
        r = requests.get(
            f"{url}/rest/v1/activity_logs",
            headers=_headers_select(key),
            params=params,
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        return []
    except Exception:
        return []


def fetch_queue_logs(cfg: dict, user: str = "",
                     date_from: str = "", date_to: str = "",
                     limit: int = 500) -> list:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return []
    try:
        params = {
            "select": "*",
            "order": "timestamp.desc",
            "limit": str(limit),
        }
        if user:
            params["username"] = f"eq.{user}"
        if date_from:
            params["timestamp"] = f"gte.{date_from} 00:00:00"
        if date_to:
            if "timestamp" in params:
                params["timestamp"] = f"gte.{date_from} 00:00:00"
                params["and"] = f"(timestamp.lte.{date_to} 23:59:59)"
            else:
                params["timestamp"] = f"lte.{date_to} 23:59:59"
        r = requests.get(
            f"{url}/rest/v1/queue_logs",
            headers=_headers_select(key),
            params=params,
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        return []
    except Exception:
        return []


def fetch_distinct_users(cfg: dict) -> list:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return []
    try:
        r = requests.get(
            f"{url}/rest/v1/activity_logs",
            headers=_headers_select(key),
            params={"select": "username", "order": "username"},
            timeout=10,
        )
        if r.status_code == 200:
            seen = set()
            users = []
            for row in r.json():
                u = row.get("username", "")
                if u and u not in seen:
                    seen.add(u)
                    users.append(u)
            return users
        return []
    except Exception:
        return []


# ── Migration: upload local logs ครั้งแรก ──────────────────────

def _migration_flag_path() -> str:
    import os
    from config import CONFIG_FILE
    return os.path.join(os.path.dirname(CONFIG_FILE), ".cloud_log_migrated")


def needs_migration() -> bool:
    import os
    return not os.path.exists(_migration_flag_path())


def migrate_local_logs(cfg: dict, activity_rows: list,
                       queue_rows: list) -> dict:
    if not is_configured(cfg):
        return {"ok": False, "error": "ยังไม่ได้ตั้งค่า Supabase"}
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")

    a_count = 0
    try:
        if activity_rows:
            batch = []
            for r in activity_rows:
                batch.append({
                    "timestamp": r.get("time", "").replace("T", " "),
                    "username": r.get("user", ""),
                    "action": r.get("action", ""),
                    "detail": r.get("detail", ""),
                    "company": r.get("company", ""),
                    "machine": _machine + " (migrated)",
                })
            resp = requests.post(
                f"{url}/rest/v1/activity_logs",
                headers=_headers(key),
                json=batch,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                a_count = len(batch)
    except Exception as e:
        return {"ok": False, "error": f"Activity migration: {e}"}

    q_count = 0
    try:
        if queue_rows:
            batch = []
            for r in queue_rows:
                batch.append({
                    "timestamp": r.get("time", "").replace("T", " "),
                    "username": r.get("user", ""),
                    "detail": r.get("detail", ""),
                    "company": r.get("company", ""),
                    "item_count": r.get("count", 0),
                    "items": json.dumps(r.get("items", []), ensure_ascii=False),
                    "machine": _machine + " (migrated)",
                })
            resp = requests.post(
                f"{url}/rest/v1/queue_logs",
                headers=_headers(key),
                json=batch,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                q_count = len(batch)
    except Exception as e:
        return {"ok": False, "error": f"Queue migration: {e}"}

    import os
    with open(_migration_flag_path(), "w") as f:
        f.write(datetime.now().isoformat())

    return {"ok": True, "activity": a_count, "queue": q_count}


# ── Cloud Users (sync user accounts) ────────────────────────────

def upsert_user(cfg: dict, user_data: dict) -> bool:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return False
    try:
        headers = _headers(key)
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        r = requests.post(
            f"{url}/rest/v1/users",
            headers=headers,
            json=user_data,
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def upsert_user_async(cfg: dict, user_data: dict):
    if not is_configured(cfg):
        return
    t = threading.Thread(target=upsert_user, args=(cfg, user_data), daemon=True)
    t.start()


def fetch_cloud_users(cfg: dict) -> list:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return []
    try:
        r = requests.get(
            f"{url}/rest/v1/users",
            headers=_headers_select(key),
            params={"select": "*", "order": "username"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        return []
    except Exception:
        return []


def delete_cloud_user(cfg: dict, username: str) -> bool:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return False
    try:
        r = requests.delete(
            f"{url}/rest/v1/users",
            headers=_headers(key),
            params={"username": f"eq.{username}"},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception:
        return False


def migrate_users(cfg: dict, local_users: list) -> dict:
    if not is_configured(cfg):
        return {"ok": False, "error": "ยังไม่ได้ตั้งค่า Supabase"}
    count = 0
    for u in local_users:
        data = {
            "username": u.get("username", ""),
            "salt": u.get("salt", ""),
            "pwhash": u.get("pwhash", ""),
            "fullname": u.get("fullname", ""),
            "nickname": u.get("nickname", ""),
            "role": u.get("role", "user"),
        }
        if upsert_user(cfg, data):
            count += 1
    return {"ok": True, "count": count}


# ── Test connection ──────────────────────────────────────────────

def test_connection(cfg: dict) -> tuple:
    url = cfg.get("supabase_url", "").rstrip("/")
    key = cfg.get("supabase_key", "")
    if not url or not key:
        return False, "ยังไม่ได้กรอก URL หรือ API Key"
    try:
        r = requests.get(
            f"{url}/rest/v1/activity_logs",
            headers=_headers_select(key),
            params={"select": "id", "limit": "1"},
            timeout=10,
        )
        if r.status_code == 200:
            return True, "เชื่อมต่อสำเร็จ"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)
