import httpx
import pytest

from app import db
from app.api import auth, easywall, fleet
from tests.test_api import PW, api, login  # noqa: F401  (fixture re-export)


def make_token(client, name="central", role="network_admin") -> str:
    token, digest = auth.new_token()
    with db.make_sessionmaker(client.url)() as s:
        s.add(db.ApiToken(name=name, role=role, token_hash=digest))
        s.commit()
    return token


# --- node side: service tokens ---------------------------------------------------

def test_bearer_token_no_csrf_and_acting_role(api):
    t = make_token(api)
    h = {"authorization": f"Bearer {t}"}
    assert api.get("/api/sessions", headers=h).json()["total"] == 3
    me = api.get("/api/auth/me", headers=h).json()  # how a console verifies a token
    assert me["username"] == "token:central" and me["csrf_token"] is None
    assert api.post("/api/auth/password", json={"current": "x", "new": "y" * 12}, headers=h).status_code == 403
    # bearer is not a cookie: no CSRF header needed
    assert api.post("/api/sessions/a1/disconnect", json={}, headers=h).status_code == 200
    # the acting admin's role narrows the token
    ro = {**h, "x-bng-admin": "alice", "x-bng-role": "read_only"}
    assert api.post("/api/sessions/b2/disconnect", json={}, headers=ro).status_code == 403
    noc = {**h, "x-bng-admin": "alice", "x-bng-role": "noc_operator"}
    assert api.post("/api/sessions/b2/disconnect", json={}, headers=noc).status_code == 200
    rows = [(a.admin, a.result) for a in _audit(api, "session_disconnect")]
    assert ("alice@central", "ok") in rows and ("token:central", "ok") in rows


def test_acting_role_cannot_widen_token(api):
    t = make_token(api, "weak", "read_only")
    h = {"authorization": f"Bearer {t}", "x-bng-admin": "alice", "x-bng-role": "super_admin"}
    assert api.post("/api/sessions/a1/disconnect", json={}, headers=h).status_code == 403
    assert api.get("/api/users", headers=h).status_code == 403


def test_bad_or_revoked_token(api):
    assert api.get("/api/sessions", headers={"authorization": "Bearer bngt_nope"}).status_code == 401
    t = make_token(api, "gone")
    with db.make_sessionmaker(api.url)() as s:
        s.query(db.ApiToken).filter_by(name="gone").update({"disabled": True})
        s.commit()
    assert api.get("/api/sessions", headers={"authorization": f"Bearer {t}"}).status_code == 401


def _audit(client, action):
    from sqlalchemy import select
    with db.make_sessionmaker(client.url)() as s:
        return list(s.scalars(select(db.AuditLog).where(db.AuditLog.action == action)))


# --- console side: registry, proxy, fleet --------------------------------------

@pytest.fixture
def remote(api, tmp_path, monkeypatch):
    """A registered node 'bng02' whose HTTP calls are answered by a stub."""
    monkeypatch.setenv("BNG_NODE_SECRETS", str(tmp_path / "nodes"))
    (tmp_path / "nodes").mkdir()
    (tmp_path / "nodes" / "bng02.token").write_text("bngt_x")
    (tmp_path / "nodes" / "bng02.crt").write_text("pem")
    with db.make_sessionmaker(api.url)() as s:
        s.add(db.BngNode(name="bng02", url="https://192.0.2.2:8443", fingerprint="0" * 64))
        s.commit()
    monkeypatch.setattr(fleet, "pinned_context", lambda pem: None)
    calls = []

    async def fake_request(self, method, path, p, **kw):
        calls.append((method, path, self.headers(p), kw.get("params")))
        if path == "metrics":
            return httpx.Response(200, json={"node": "bng02", "sessions": {"active": 7}}, request=httpx.Request(method, "https://x"))
        if path == "sessions":
            return httpx.Response(200, json={"items": [{"sid": "z9", "username": "alice"}]}, request=httpx.Request(method, "https://x"))
        return httpx.Response(200, json={"path": path, "method": method}, request=httpx.Request(method, "https://x"))
    monkeypatch.setattr(fleet.Remote, "request", fake_request)
    return calls


def test_nodes_and_fleet(api, remote):
    login(api, "noc_operator")
    names = [(n["name"], n["local"]) for n in api.get("/api/nodes").json()]
    assert names == [("t1", True), ("bng02", False)]
    f = api.get("/api/fleet").json()
    assert [(x["name"], x["ok"]) for x in f] == [("t1", True), ("bng02", True)]
    assert f[1]["live"]["sessions"]["active"] == 7
    items = api.get("/api/fleet/sessions", params={"search": "alice"}).json()["items"]
    assert {(i["node"], i["username"]) for i in items} == {("t1", "alice"), ("bng02", "alice")}


