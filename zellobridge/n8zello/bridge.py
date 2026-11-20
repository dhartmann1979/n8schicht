"""
Live state of asl-zello-bridge (github.com/mattmelling/asl-zello-bridge), read from
its systemd journal. The bridge logs every key-up and key-down:

  INFO:ZelloController:Keyed:USRP              radio (AllStarLink/SvxLink via USRP) talks into Zello
  INFO:ZelloController:UnKeyed:USRP (12.3s)
  INFO:ZelloController:Keyed:DL1ABC            a Zello user talks to the radio side
  INFO:ZelloController:UnKeyed:DL1ABC (8.0s)
  INFO:ZelloController:Connecting to wss://…   / Logged in! / Channel is ready / Websocket closed! …

The journal is followed with ``journalctl -f -o json``; the cursor is kept in SQLite,
so nothing is lost while the dashboard restarts.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .db import DB

MSG_RE = re.compile(r"^(?:(DEBUG|INFO|WARNING|ERROR|CRITICAL):)?(?:([A-Za-z_][\w.]*):)?(.*)$", re.S)
KEY_RE = re.compile(r"^Keyed:(.+)$")
UNKEY_RE = re.compile(r"^UnKeyed:(.+?)(?: \((\d+(?:\.\d+)?)s\))?$")
STATS_RE = re.compile(r"Stats: start_attempts=(\d+) start_ok=(\d+) chn_not_ready=(\d+) read_timeouts=(\d+)")
GAIN_RE = re.compile(r"USRP (RX|TX) gain: (-?[\d.]+)dB")
LAST_HEARD = 50
EVENTS = 300


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class Bridge:
    def __init__(self, db: DB, notify: Callable[[Dict[str, Any]], None], unit: str = "asl-zello-bridge"):
        self.db = db
        self.notify = notify
        self.unit = unit
        self.lock = threading.RLock()
        self.zello = "unknown"            # connecting | online | ready | offline | error | stopped
        self.zello_since: Optional[float] = None
        self.endpoint = ""
        self.last_login: Optional[float] = None
        self.last_error: Optional[Dict[str, Any]] = None
        self.talk: Optional[Dict[str, Any]] = None
        self.stats: Dict[str, int] = {}
        self.gains: Dict[str, float] = {}
        self.counters = {"kicked": 0, "woodpecker": 0, "not_ready": 0, "ws_closed": 0, "errors": 0}
        self.last_heard: List[Dict[str, Any]] = [dict(r) for r in db.q(
            "SELECT * FROM talks WHERE end_ts IS NOT NULL ORDER BY ts DESC LIMIT ?", (LAST_HEARD,))]
        self.events: List[Dict[str, Any]] = [dict(r) for r in db.q("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (EVENTS,))]
        self.db.x("UPDATE talks SET end_ts=ts, duration=0 WHERE end_ts IS NULL")
        self._proc: Optional[subprocess.Popen] = None
        self._stop = False
        s = db.kv_get("bridge.state")
        if s:
            try:
                st = json.loads(s)
                self.zello, self.endpoint, self.last_login = st.get("zello", "unknown"), st.get("endpoint", ""), st.get("last_login")
                self.zello_since = st.get("since")
            except ValueError:
                pass

    # ------------------------------------------------------------ public state
    def state(self) -> Dict[str, Any]:
        with self.lock:
            return {"zello": self.zello, "since": self.zello_since, "endpoint": self.endpoint, "last_login": self.last_login,
                    "last_error": self.last_error, "talk": self.talk, "stats": self.stats, "gains": self.gains,
                    "counters": dict(self.counters)}

    def _save(self) -> None:
        self.db.kv_set("bridge.state", json.dumps({"zello": self.zello, "endpoint": self.endpoint, "last_login": self.last_login,
                                                   "since": self.zello_since}))

    def _set_zello(self, st: str, ts: float) -> None:
        if st != self.zello:
            self.zello, self.zello_since = st, ts
            self._save()
            self.notify({"type": "bridge", "bridge": self.state()})

    def _event(self, ts: float, level: str, kind: str, text: str) -> None:
        cur = self.db.x("INSERT INTO events (ts, level, kind, text) VALUES (?,?,?,?)", (ts, level, kind, text[:500]))
        e = {"id": cur.lastrowid, "ts": ts, "level": level, "kind": kind, "text": text[:500]}
        self.events.insert(0, e)
        del self.events[EVENTS:]
        self.notify({"type": "event", "event": e})

    # ------------------------------------------------------------ one journal line
    def feed(self, ts: float, message: str, ident: str = "") -> None:
        """One journal entry of the bridge unit (or of systemd about it)."""
        message = (message or "").strip()
        if not message:
            return
        with self.lock:
            if ident == "systemd":
                self._systemd(ts, message)
                return
            m = MSG_RE.match(message)
            level, msg = (m.group(1) or "INFO"), (m.group(3) or "").strip()
            k = KEY_RE.match(msg)
            if k and not msg.startswith("UnKeyed"):
                self._key(ts, k.group(1).strip())
                return
            u = UNKEY_RE.match(msg)
            if u:
                self._unkey(ts, u.group(1).strip(), float(u.group(2)) if u.group(2) else None)
                return
            if msg.startswith("Connecting to "):
                self.endpoint = msg[len("Connecting to "):].strip()
                self._set_zello("connecting", ts)
            elif msg in ("Logged in!", "Authentication successful!"):
                self.last_login = ts
                if self.zello != "ready":
                    self._set_zello("online", ts)
                if msg == "Logged in!":
                    self._event(ts, "ok", "login", "Logged in at Zello")
            elif msg == "Channel is ready":
                if self.zello != "ready":
                    self._event(ts, "ok", "ready", "Zello channel is ready")
                self._set_zello("ready", ts)
            elif msg.startswith("Websocket closed") or msg.startswith("WebSocket error"):
                self.counters["ws_closed"] += 1
                self._end_talk(ts)
                self._set_zello("offline", ts)
                self._event(ts, "warn", "disconnect", msg)
            elif msg.startswith("Authentication failed"):
                self._set_zello("error", ts)
                self._error(ts, msg)
            elif msg.startswith("Kicked from channel"):
                self.counters["kicked"] += 1
                self._set_zello("error", ts)
                self._error(ts, msg)
            elif msg.startswith("Woodpecker protection"):
                self.counters["woodpecker"] += 1
                self._event(ts, "warn", "woodpecker", msg)
            elif msg.startswith("Channel not ready"):
                self.counters["not_ready"] += 1
            elif STATS_RE.search(msg):
                s = STATS_RE.search(msg)
                self.stats = {"start_attempts": int(s.group(1)), "start_ok": int(s.group(2)), "chn_not_ready": int(s.group(3)),
                              "read_timeouts": int(s.group(4))}
                self._event(ts, "info", "stats", msg)
            elif GAIN_RE.search(msg):
                g = GAIN_RE.search(msg)
                self.gains[g.group(1).lower()] = float(g.group(2))
            elif msg in ("Initialising Zello",):
                self._event(ts, "info", "start", "Bridge started")
                self._set_zello("connecting", ts)
            elif level in ("ERROR", "CRITICAL") or msg.startswith("Traceback"):
                self._error(ts, msg)
            elif level == "WARNING":
                self._event(ts, "warn", "warning", msg)

    def _systemd(self, ts: float, msg: str) -> None:
        if msg.startswith("Started"):
            self._event(ts, "info", "service", msg)
        elif msg.startswith(("Stopped", "Stopping")) or "Main process exited" in msg or "Failed with result" in msg:
            if msg.startswith("Stopping"):
                return
            self._end_talk(ts)
            self._set_zello("stopped", ts)
            self._event(ts, "warn" if "Failed" in msg or "exited" in msg else "info", "service", msg)
        elif "Scheduled restart job" in msg:
            self._event(ts, "warn", "service", msg)

    def _error(self, ts: float, msg: str) -> None:
        self.counters["errors"] += 1
        self.last_error = {"ts": ts, "text": msg[:300]}
        self._event(ts, "error", "error", msg)
        self.notify({"type": "bridge", "bridge": self.state()})

    # ------------------------------------------------------------ talks
    def _key(self, ts: float, who: str) -> None:
        if self.talk:
            self._end_talk(ts)
        direction = "rf" if who == "USRP" else "zello"
        user = "" if who in ("USRP", "Unknown") else who
        cur = self.db.x("INSERT INTO talks (ts, dir, user) VALUES (?,?,?)", (ts, direction, user))
        self.talk = {"id": cur.lastrowid, "ts": ts, "dir": direction, "user": user}
        if self.zello in ("unknown", "offline", "connecting", "online"):
            self.zello = "ready"
        self.notify({"type": "talk", "talk": self.talk})

    def _unkey(self, ts: float, who: str, dur: Optional[float]) -> None:
        t = self.talk
        direction = "rf" if who == "USRP" else "zello"
        if not t or t["dir"] != direction:
            # key-up not seen (dashboard started later): record with the logged duration
            if dur is None:
                return
            user = "" if who in ("USRP", "Unknown") else who
            cur = self.db.x("INSERT INTO talks (ts, dir, user) VALUES (?,?,?)", (ts - dur, direction, user))
            t = {"id": cur.lastrowid, "ts": ts - dur, "dir": direction, "user": user}
        self.talk = t
        self._end_talk(ts, dur)

    def _end_talk(self, ts: float, dur: Optional[float] = None) -> None:
        t, self.talk = self.talk, None
        if not t:
            return
        dur = round(dur if dur is not None else max(0.0, ts - t["ts"]), 1)
        if dur < 0.5:
            self.db.x("DELETE FROM talks WHERE id=?", (t["id"],))
            self.notify({"type": "talk", "talk": None})
            return
        self.db.x("UPDATE talks SET end_ts=?, duration=? WHERE id=?", (t["ts"] + dur, dur, t["id"]))
        entry = {**t, "end_ts": t["ts"] + dur, "duration": dur}
        self.last_heard.insert(0, entry)
        del self.last_heard[LAST_HEARD:]
        self.notify({"type": "heard", "entry": entry})

    # ------------------------------------------------------------ journal follower
    def follow(self) -> None:
        """Runs in a thread: journalctl -f, restarted if it ends."""
        while not self._stop:
            cursor = self.db.kv_get("journal.cursor")
            cmd = ["journalctl", "-u", f"{self.unit}.service", "-o", "json", "-f", "--no-pager"]
            cmd += [f"--after-cursor={cursor}"] if cursor else ["--since", "-2d"]
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1, text=True,
                                              encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return
            n = 0
            for line in self._proc.stdout:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                msg = e.get("MESSAGE")
                if isinstance(msg, list):                 # journald sends binary messages as byte lists
                    msg = bytes(msg).decode("utf-8", "replace")
                ts = int(e.get("__REALTIME_TIMESTAMP", time.time() * 1e6)) / 1e6
                try:
                    self.feed(ts, msg or "", e.get("SYSLOG_IDENTIFIER", ""))
                except Exception:                         # one odd line must not stop the follower
                    pass
                n += 1
                if n % 20 == 0 or time.time() - ts < 5:
                    self.db.kv_set("journal.cursor", e.get("__CURSOR", ""))
            if self._stop:
                return
            time.sleep(5)

    def stop(self) -> None:
        self._stop = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
