"""The web server in demo mode, over real HTTP."""
import importlib
import json
import socket
import threading
import time
import urllib.request

import pytest


def port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("N8ZELLO_DATA", str(tmp_path))
    monkeypatch.setenv("N8ZELLO_DEMO", "1")
    import n8zello.app as A
    importlib.reload(A)
    app = A.make_app()
    app.auth.set_superadmin("chef", "chef-passwort-1")
    app.refresh_services()
    app.start()
    srv = A.ThreadingHTTPServer(("127.0.0.1", port()), A.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    yield f"http://127.0.0.1:{srv.server_address[1]}", app
    srv.shutdown()
    app.stop()


def call(url, method="GET", body=None, cookie=None, header=True):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if header:
        req.add_header("X-N8", "1")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


def test_public_and_admin(server):
    base, app = server
    st, info, _ = call(base + "/api/info")
    assert st == 200 and info["title"] == "N8 ZelloBridge" and info["setup_needed"] is False
    st, live, _ = call(base + "/api/live")
    assert live["config"]["channel"] == "Die N8schicht" and "env" not in live["config"]
    assert any(s["bridge"] for s in live["services"]) and live["hw"] is None or live["hw"]
    st, stats, _ = call(base + "/api/stats?range=7d")
    assert st == 200 and stats["totals"]
    assert call(base + "/api/admin/settings")[0] == 401
    assert call(base + "/api/auth/login", "POST", {"username": "chef", "password": "falsch"})[0] == 400
    assert call(base + "/api/auth/login", "POST", {"username": "chef", "password": "chef-passwort-1"}, header=False)[0] == 403
    st, d, h = call(base + "/api/auth/login", "POST", {"username": "chef", "password": "chef-passwort-1"})
    cookie = h["Set-Cookie"].split(";")[0]
    st, live, _ = call(base + "/api/live", cookie=cookie)
    assert live["config"]["env"]["ZELLO_PASSWORD"] == "••••••" and live["config"]["username"] == "n8schicht-bridge"
    st, d, _ = call(base + "/api/admin/settings", "POST", {"changes": {"dashboard.public": False}}, cookie=cookie)
    assert d["changed"] == ["dashboard.public"]
    assert call(base + "/api/live")[0] == 401
    st, d, _ = call(base + "/api/admin/service", "POST", {"unit": "asl-zello-bridge", "action": "restart"}, cookie=cookie)
    assert st == 200
    assert call(base + "/api/admin/service", "POST", {"unit": "sshd", "action": "stop"}, cookie=cookie)[0] == 400


def test_static_and_traversal(server):
    base, _ = server
    with urllib.request.urlopen(base + "/") as r:
        assert b"N8 ZelloBridge" in r.read()
    try:
        urllib.request.urlopen(base + "/static/../n8zello/app.py")
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 404
