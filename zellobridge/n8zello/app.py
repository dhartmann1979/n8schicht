"""
N8 ZelloBridge – web server (Python standard library only: http.server, sqlite3,
Server-Sent Events). Runs well on the Asus Tinker Board next to the bridge.

Environment:
  N8ZELLO_DATA   data directory (SQLite), default /var/lib/n8zello
  N8ZELLO_PORT   web port, default 80
  N8ZELLO_BIND   listen address, default 0.0.0.0
  N8ZELLO_DEMO   1 = demo mode (invented values and traffic)
"""
from __future__ import annotations

import gzip
import json
import logging
import mimetypes
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .auth import Auth, AuthError
from .bridge import Bridge
from .db import DB
from .settings import Settings, SettingsError
from .system import Hardware, Services, sudo, udp_listening

log = logging.getLogger("n8zello")
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("N8ZELLO_DATA", "/var/lib/n8zello"))
DEMO = os.environ.get("N8ZELLO_DEMO", "") not in ("", "0")
COOKIE = "n8zello_session"
BACKUP_HELPER = "/usr/local/sbin/n8zello-backup"
SECRET_KEYS = re.compile(r"PASSWORD|SECRET|TOKEN|ISSUER", re.I)


class HTTPError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


class Hub:
    """Server-Sent Events: every browser gets its own queue."""

    def __init__(self):
        self.clients: List[Tuple[queue.Queue, bool]] = []
        self.lock = threading.Lock()

    def add(self, admin: bool) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=300)
        with self.lock:
            self.clients.append((q, admin))
        return q

    def remove(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients = [c for c in self.clients if c[0] is not q]

    def send(self, ev: Dict[str, Any], admin_only: bool = False) -> None:
        data = json.dumps(ev, separators=(",", ":"))
        with self.lock:
            for q, admin in self.clients:
                if admin_only and not admin:
                    continue
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass


class App:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.db = DB(DATA / "n8zello.db")
        self.settings = Settings(self.db)
        self.auth = Auth(self.db)
        self.hub = Hub()
        self.hw = Hardware(DEMO)
        self.services = Services(DEMO)
        if DEMO:
            from .demo import seed
            seed(self.db)
        self.bridge = Bridge(self.db, self._bridge_event, self.settings.get("bridge.service"))
        self.svc: Dict[str, Dict[str, Any]] = {}
        self.env: Dict[str, str] = {}
        self.started = time.time()
        self._stop = threading.Event()
        self._minute: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ events from the bridge parser
    def _bridge_event(self, ev: Dict[str, Any]) -> None:
        if ev.get("type") in ("talk", "heard") and not self.settings.get("dashboard.show_users"):
            ev = json.loads(json.dumps(ev))
            for k in ("talk", "entry"):
                if ev.get(k) and ev[k].get("user"):
                    ev[k]["user"] = "Zello"
        self.hub.send(ev)

    # ------------------------------------------------------------ background threads
    def start(self) -> None:
        if DEMO:
            from .demo import live
            threading.Thread(target=live, args=(self.bridge, self._stop), daemon=True).start()
        else:
            threading.Thread(target=self.bridge.follow, daemon=True, name="journal").start()
        threading.Thread(target=self._sampler, daemon=True, name="sampler").start()

    def stop(self) -> None:
        self._stop.set()
        self.bridge.stop()

    def refresh_services(self) -> bool:
        unit = self.settings.get("bridge.service")
        units = [unit] + self.services.find_extra(self.settings.get("bridge.extra"), unit)
        out = {}
        for u in units:
            d = self.services.show(u)
            env = Services.parse_env(d.get("Environment", "")) if u == unit else {}
            if u == unit:
                self.env = env
            out[u] = {"unit": u, "bridge": u == unit, "active": d.get("ActiveState", "unknown"), "sub": d.get("SubState", ""),
                      "pid": int(d.get("MainPID") or 0), "restarts": int(d.get("NRestarts") or 0),
                      "since": _systemd_ts(d.get("ActiveEnterTimestamp", "")), "description": d.get("Description", ""),
                      "memory": int(d["MemoryCurrent"]) if d.get("MemoryCurrent", "").isdigit() else None,
                      "enabled": d.get("UnitFileState", ""), "files": [p for p in [d.get("FragmentPath", "")] + d.get("DropInPaths", "").split() if p]}
        changed = {k: (v["active"], v["pid"], v["restarts"]) for k, v in out.items()} != \
                  {k: (v["active"], v["pid"], v["restarts"]) for k, v in self.svc.items()}
        self.svc = out
        b = out.get(unit)
        if b and b["active"] != "active" and self.bridge.zello not in ("stopped",):
            with self.bridge.lock:
                self.bridge._set_zello("stopped", time.time())
        return changed

    def _sampler(self) -> None:
        n = 0
        while not self._stop.is_set():
            try:
                if n % 2 == 0:
                    if self.refresh_services():
                        self.hub.send({"type": "services", "services": self.public_services(False)})
                        self.hub.send({"type": "services", "services": self.public_services(True)}, admin_only=True)
                self.hw.iface = self.settings.get("bridge.iface")
                unit = self.settings.get("bridge.service")
                d = self.hw.sample(self.svc.get(unit, {}).get("pid", 0))
                self._minute.append(d)
                self.hub.send({"type": "hw", "hw": self.public_hw(d)})
                if int(d["ts"]) // 60 != int(self._minute[0]["ts"]) // 60 or len(self._minute) > 14:
                    self._store_minute()
                if n % 720 == 3:
                    self.cleanup()
            except Exception:
                log.exception("sampler")
            n += 1
            self._stop.wait(5)

    def _store_minute(self) -> None:
        m, self._minute = self._minute, []
        if not m:
            return
        avg = lambda k: sum((x.get(k) or 0) for x in m) / len(m)   # noqa: E731
        b = [x["bridge"] for x in m if x.get("bridge")]
        self.db.x("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (int(m[-1]["ts"]) // 60 * 60, round(avg("cpu"), 1), round(avg("temp"), 1) if m[-1].get("temp") is not None else None,
                   int(avg("mem_used")), m[-1].get("mem_total"), round(avg("rx"), 1), round(avg("tx"), 1), m[-1]["load"][0],
                   round(sum((x.get("cpu") or 0) for x in b) / len(b), 1) if b else None, b[-1].get("rss") if b else None))

    def cleanup(self) -> None:
        s, now = self.settings.all(), time.time()
        self.db.x("DELETE FROM talks WHERE ts < ?", (now - s["data.talk_days"] * 86400,))
        self.db.x("DELETE FROM events WHERE ts < ?", (now - s["data.event_days"] * 86400,))
        self.db.x("DELETE FROM samples WHERE ts < ?", (now - s["data.sample_days"] * 86400,))
        self.db.x("DELETE FROM sessions WHERE expires_at < ?", (now,))
        self.db.x("PRAGMA optimize")

    # ------------------------------------------------------------ views
    @staticmethod
    def public_hw(d: Dict[str, Any]) -> Dict[str, Any]:
        return {k: d.get(k) for k in ("ts", "cpu", "cores", "temp", "temps", "freq", "mem_total", "mem_used", "swap_total", "swap_used",
                                      "disk_total", "disk_used", "iface", "rx", "tx", "rx_total", "tx_total", "load", "uptime", "bridge")}

    def public_services(self, admin: bool) -> List[Dict[str, Any]]:
        out = []
        for s in self.svc.values():
            d = dict(s)
            if not admin:
                d.pop("files", None)
            out.append(d)
        return out

    def config(self, admin: bool) -> Dict[str, Any]:
        e = self.env
        cfg = {"channel": e.get("ZELLO_CHANNEL", ""), "endpoint": e.get("ZELLO_WS_ENDPOINT", "") or self.bridge.endpoint,
               "mode": "Zello Work" if e.get("ZELLO_API_ENDPOINT") else ("Zello Free" if e.get("ZELLO_PRIVATE_KEY") else ""),
               "usrp_rx": e.get("USRP_RXPORT", ""), "usrp_tx": e.get("USRP_TXPORT", ""),
               "gain_rx": e.get("USRP_GAIN_RX_DB", self.bridge.gains.get("rx", "")),
               "gain_tx": e.get("USRP_GAIN_TX_DB", self.bridge.gains.get("tx", ""))}
        try:
            cfg["usrp_listening"] = udp_listening(int(e.get("USRP_RXPORT") or 0))
        except ValueError:
            cfg["usrp_listening"] = None
        if DEMO:
            cfg["usrp_listening"] = True
        if admin:
            cfg["username"] = e.get("ZELLO_USERNAME", "")
            cfg["env"] = {k: ("••••••" if SECRET_KEYS.search(k) and v else v) for k, v in sorted(e.items())}
        return cfg

    def today(self) -> Dict[str, Any]:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        r = self.db.q("SELECT dir, COUNT(*) n, COALESCE(SUM(duration),0) s, COUNT(DISTINCT NULLIF(user,'')) u FROM talks "
                      "WHERE ts>=? AND end_ts IS NOT NULL GROUP BY dir", (start,))
        d = {x["dir"]: dict(x) for x in r}
        return {"talks": sum(x["n"] for x in d.values()), "airtime": sum(x["s"] for x in d.values()),
                "rf": d.get("rf", {}).get("n", 0), "zello": d.get("zello", {}).get("n", 0), "users": d.get("zello", {}).get("u", 0)}

    def live(self, admin: bool) -> Dict[str, Any]:
        show = admin or self.settings.get("dashboard.show_users")
        lh = [dict(x) for x in self.bridge.last_heard[:25]]
        st = self.bridge.state()
        if not show:
            for x in lh:
                x["user"] = "Zello" if x["user"] else ""
            if st["talk"] and st["talk"].get("user"):
                st["talk"] = {**st["talk"], "user": "Zello"}
        return {"bridge": st, "services": self.public_services(admin), "hw": self.public_hw(self.hw.now) if self.hw.now else None,
                "host": self.hw.static, "config": self.config(admin), "today": self.today(), "last_heard": lh,
                "events": self.bridge.events[:30]}

    def stats(self, rng: str) -> Dict[str, Any]:
        now = time.time()
        today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        spans = {"today": (today0.timestamp(), 3600), "24h": (now - 86400, 3600), "7d": (now - 7 * 86400, 6 * 3600),
                 "30d": (now - 30 * 86400, 86400), "1y": (now - 365 * 86400, 7 * 86400)}
        if rng not in spans:
            raise HTTPError(400, "Unknown range.")
        start, step = spans[rng]
        start = int(start // step * step) if step < 86400 else int(datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0).timestamp())
        n = max(1, int((now - start) // step) + 1)
        buckets = [{"ts": start + i * step, "rf": 0, "zello": 0, "rf_s": 0.0, "zello_s": 0.0} for i in range(n)]
        for r in self.db.q("SELECT ts, dir, duration FROM talks WHERE ts>=? AND end_ts IS NOT NULL", (start,)):
            i = int((r["ts"] - start) // step) if step < 86400 else (datetime.fromtimestamp(r["ts"]).date() - datetime.fromtimestamp(start).date()).days // (step // 86400)
            if 0 <= i < n:
                buckets[i][r["dir"]] += 1
                buckets[i][r["dir"] + "_s"] += r["duration"] or 0
        tot = self.db.q("SELECT dir, COUNT(*) n, COALESCE(SUM(duration),0) s, MAX(duration) mx FROM talks WHERE ts>=? AND end_ts IS NOT NULL "
                        "GROUP BY dir", (start,))
        totals = {r["dir"]: {"talks": r["n"], "airtime": r["s"], "longest": r["mx"]} for r in tot}
        top = [dict(r) for r in self.db.q("SELECT user, COUNT(*) talks, SUM(duration) airtime, MAX(ts) last FROM talks WHERE ts>=? AND dir='zello' "
                                          "AND user<>'' AND end_ts IS NOT NULL GROUP BY user ORDER BY airtime DESC LIMIT 10", (start,))]
        if not self.settings.get("dashboard.show_users"):
            top = []
        hours = [0] * 24
        for r in self.db.q("SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) h, COUNT(*) n FROM talks WHERE ts>=? GROUP BY h", (start,)):
            hours[r["h"]] = r["n"]
        hstep = {"today": 600, "24h": 600, "7d": 3600, "30d": 6 * 3600, "1y": 86400}[rng]
        hw = [dict(r) for r in self.db.q(
            "SELECT (ts/?)*? AS ts, ROUND(AVG(cpu),1) cpu, ROUND(AVG(temp),1) temp, ROUND(MAX(temp),1) temp_max, AVG(mem_used) mem_used, "
            "MAX(mem_total) mem_total, AVG(rx_rate) rx, AVG(tx_rate) tx, ROUND(AVG(bridge_cpu),1) bridge_cpu "
            "FROM samples WHERE ts>=? GROUP BY ts/? ORDER BY 1", (hstep, hstep, start, hstep))]
        return {"range": rng, "step": step, "start": start, "buckets": buckets, "totals": totals, "top": top, "hours": hours, "hw": hw,
                "hw_step": hstep}


def _systemd_ts(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s or s == "n/a":
        return None
    try:
        return datetime.strptime(" ".join(s.split()[1:3]), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


APP: Optional[App] = None


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "N8ZelloBridge"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):          # quiet; errors go to the journal via logging
        pass

    # helpers
    def _ip(self) -> str:
        return self.client_address[0]

    def _user(self) -> Optional[Dict[str, Any]]:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return APP.auth.session_user(c[COOKIE].value if COOKIE in c else None)

    def _send(self, status: int, body: bytes, ctype: str, headers: Optional[Dict[str, str]] = None) -> None:
        gz = len(body) > 900 and "gzip" in self.headers.get("Accept-Encoding", "") and not ctype.startswith(("image/png",))
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> None:
        self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json", {"Cache-Control": "no-store", **(headers or {})})

    def _body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise HTTPError(413, "Too large.")
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise HTTPError(400, "Invalid request.")
        if not isinstance(b, dict):
            raise HTTPError(400, "Invalid request.")
        return b

    def _need_view(self) -> Optional[Dict[str, Any]]:
        u = self._user()
        if not u and not APP.settings.get("dashboard.public"):
            raise HTTPError(401, "Please log in.")
        return u

    def _need_admin(self) -> Dict[str, Any]:
        u = self._user()
        if not u:
            raise HTTPError(401, "Please log in.")
        return u

    # dispatch
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        path, qs = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if method != "GET" and path.startswith("/api/") and self.headers.get("X-N8") != "1":
                raise HTTPError(403, "Missing X-N8 header.")
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            fn = ROUTES.get((method, path))
            if fn is None:
                m = re.match(r"^/api/admin/users/(\d+)$", path)
                if m and method == "DELETE":
                    return api_user_delete(self, int(m.group(1)))
                raise HTTPError(404, "Not found.")
            fn(self, qs)
        except HTTPError as e:
            self._json({"error": e.msg}, e.status)
        except (AuthError, SettingsError) as e:
            self._json({"error": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                   # pragma: no cover
            log.exception("request %s %s", method, path)
            try:
                self._json({"error": f"Internal error: {e}"}, 500)
            except Exception:
                pass

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{V}}", __version__)
            return self._send(200, html.encode(), "text/html; charset=utf-8", {"Cache-Control": "no-cache"})
        if not path.startswith("/static/"):
            raise HTTPError(404, "Not found.")
        f = (STATIC / path[len("/static/"):]).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            raise HTTPError(404, "Not found.")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype, {"Cache-Control": "public, max-age=604800" if "v=" in self.path else "public, max-age=600"})


# ---------------------------------------------------------------- API
def api_info(h: Handler, qs):
    u = h._user()
    s = APP.settings.all()
    info = {"title": s["dashboard.title"], "subtitle": s["dashboard.subtitle"], "version": __version__, "demo": DEMO,
            "public": s["dashboard.public"], "setup_needed": not APP.auth.has_superadmin(), "user": u}
    if not u and not s["dashboard.public"]:
        info["login_required"] = True
    h._json(info)


def api_live(h: Handler, qs):
    u = h._need_view()
    h._json(APP.live(bool(u)))


def api_stats(h: Handler, qs):
    h._need_view()
    h._json(APP.stats(qs.get("range", "24h")))


def api_events(h: Handler, qs):
    u = h._need_view()
    q = APP.hub.add(bool(u))
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("X-Accel-Buffering", "no")
    h.end_headers()
    h.close_connection = True
    try:
        h.wfile.write(b"retry: 3000\n\n")
        h.wfile.flush()
        while True:
            try:
                data = q.get(timeout=20)
                h.wfile.write(f"data: {data}\n\n".encode())
            except queue.Empty:
                h.wfile.write(b": ping\n\n")
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        APP.hub.remove(q)


def api_login(h: Handler, qs):
    b = h._body()
    hours = 24 * 30 if b.get("remember") else float(APP.settings.get("dashboard.session_hours"))
    r = APP.auth.login(str(b.get("username", "")), str(b.get("password", "")), h._ip(), hours)
    cookie = f"{COOKIE}={r['token']}; Path=/; HttpOnly; SameSite=Lax" + (f"; Max-Age={r['max_age']}" if b.get("remember") else "")
    h._json({"user": r["user"]}, headers={"Set-Cookie": cookie})


def api_logout(h: Handler, qs):
    c = SimpleCookie(h.headers.get("Cookie", ""))
    APP.auth.logout(c[COOKIE].value if COOKIE in c else None)
    h._json({"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})


def api_password(h: Handler, qs):
    u = h._need_admin()
    b = h._body()
    APP.auth.change_password(u, str(b.get("old", "")), str(b.get("new", "")), h._ip())
    h._json({"ok": True})


def api_settings_get(h: Handler, qs):
    h._need_admin()
    h._json({"sections": APP.settings.describe()})


def api_settings_post(h: Handler, qs):
    u = h._need_admin()
    b = h._body()
    changed = APP.settings.update(b.get("changes") or {}, u["username"])
    if changed:
        APP.auth.audit("settings_changed", u["username"], ", ".join(changed), h._ip())
        if "bridge.service" in changed:
            APP.bridge.unit = APP.settings.get("bridge.service")
            APP.db.kv_set("journal.cursor", "")
    h._json({"changed": changed})


def api_service(h: Handler, qs):
    u = h._need_admin()
    b = h._body()
    unit, action = str(b.get("unit", "")), str(b.get("action", ""))
    if unit not in APP.svc:
        raise HTTPError(400, "Unknown service.")
    try:
        rc, out = APP.services.control(unit, action)
    except ValueError as e:
        raise HTTPError(400, str(e))
    if rc != 0:
        raise HTTPError(500, f"systemctl {action} {unit}: {out.strip()[:300] or 'failed'}")
    APP.auth.audit("service", u["username"], f"{action} {unit}", h._ip())
    time.sleep(1.5)
    APP.refresh_services()
    h._json({"ok": True, "services": APP.public_services(True)})


def api_journal(h: Handler, qs):
    h._need_admin()
    unit = qs.get("unit", APP.settings.get("bridge.service"))
    if unit not in APP.svc and unit != "n8zello":
        raise HTTPError(400, "Unknown service.")
    h._json({"text": APP.services.journal(unit, int(qs.get("lines", 300)))})


def api_backup(h: Handler, qs):
    """Configuration of the bridge (unit, drop-ins, key, SvxLink) plus the dashboard database as .tar.gz."""
    u = h._need_admin()
    APP.auth.audit("backup", u["username"], "", h._ip())
    name = f"zellobridge-backup-{datetime.now():%Y%m%d-%H%M}.tar.gz"
    if DEMO:
        data = gzip.compress(b"demo backup\n")
    else:
        snap = DATA / "n8zello-backup.db"
        try:
            snap.unlink()
        except FileNotFoundError:
            pass
        with APP.db.lock:
            APP.db.conn.execute("VACUUM INTO ?", (str(snap),))
        p = subprocess.run([*sudo(), BACKUP_HELPER], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        try:
            snap.unlink()
        except FileNotFoundError:
            pass
        if p.returncode != 0 or not p.stdout:
            raise HTTPError(500, "Backup failed: " + p.stderr.decode("utf-8", "replace")[:300])
        data = p.stdout
    h._send(200, data, "application/gzip", {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def api_system(h: Handler, qs):
    h._need_admin()
    counts = {t: APP.db.q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ("talks", "events", "samples", "users")}
    size = sum(p.stat().st_size for p in DATA.glob("n8zello.db*"))
    h._json({"version": __version__, "demo": DEMO, "uptime": time.time() - APP.started, "counts": counts, "db_size": size,
             "data_dir": str(DATA), "host": APP.hw.static, "clients": len(APP.hub.clients)})


def api_power(h: Handler, qs):
    u = h._need_admin()
    if u["role"] != "superadmin":
        raise HTTPError(403, "Only the superadmin may reboot the board.")
    b = h._body()
    try:
        rc, out = APP.services.power(str(b.get("action", "")))
    except ValueError as e:
        raise HTTPError(400, str(e))
    APP.auth.audit(str(b.get("action")), u["username"], "", h._ip())
    h._json({"ok": rc == 0})


def api_users(h: Handler, qs):
    h._need_admin()
    h._json({"users": APP.auth.users()})


def api_user_create(h: Handler, qs):
    u = h._need_admin()
    b = h._body()
    APP.auth.create_admin(u, str(b.get("username", "")), str(b.get("password", "")), str(b.get("display_name", "")), h._ip())
    h._json({"users": APP.auth.users()})


def api_user_delete(h: Handler, uid: int):
    u = h._need_admin()
    APP.auth.delete_admin(u, uid, h._ip())
    h._json({"users": APP.auth.users()})


def api_audit(h: Handler, qs):
    h._need_admin()
    h._json({"sessions": APP.auth.sessions(), "entries": APP.auth.audit_log()})


def api_events_list(h: Handler, qs):
    h._need_view()
    lim = min(int(qs.get("limit", 200)), 1000)
    h._json({"events": [dict(r) for r in APP.db.q("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (lim,))]})


ROUTES = {
    ("GET", "/api/info"): api_info, ("GET", "/api/live"): api_live, ("GET", "/api/stats"): api_stats,
    ("GET", "/api/events"): api_events, ("GET", "/api/eventlog"): api_events_list,
    ("POST", "/api/auth/login"): api_login, ("POST", "/api/auth/logout"): api_logout, ("POST", "/api/auth/password"): api_password,
    ("GET", "/api/admin/settings"): api_settings_get, ("POST", "/api/admin/settings"): api_settings_post,
    ("POST", "/api/admin/service"): api_service, ("GET", "/api/admin/journal"): api_journal, ("GET", "/api/admin/backup"): api_backup,
    ("GET", "/api/admin/system"): api_system, ("POST", "/api/admin/power"): api_power,
    ("GET", "/api/admin/users"): api_users, ("POST", "/api/admin/users"): api_user_create, ("GET", "/api/admin/audit"): api_audit,
}


def main() -> None:
    global APP
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    APP = App()
    APP.refresh_services()
    APP.start()
    srv = ThreadingHTTPServer((os.environ.get("N8ZELLO_BIND", "0.0.0.0"), int(os.environ.get("N8ZELLO_PORT", "80"))), Handler)
    srv.daemon_threads = True
    log.info("N8 ZelloBridge %s on port %s", __version__, srv.server_address[1])
    try:
        srv.serve_forever()
    finally:
        APP.stop()


def make_app() -> App:
    """For tests: an App without HTTP server."""
    global APP
    APP = App()
    return APP