def test_proxy_forwards_acting_admin_and_blocks_management_paths(api, remote):
    login(api, "noc_operator")
    r = api.post("/api/nodes/bng02/sessions/z9/disconnect", json={})
    assert r.json() == {"path": "sessions/z9/disconnect", "method": "POST"}
    h = remote[-1][2]
    assert h["x-bng-admin"] == "noc_operator" and h["x-bng-role"] == "noc_operator" and h["authorization"] == "Bearer bngt_x"
    for bad in ("auth/me", "nodes", "fleet", "ws/metrics"):
        assert api.get(f"/api/nodes/bng02/{bad}").status_code == 404
    assert api.get("/api/nodes/nope/sessions").status_code == 404
    api.headers.pop("X-CSRF-Token")
    assert api.post("/api/nodes/bng02/sessions/z9/disconnect", json={}).status_code == 403  # CSRF still enforced


def test_node_management_needs_permission(api, remote):
    login(api, "noc_operator")
    assert api.delete("/api/nodes/bng02").status_code == 403
    login(api, "network_admin")
    assert api.post("/api/nodes", json={"name": "x", "url": "http://plain", "token": "bngt_" + "a" * 30,
                                        "fingerprint": "0" * 64}).status_code == 422  # https only
    assert api.delete("/api/nodes/bng02").json() == {"ok": True}
    assert [n["name"] for n in api.get("/api/nodes").json()] == ["t1"]


def test_register_node_pins_fingerprint(api, remote, monkeypatch):
    login(api, "super_admin")
    monkeypatch.setattr(fleet, "fetch_cert", lambda url: ("PEM", "a" * 64))
    body = {"name": "bng03", "url": "https://192.0.2.3:8443", "token": "bngt_" + "b" * 30, "fingerprint": "b" * 64}
    r = api.post("/api/nodes", json=body)
    assert r.status_code == 409 and "fingerprint" in r.json()["detail"]


# --- easywall proxy -------------------------------------------------------------

def test_easywall_rewrites():
    html = b'<link href="/static/style.css"><a href="//cdn.x/y">x</a><form action="/login">'
    out = easywall.rewrite_body(html, "text/html")
    assert b'href="/easywall/static/style.css"' in out and b'href="//cdn.x/y"' in out and b'action="/easywall/login"' in out
    js = b"fetch('/apply/status'); s.replace(/\"/g, '&quot;'); x = '/'"
    assert easywall.rewrite_body(js, "application/javascript") == \
        b"fetch('/easywall/apply/status'); s.replace(/\"/g, '&quot;'); x = '/'"
    assert easywall.rewrite_body(b"url(/static/f.woff2)", "text/css") == b"url(/easywall/static/f.woff2)"
    assert easywall.rewrite_location("/login") == "/easywall/login"
    assert easywall.rewrite_location("https://127.0.0.1:12227/firstrun") == "/easywall/firstrun"
    assert easywall.rewrite_cookie("sid=1; Path=/; HttpOnly") == "sid=1; Path=/easywall/; HttpOnly"
    assert easywall.rewrite_cookie("sid=1") == "sid=1; Path=/easywall/"


def test_easywall_proxy(api, monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen.update(path=req.url.path, cookie=req.headers.get("cookie"), origin=req.headers.get("origin"))
        return httpx.Response(303, headers={"location": "/firstrun", "set-cookie": "ew=1; Path=/",
                                            "x-frame-options": "DENY", "content-security-policy": "default-src 'self'"})
    monkeypatch.setattr(easywall, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                               base_url=easywall.UPSTREAM))
    assert api.get("/easywall/", follow_redirects=False).headers["location"] == "/"  # not logged in
    login(api, "noc_operator")
    assert api.get("/easywall/").status_code == 403
    login(api, "network_admin")
    r = api.get("/easywall/", follow_redirects=False, headers={"cookie": f"{auth.COOKIE}={api.cookies.get(auth.COOKIE)}; ew=1",
                                                              "origin": "https://testserver"})
    assert r.status_code == 303 and r.headers["location"] == "/easywall/firstrun"
    assert "x-frame-options" not in r.headers and r.headers["content-security-policy"].endswith("frame-ancestors 'self'")
    assert "Path=/easywall/" in r.headers["set-cookie"]
    assert seen["path"] == "/" and seen["cookie"] == "ew=1" and seen["origin"] == easywall._UP_ORIGIN


def test_proxied_websocket_ends_when_the_session_is_revoked(api, remote, monkeypatch):
    import asyncio
    from sqlalchemy import select
    from starlette.websockets import WebSocketDisconnect
    from tests.test_api import ws_headers

    class Up:  # a node socket that stays open and silent
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)

        async def send(self, m):
            pass

    monkeypatch.setattr(fleet.websockets, "connect", lambda *a, **k: Up())
    monkeypatch.setattr(fleet, "RECHECK_S", 0.05)
    login(api, "noc_operator")
    with api.websocket_connect("/api/nodes/bng02/ws/metrics", headers=ws_headers(api)) as ws:
        with db.make_sessionmaker(api.url)() as s:  # logout / disable elsewhere
            s.query(db.AuthSession).delete()
            s.commit()
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
