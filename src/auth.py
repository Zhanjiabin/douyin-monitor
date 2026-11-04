"""Optional single-user sign-in for server deployments; no HTTP Basic auth."""
from contextlib import contextmanager
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit, unquote

from config import DATA_DIR

COOKIE_NAME = "gc_session"
LOCK = threading.RLock()
ATTEMPT_LIMIT = 8
ATTEMPT_WINDOW = 600


class TooManyAttempts(ValueError):
    pass


def settings():
    configured = os.environ.get("DOUYIN_AUTH_FILE")
    path = Path(configured) if configured else DATA_DIR / "auth" / "settings.json"
    if not path.exists():
        if configured:
            raise RuntimeError("登录配置不存在，服务器拒绝启动")
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("enabled") is False:
        if configured:
            raise RuntimeError("服务器登录保护不能关闭")
        return None
    if not isinstance(value.get("username"), str) or not value.get("password_hash", "").startswith("scrypt$"):
        raise RuntimeError("登录配置不完整")
    return value


def enabled():
    return settings() is not None


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=1,
                            dklen=32, maxmem=128 * 1024 * 1024)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def verify_password(password, stored):
    if not isinstance(password, str) or len(password) > 1024:
        return False
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt" or len(salt) != 32 or len(expected) != 64:
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=32768,
                                r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def credential_version(cfg):
    return hashlib.sha256((cfg["username"] + "\0" + cfg["password_hash"]).encode()).hexdigest()


@contextmanager
def database():
    root = Path(os.environ.get("DOUYIN_AUTH_DB", DATA_DIR / "auth" / "sessions.db"))
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(root, timeout=10)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, credential_version TEXT NOT NULL, expires REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS login_failures (peer TEXT PRIMARY KEY, since REAL NOT NULL, attempts INTEGER NOT NULL)")
        with conn:
            yield conn
    finally:
        conn.close()


def token(headers):
    try:
        cookie = SimpleCookie()
        cookie.load(headers.get("Cookie", ""))
        value = cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else ""
        return value if re.fullmatch(r"[A-Za-z0-9_-]{43}", value) else ""
    except (CookieError, ValueError):
        return ""


def current_user(headers):
    cfg = settings()
    value = token(headers)
    if not cfg or not value:
        return None
    with LOCK, database() as conn:
        conn.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        row = conn.execute("SELECT username,credential_version FROM sessions WHERE token_hash=?",
                           (hashlib.sha256(value.encode()).hexdigest(),)).fetchone()
    if row and hmac.compare_digest(row[1], credential_version(cfg)):
        return row[0]
    return None


def login(username, password, peer):
    cfg = settings()
    if not cfg:
        raise ValueError("当前无需登录")
    now = time.time()
    with LOCK, database() as conn:
        row = conn.execute("SELECT since,attempts FROM login_failures WHERE peer=?", (peer,)).fetchone()
        if row and now - row[0] < ATTEMPT_WINDOW and row[1] >= ATTEMPT_LIMIT:
            raise TooManyAttempts("尝试次数过多，请 10 分钟后重试")
        valid_password = verify_password(password, cfg["password_hash"])
        valid_name = isinstance(username, str) and hmac.compare_digest(username.encode(), cfg["username"].encode())
        if not valid_name or not valid_password:
            # Commit the failed attempt even though the caller receives an error.
            since, attempts = (row[0], row[1] + 1) if row and now-row[0] < ATTEMPT_WINDOW else (now, 1)
            conn.execute("INSERT INTO login_failures VALUES (?,?,?) ON CONFLICT(peer) DO UPDATE SET since=excluded.since,attempts=excluded.attempts", (peer, since, attempts))
            conn.commit()
            raise ValueError("账号或密码错误")
        value = secrets.token_urlsafe(32)
        hours = max(1, min(int(cfg.get("session_hours", 168)), 720))
        conn.execute("DELETE FROM login_failures WHERE peer=? OR since<?", (peer, now-ATTEMPT_WINDOW))
        conn.execute("DELETE FROM sessions WHERE expires<=? OR credential_version!=?", (now, credential_version(cfg)))
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", (hashlib.sha256(value.encode()).hexdigest(), cfg["username"], credential_version(cfg), now+hours*3600))
        conn.execute("DELETE FROM sessions WHERE token_hash NOT IN (SELECT token_hash FROM sessions ORDER BY expires DESC LIMIT 20)")
        return value, hours*3600


def logout(headers):
    value = token(headers)
    if value:
        with LOCK, database() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(value.encode()).hexdigest(),))


def session_cookie(value, max_age):
    cookie = SimpleCookie()
    cookie[COOKIE_NAME] = value
    item = cookie[COOKIE_NAME]
    item["path"] = "/"
    item["httponly"] = True
    item["samesite"] = "Lax"
    item["max-age"] = str(max_age)
    if os.environ.get("DOUYIN_PUBLIC_ORIGIN", "").startswith("https://"):
        item["secure"] = True
    return item.OutputString()


def same_origin(headers):
    if headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    origin = headers.get("Origin")
    if not origin:
        return True
    expected = os.environ.get("DOUYIN_PUBLIC_ORIGIN", "http://" + headers.get("Host", ""))
    return origin.rstrip("/") == expected.rstrip("/")


def client_ip(handler):
    peer = handler.client_address[0]
    if os.environ.get("DOUYIN_TRUST_PROXY") == "1" and peer in {"127.0.0.1", "::1"}:
        try:
            return str(ipaddress.ip_address(handler.headers.get("X-Real-IP", peer)))
        except ValueError:
            pass
    return peer


def safe_next(value):
    value = str(value or "/home")
    decoded = unquote(value)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "/home"
    if (not value.startswith("/") or decoded.startswith("//") or parsed.scheme or parsed.netloc
            or "\\" in decoded or any(ord(c) < 32 for c in decoded)
            or parsed.path in {"/login", "/api/auth/login", "/api/auth/logout"}):
        return "/home"
    return value
