"""
Users — ระบบผู้ใช้/login (requirement ข้อ 8)
เก็บข้างโปรแกรม (พกพา) แบบ "เข้ารหัส": kcash_users.dat
- รหัสผ่านเก็บเป็น hash (sha256+salt) ไม่เก็บ plaintext
- ทั้งไฟล์ถูก obfuscate (XOR+base64) อ่านตรง ๆ ไม่ออก

role: dev (สูงสุด) > admin > user
- admin: จัดการ user ได้ แต่ "มองไม่เห็น/แตะ dev ไม่ได้"
- dev: ทำได้ทุกอย่าง

แต่ละ user: {username, salt, pwhash, fullname, nickname, role}
"""
import os
import json
import base64
import hashlib
import secrets
from config import CONFIG_FILE

_KEY = b"KCash-Queue-2026-#bird-#dmat-secret-please-do-not-share-x9"


def _path() -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_users.dat")


def _xor(data: bytes) -> bytes:
    k = _KEY
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(data))


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.sha256((salt + (pw or "")).encode("utf-8")).hexdigest()


def _load() -> list:
    p = _path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, "rb") as f:
            raw = _xor(base64.b64decode(f.read())).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


def _save(users: list) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    raw = json.dumps(users, ensure_ascii=False).encode("utf-8")
    with open(p, "wb") as f:
        f.write(base64.b64encode(_xor(raw)))


def _remember_path() -> str:
    return os.path.join(os.path.dirname(CONFIG_FILE), "kcash_remember.dat")


def save_remember(username: str, password: str) -> None:
    """จำ username+password (เข้ารหัส XOR+base64)"""
    try:
        raw = json.dumps({"u": username, "p": password}, ensure_ascii=False).encode("utf-8")
        with open(_remember_path(), "wb") as f:
            f.write(base64.b64encode(_xor(raw)))
    except Exception:
        pass


def load_remember():
    """คืน (username, password) ถ้ามี ไม่งั้น (None, None)"""
    p = _remember_path()
    if not os.path.exists(p):
        return None, None
    try:
        with open(p, "rb") as f:
            d = json.loads(_xor(base64.b64decode(f.read())).decode("utf-8"))
        return d.get("u"), d.get("p")
    except Exception:
        return None, None


def clear_remember() -> None:
    try:
        p = _remember_path()
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def _mk(username, password, fullname, nickname, role):
    salt = secrets.token_hex(8)
    return {"username": username, "salt": salt,
            "pwhash": _hash_pw(password, salt),
            "fullname": fullname, "nickname": nickname, "role": role}


def ensure_seed():
    """สร้างไฟล์ผู้ใช้ครั้งแรก พร้อม admin (birdtk) + dev (dev)"""
    users = _load()
    if users is None:
        users = [
            _mk("birdtk", "0877971136", "Tanatkul Chongkriengkrai", "Bird", "admin"),
            _mk("dev", "11223343mild", "Developer", "Dev", "dev"),
        ]
        _save(users)
    else:
        # กัน dev/admin หลักหาย
        names = {u["username"] for u in users}
        changed = False
        if "dev" not in names:
            users.append(_mk("dev", "11223343mild", "Developer", "Dev", "dev")); changed = True
        if "birdtk" not in names:
            users.append(_mk("birdtk", "0877971136", "Tanatkul Chongkriengkrai", "Bird", "admin")); changed = True
        if changed:
            _save(users)
    return users


def _cloud_cfg():
    try:
        from config import load_config
        c = load_config()
        return {"supabase_url": c.get("supabase_url", ""), "supabase_key": c.get("supabase_key", "")}
    except Exception:
        return {}


def _try_cloud_auth(username: str, password: str):
    try:
        import cloud_log
        cfg = _cloud_cfg()
        if not cloud_log.is_configured(cfg):
            return None, False
        cloud_users = cloud_log.fetch_cloud_users(cfg)
        if not cloud_users:
            return None, False
        for u in cloud_users:
            if u.get("username", "").lower() == username.lower():
                salt = u.get("salt", "")
                if _hash_pw(password, salt) == u.get("pwhash", ""):
                    return {k: v for k, v in u.items()
                            if k not in ("salt", "pwhash", "id", "created_at", "updated_at")}, True
                return None, True
        return None, True
    except Exception:
        return None, False


