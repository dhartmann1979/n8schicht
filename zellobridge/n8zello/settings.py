"""Settings of the dashboard, stored in SQLite (table ``settings``)."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .db import DB


class SettingsError(Exception):
    """Message is shown to the user."""


@dataclass(frozen=True)
class Field:
    key: str
    section: str
    label: str
    kind: str                 # text | int | bool | choice
    default: Any
    help: str = ""
    choices: Sequence[Tuple[Any, str]] = ()
    min: Optional[float] = None
    max: Optional[float] = None
    pattern: str = ""


SECTIONS = [
    ("dashboard", "🖥️ Dashboard", "Title and who may see it"),
    ("bridge", "🌉 Bridge", "Which services are watched"),
    ("data", "🗄️ Data", "How long talks, events and hardware values are kept"),
]

FIELDS: List[Field] = [
    Field("dashboard.title", "dashboard", "Title", "text", "N8 ZelloBridge"),
    Field("dashboard.subtitle", "dashboard", "Subtitle", "text", "AllStarLink / SvxLink ⇄ Zello"),
    Field("dashboard.public", "dashboard", "Public view", "bool", True, "On: everybody in the network sees the dashboard without login"),
    Field("dashboard.show_users", "dashboard", "Show Zello user names publicly", "bool", True),
    Field("dashboard.session_hours", "dashboard", "Login valid for (hours)", "int", 12, min=1, max=720),
    Field("bridge.service", "bridge", "Bridge service", "text", "asl-zello-bridge", "systemd unit of the bridge",
          pattern=r"[A-Za-z0-9@._-]+"),
    Field("bridge.extra", "bridge", "Further services", "text", "auto",
          "Comma separated, e.g. svxlink, asterisk. auto = every svxlink/asterisk/usrp service found", pattern=r"[A-Za-z0-9@._, -]*"),
    Field("bridge.iface", "bridge", "Network interface", "text", "auto", "For the traffic graph; auto = the one with the default route",
          pattern=r"[A-Za-z0-9._-]+"),
    Field("data.talk_days", "data", "Keep talks (days)", "int", 400, min=7, max=3650),
    Field("data.event_days", "data", "Keep events (days)", "int", 60, min=1, max=3650),
    Field("data.sample_days", "data", "Keep hardware values (days)", "int", 31, min=1, max=400),
]
BY_KEY = {f.key: f for f in FIELDS}


def validate(f: Field, raw: Any) -> Any:
    if f.kind == "bool":
        return raw.strip().lower() in ("1", "true", "on", "yes", "ja") if isinstance(raw, str) else bool(raw)
    if f.kind == "int":
        try:
            v = int(str(raw).strip())
        except ValueError:
            raise SettingsError(f"{f.label}: please enter a whole number.")
        if f.min is not None and v < f.min or f.max is not None and v > f.max:
            raise SettingsError(f"{f.label}: allowed {int(f.min)}–{int(f.max)}.")
        return v
    v = "" if raw is None else str(raw).strip()
    if "\n" in v or len(v) > 200:
        raise SettingsError(f"{f.label}: one short line please.")
    if f.pattern and v and not re.fullmatch(f.pattern, v):
        raise SettingsError(f"{f.label}: '{v}' is not valid.")
    return v


class Settings:
    def __init__(self, db: DB):
        self.db = db
        self._cache: Optional[Dict[str, Any]] = None

    def all(self) -> Dict[str, Any]:
        if self._cache is None:
            stored = {r["key"]: r["value"] for r in self.db.q("SELECT key, value FROM settings")}
            self._cache = {f.key: (json.loads(stored[f.key]) if f.key in stored else f.default) for f in FIELDS}
        return dict(self._cache)

    def get(self, key: str) -> Any:
        return self.all()[key]

    def update(self, changes: Dict[str, Any], who: str = "") -> List[str]:
        cur = self.all()
        clean = {}
        for k, raw in changes.items():
            f = BY_KEY.get(k)
            if not f:
                raise SettingsError(f"Unknown setting: {k}")
            clean[k] = validate(f, raw)
        changed = [k for k, v in clean.items() if cur.get(k) != v]
        for k in changed:
            self.db.x("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                      "value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                      (k, json.dumps(clean[k]), time.time(), who))
        self._cache = None
        return changed

    def describe(self) -> List[Dict[str, Any]]:
        vals = self.all()
        return [{"id": sid, "title": t, "subtitle": sub,
                 "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "value": vals[f.key], "help": f.help,
                             "choices": [list(c) for c in f.choices], "min": f.min, "max": f.max}
                            for f in FIELDS if f.section == sid]} for sid, t, sub in SECTIONS]
