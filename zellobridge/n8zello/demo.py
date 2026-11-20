"""Demo mode (N8ZELLO_DEMO=1): invented history and live traffic – for screenshots and development only."""
from __future__ import annotations

import math
import random
import threading
import time
from typing import List

from .db import DB

USERS = ["DL1ABC", "DO7QJK", "DK5QDO", "OE3QEF", "Anke DO1QAA", "DH6QMN", "PD1QHJ", "Funkfreund Kai", "DG2QCK", "HB9QFG"]


def seed(db: DB, days: int = 45) -> None:
    if db.q("SELECT 1 FROM talks LIMIT 1"):
        return
    rnd = random.Random(7)
    now = time.time()
    t = now - days * 86400
    talks, samples = [], []
    while t < now - 300:
        h = time.localtime(t).tm_hour
        w = 0.15 + 0.85 * max(0.0, math.sin((h - 6) / 24 * 2 * math.pi)) ** 2 + (1.2 if 19 <= h <= 22 else 0)
        for _ in range(6):
            if rnd.random() < 0.07 * w:
                d = "rf" if rnd.random() < 0.55 else "zello"
                dur = round(max(1.0, rnd.lognormvariate(2.4, 0.7)), 1)
                ts = t + rnd.uniform(0, 300)
                talks.append((ts, ts + dur, d, "" if d == "rf" else rnd.choice(USERS), dur))
        busy = min(1.0, w / 2)
        samples.append((int(t // 60) * 60, round(4 + busy * 9 + rnd.uniform(-1, 2), 1), round(45 + busy * 5 + rnd.uniform(-1, 1), 1),
                        int(360e6 + busy * 25e6), 2_090_000_000, 2000 + busy * 6000, 1800 + busy * 5500, round(0.15 + busy * 0.3, 2),
                        round(1.5 + busy * 6, 1), 33_968_000))
        t += 300
    with db.lock:
        db.conn.execute("BEGIN")
        db.conn.executemany("INSERT INTO talks (ts, end_ts, dir, user, duration) VALUES (?,?,?,?,?)", talks)
        db.conn.executemany("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?)", samples)
        db.conn.execute("COMMIT")
    ev = [(now - 3 * 86400, "warn", "disconnect", "Websocket closed!"), (now - 3 * 86400 + 6, "ok", "ready", "Zello channel is ready"),
          (now - 26 * 3600, "warn", "woodpecker", "Woodpecker protection triggered: {'error': 'channel busy'}")]
    for e in sorted(ev):
        db.x("INSERT INTO events (ts, level, kind, text) VALUES (?,?,?,?)", e)


def live(bridge, stop: threading.Event) -> None:
    """Feeds invented journal lines into the bridge parser."""
    rnd = random.Random()
    now = time.time()
    for msg in ("INFO:__main__:Initialising Zello", "INFO:USRPController:USRP RX gain: 0dB = 1.0",
                "INFO:USRPController:USRP TX gain: -3dB = 0.708", "INFO:ZelloController:Connecting to wss://zello.io/ws",
                "INFO:ZelloController:Authentication successful!", "INFO:ZelloController:Logged in!",
                "INFO:ZelloController:Channel is ready"):
        bridge.feed(now - 5 * 3600 - 1260, msg)
    while not stop.is_set():
        if stop.wait(rnd.uniform(6, 18)):
            return
        who = "USRP" if rnd.random() < 0.55 else rnd.choice(USERS)
        bridge.feed(time.time(), f"INFO:ZelloController:Keyed:{who}")
        dur = rnd.uniform(4, 25)
        if stop.wait(dur):
            return
        bridge.feed(time.time(), f"INFO:ZelloController:UnKeyed:{who} ({dur:.1f}s)")


def demo_journal() -> str:
    t0 = time.time() - 600
    out: List[str] = []
    for i, line in enumerate(["INFO:ZelloController:Keyed:USRP", "INFO:ZelloController:UnKeyed:USRP (11.4s)",
                              "INFO:ZelloController:Keyed:DL1ABC", "INFO:ZelloController:UnKeyed:DL1ABC (7.9s)",
                              "WARNING:ZelloController:Channel not ready (seq=4211)", "INFO:ZelloController:Keyed:USRP",
                              "INFO:ZelloController:UnKeyed:USRP (23.0s)"]):
        out.append(time.strftime("%Y-%m-%dT%H:%M:%S+0100", time.localtime(t0 + i * 70)) + " ZelloBridge asl-zello-bridge[315]: " + line)
    return "\n".join(out)