def _sync_local_to_cloud():
    try:
        import cloud_log
        cfg = _cloud_cfg()
        if not cloud_log.is_configured(cfg):
            return
        local = _load()
        if not local:
            return
        flag = os.path.join(os.path.dirname(CONFIG_FILE), ".cloud_users_migrated")
        if os.path.exists(flag):
            return
        import threading
        def _do():
            cloud_log.migrate_users(cfg, local)
            with open(flag, "w") as f:
                f.write("done")
        threading.Thread(target=_do, daemon=True).start()
    except Exception:
        pass


def authenticate(username: str, password: str):
    """คืน user dict ถ้าถูกต้อง ไม่งั้น None
    เช็ค cloud ก่อน → fallback local → sync ทุกครั้ง"""
    username = (username or "").strip()
    if not username:
        return None

    cloud_result, cloud_ok = _try_cloud_auth(username, password)
    if cloud_ok and cloud_result is not None:
        _sync_local_to_cloud()
        return cloud_result

    for u in ensure_seed():
        if u["username"].lower() == username.lower():
            if _hash_pw(password, u["salt"]) == u["pwhash"]:
                _sync_local_to_cloud()
                try:
                    import cloud_log
                    cloud_log.upsert_user_async(_cloud_cfg(), {
                        "username": u["username"], "salt": u["salt"],
                        "pwhash": u["pwhash"], "fullname": u["fullname"],
                        "nickname": u["nickname"], "role": u["role"],
                    })
                except Exception:
                    pass
                return {k: v for k, v in u.items() if k not in ("salt", "pwhash")}
            return None
    return None


def list_users(viewer_role: str) -> list:
    """รายชื่อผู้ใช้ — admin จะไม่เห็น dev"""
    out = []
    for u in ensure_seed():
        if viewer_role != "dev" and u["role"] == "dev":
            continue   # admin มองไม่เห็น dev
        out.append({"username": u["username"], "fullname": u["fullname"],
                    "nickname": u["nickname"], "role": u["role"]})
    return out


def _find(users, username):
    for u in users:
        if u["username"].lower() == username.lower():
            return u
    return None


def add_user(viewer_role, username, password, fullname, nickname, role="user"):
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("ต้องมี username และ password")
    if viewer_role != "dev" and role != "user":
        raise PermissionError("admin สร้างได้เฉพาะผู้ใช้ทั่วไป (user) เท่านั้น")
    users = ensure_seed()
    if _find(users, username):
        raise ValueError(f"มี username '{username}' อยู่แล้ว")
    new_user = _mk(username, password, fullname or "", nickname or username, role)
    users.append(new_user)
    _save(users)
    try:
        import cloud_log
        cfg = _cloud_cfg()
        cloud_log.upsert_user_async(cfg, {
            "username": new_user["username"], "salt": new_user["salt"],
            "pwhash": new_user["pwhash"], "fullname": new_user["fullname"],
            "nickname": new_user["nickname"], "role": new_user["role"],
        })
    except Exception:
        pass


def delete_user(viewer_role, username):
    users = ensure_seed()
    u = _find(users, username)
    if not u:
        return
    if u["role"] == "dev":
        raise PermissionError("ลบ dev ไม่ได้")          # ห้ามแตะ dev เสมอ
    users = [x for x in users if x["username"].lower() != username.lower()]
    _save(users)
    try:
        import cloud_log
        cloud_log.delete_cloud_user(_cloud_cfg(), username)
    except Exception:
        pass


def update_user(viewer_role, username, *, password=None, fullname=None,
                nickname=None, role=None):
    users = ensure_seed()
    u = _find(users, username)
    if not u:
        raise ValueError("ไม่พบผู้ใช้")
    if u["role"] == "dev" and viewer_role != "dev":
        raise PermissionError("admin แก้ไข dev ไม่ได้")
    if viewer_role != "dev" and role is not None and role != "user":
        raise PermissionError("admin ตั้ง role ได้เฉพาะ user เท่านั้น")
    if password:
        u["salt"] = secrets.token_hex(8)
        u["pwhash"] = _hash_pw(password, u["salt"])
    if fullname is not None:
        u["fullname"] = fullname
    if nickname is not None:
        u["nickname"] = nickname
    if role is not None and u["role"] != "dev":
        u["role"] = role
    _save(users)
    try:
        import cloud_log
        cloud_log.upsert_user_async(_cloud_cfg(), {
            "username": u["username"], "salt": u["salt"],
            "pwhash": u["pwhash"], "fullname": u["fullname"],
            "nickname": u["nickname"], "role": u["role"],
        })
    except Exception:
        pass
