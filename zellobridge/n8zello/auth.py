"""
Users, logins and sessions – as on the other N8 servers:

* ``superadmin`` – one account, created by the guided installation (or the CLI);
  manages admins. ``admin`` – restarts services, settings, backup, logs.
* No factory login. Passwords as scrypt hashes, sessions as SHA-256 of a random token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from .db import DB

ROLES = ("superadmin", "admin")
_N, _R, _P = 2 ** 14, 8, 1
NAME_RE = re.compile(r"[A-Za-z0-9._@-]{2,32}")


class AuthError(Exception):
    """Message is shown to the user."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        want_b = base64.b64decode(want)
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            dklen=len(want_b), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(dk, want_b)
    except Exception:
        return False


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, db: DB):
        self.db = db
        self._fails: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def public(row) -> Dict[str, Any]:
        return {"id": row["id"], "username": row["username"], "role": row["role"], "display_name": row["display_name"],
                "disabled": bool(row["disabled"]), "created_at": row["created_at"], "last_login": row["last_login"]}

    def audit(self, action: str, username: str = "", detail: str = "", ip: str = "") -> None:
        self.db.x("INSERT INTO audit (ts, username, action, detail, ip) VALUES (?,?,?,?,?)", (time.time(), username, action, detail[:500], ip))

    def has_superadmin(self) -> bool:
        return bool(self.db.q("SELECT 1 FROM users WHERE role='superadmin' LIMIT 1"))

    @staticmethod
    def _check_pw(password: str) -> None:
        if len(password or "") < 10:
            raise AuthError("The password needs at least 10 characters.")

    def set_superadmin(self, username: str, password: str) -> None:
        if not NAME_RE.fullmatch(username or ""):
            raise AuthError("Username: 2–32 characters, letters, digits, . _ @ -")
        self._check_pw(password)
        h = hash_password(password)
        self.db.x("UPDATE users SET role='admin' WHERE role='superadmin' AND username<>?", (username,))
        if self.db.q("SELECT 1 FROM users WHERE username=?", (username,)):
            self.db.x("UPDATE users SET password_hash=?, role='superadmin', disabled=0 WHERE username=?", (h, username))
        else:
            self.db.x("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)", (username, h, "superadmin", time.time()))
        self.audit("superadmin_set", username, "command line / installation")

    def reset(self, username: str, password: str) -> None:
        self._check_pw(password)
        rows = self.db.q("SELECT id FROM users WHERE username=?", (username,))
        if not rows:
            raise AuthError(f"Unknown user {username}.")
        self.db.x("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), rows[0]["id"]))
        self.db.x("DELETE FROM sessions WHERE user_id=?", (rows[0]["id"],))
        self.audit("password_reset", username, "command line")

    # ------------------------------------------------------------ login & sessions
    def login(self, username: str, password: str, ip: str, hours: float) -> Dict[str, Any]:
        key = f"{ip}|{(username or '').lower()}"
        now = time.time()
        with self._lock:
            recent = [t for t in self._fails.get(key, []) if now - t < 900]
            self._fails[key] = recent
            if len(recent) >= 5:
                raise AuthError("Too many failed attempts. Please wait 15 minutes.")
        rows = self.db.q("SELECT * FROM users WHERE username=?", ((username or "").strip(),))
        row = rows[0] if rows else None
        if not row or row["disabled"] or not verify_password(password or "", row["password_hash"]):
            with self._lock:
                self._fails.setdefault(key, []).append(now)
            self.audit("login_failed", (username or "")[:64], "", ip)
            raise AuthError("Wrong username or password.")
        with self._lock:
            self._fails.pop(key, None)
        token = secrets.token_urlsafe(32)
        self.db.x("INSERT INTO sessions (id, user_id, created_at, expires_at, ip) VALUES (?,?,?,?,?)",
                  (_th(token), row["id"], now, now + hours * 3600, ip))
        self.db.x("UPDATE users SET last_login=? WHERE id=?", (now, row["id"]))
        self.db.x("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self.audit("login", row["username"], "", ip)
        return {"token": token, "max_age": int(hours * 3600), "user": self.public(row)}

    def session_user(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        rows = self.db.q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires_at>? AND u.disabled=0",
                         (_th(token), time.time()))
        return self.public(rows[0]) if rows else None

    def logout(self, token: Optional[str]) -> None:
        if token:
            self.db.x("DELETE FROM sessions WHERE id=?", (_th(token),))

    def change_password(self, user: Dict[str, Any], old: str, new: str, ip: str) -> None:
        row = self.db.q("SELECT * FROM users WHERE id=?", (user["id"],))[0]
        if not verify_password(old or "", row["password_hash"]):
            raise AuthError("The current password is wrong.")
        self._check_pw(new)
        self.db.x("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user["id"]))
        self.db.x("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        self.audit("password_changed", user["username"], "", ip)

    # ------------------------------------------------------------ admins (managed by the superadmin)
    def users(self) -> List[Dict[str, Any]]:
        return [self.public(r) for r in self.db.q("SELECT * FROM users ORDER BY role DESC, username")]

    def create_admin(self, actor: Dict[str, Any], username: str, password: str, display_name: str, ip: str) -> None:
        if actor["role"] != "superadmin":
            raise AuthError("Only the superadmin manages admins.")
        if not NAME_RE.fullmatch(username or ""):
            raise AuthError("Username: 2–32 characters, letters, digits, . _ @ -")
        self._check_pw(password)
        if self.db.q("SELECT 1 FROM users WHERE username=?", (username,)):
            raise AuthError("This username already exists.")
        self.db.x("INSERT INTO users (username, password_hash, role, display_name, created_at) VALUES (?,?,?,?,?)",
                  (username, hash_password(password), "admin", display_name[:64], time.time()))
        self.audit("user_created", actor["username"], username, ip)

    def delete_admin(self, actor: Dict[str, Any], uid: int, ip: str) -> None:
        if actor["role"] != "superadmin":
            raise AuthError("Only the superadmin manages admins.")
        rows = self.db.q("SELECT * FROM users WHERE id=?", (uid,))
        if not rows:
            raise AuthError("Unknown user.")
        if rows[0]["role"] == "superadmin":
            raise AuthError("The superadmin cannot be deleted.")
        self.db.x("DELETE FROM users WHERE id=?", (uid,))
        self.audit("user_deleted", actor["username"], rows[0]["username"], ip)

    def sessions(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.q("SELECT s.created_at, s.expires_at, s.ip, u.username FROM sessions s JOIN users u ON u.id=s.user_id "
                                           "WHERE s.expires_at>? ORDER BY s.created_at DESC", (time.time(),))]

    def audit_log(self, limit: int = 200) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,))]
