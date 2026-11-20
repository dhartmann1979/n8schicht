import time

from n8zello.bridge import Bridge
from n8zello.db import DB
from n8zello.system import Services


def make(tmp_path):
    ev = []
    return Bridge(DB(tmp_path / "t.db"), ev.append), ev


def test_talks_both_directions(tmp_path):
    b, ev = make(tmp_path)
    t = time.time()
    for i, line in enumerate(["INFO:ZelloController:Connecting to wss://zello.io/ws", "INFO:ZelloController:Logged in!",
                              "INFO:ZelloController:Channel is ready", "INFO:ZelloController:Keyed:USRP",
                              "INFO:ZelloController:UnKeyed:USRP (12.3s)", "INFO:ZelloController:Keyed:DL1ABC Dennis",
                              "INFO:ZelloController:UnKeyed:DL1ABC Dennis (8.0s)"]):
        b.feed(t + i, line)
    assert b.zello == "ready" and b.endpoint == "wss://zello.io/ws"
    rows = [dict(r) for r in b.db.q("SELECT dir, user, duration FROM talks ORDER BY ts")]
    assert rows == [{"dir": "rf", "user": "", "duration": 12.3}, {"dir": "zello", "user": "DL1ABC Dennis", "duration": 8.0}]
    assert b.last_heard[0]["user"] == "DL1ABC Dennis"
    assert any(e["type"] == "heard" for e in ev)


def test_unkey_without_key_and_errors(tmp_path):
    b, ev = make(tmp_path)
    t = time.time()
    b.feed(t, "INFO:ZelloController:UnKeyed:OE3QEF (5.5s)")            # key-up before the dashboard started
    assert b.db.q("SELECT duration FROM talks")[0]["duration"] == 5.5
    b.feed(t + 1, "WARNING:ZelloController:Websocket closed!")
    assert b.zello == "offline" and b.counters["ws_closed"] == 1
    b.feed(t + 2, "ERROR:ZelloController:Kicked from channel: {'error': 'kicked'}")
    assert b.zello == "error" and b.last_error["text"].startswith("Kicked")
    b.feed(t + 3, "Main process exited, code=exited, status=1/FAILURE", "systemd")
    assert b.zello == "stopped"
    b.feed(t + 4, "INFO:ZelloController:Stats: start_attempts=10 start_ok=9 chn_not_ready=1 read_timeouts=0")
    assert b.stats["start_ok"] == 9


def test_short_keyups_are_dropped(tmp_path):
    b, _ = make(tmp_path)
    t = time.time()
    b.feed(t, "INFO:ZelloController:Keyed:USRP")
    b.feed(t + 0.2, "INFO:ZelloController:UnKeyed:USRP (0.2s)")
    assert b.db.q("SELECT COUNT(*) n FROM talks")[0]["n"] == 0


def test_parse_env():
    env = Services.parse_env('USRP_RXPORT=34001 "ZELLO_CHANNEL=Die N8schicht" ZELLO_PASSWORD=a\\ b')
    assert env == {"USRP_RXPORT": "34001", "ZELLO_CHANNEL": "Die N8schicht", "ZELLO_PASSWORD": "a b"}
