"""SQLite: settings, users, sessions, audit, talks, events and hardware samples."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, List

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',
    display_name  TEXT NOT NULL DEFAULT '',
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    last_login    REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    ip         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    action   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    ip       TEXT NOT NULL DEFAULT ''
);
-- one transmission through the bridge: dir 'rf' = radio (USRP) -> Zello, 'zello' = Zello -> radio
CREATE TABLE IF NOT EXISTS talks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    end_ts   REAL,
    dir      TEXT NOT NULL,
    user     TEXT NOT NULL DEFAULT '',
    duration REAL
);
CREATE INDEX IF NOT EXISTS talks_ts ON talks(ts);
-- bridge events: login, channel ready, errors, restarts …
CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    kind  TEXT NOT NULL,
    text  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
-- hardware, one row per minute
CREATE TABLE IF NOT EXISTS samples (
    ts         INTEGER PRIMARY KEY,
    cpu        REAL,
    temp       REAL,
    mem_used   INTEGER,
    mem_total  INTEGER,
    rx_rate    REAL,
    tx_rate    REAL,
    load1      REAL,
    bridge_cpu REAL,
    bridge_rss INTEGER
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        for p in ("foreign_keys = ON", "journal_mode = WAL", "synchronous = NORMAL", "temp_store = MEMORY", "cache_size = -4000"):
            self.conn.execute(f"PRAGMA {p}")
        self.conn.executescript(SCHEMA)

    def q(self, sql: str, args: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(args)).fetchall()

    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, tuple(args))

    def kv_get(self, key: str, default: str = "") -> str:
        rows = self.q("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def kv_set(self, key: str, value: str) -> None:
        self.x("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
