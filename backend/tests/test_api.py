import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app.accel.cmd import AccelError, parse_stat
from app.api import auth
from app.api.main import Node, create_app
from app.config.manager import Paths
from tests.conftest import write_cfg
from tests.test_manager import FakeDaemon

FIXTURES = Path(__file__).parent / "fixtures"
PW = "correct horse battery"


def row(sid, user, ip, mac, rate="100000/20000", state="active", rx="10", tx="990"):
    return {"sid": sid, "ifname": f"ppp{sid}", "inbound-if": "ens18.4044", "rx-pkts": "1", "tx-pkts": "2", "ip": ip, "ip6": "", "ip6-dp": "", "calling-sid": mac,
            "called-sid": "aa", "state": state, "uptime-raw": "60", "rx-bytes-raw": rx, "tx-bytes-raw": tx,
            "rate-limit": rate, "username": user}


class FakeAccel:
    def __init__(self):
        self.rows = [row("a1", "alice", "100.64.0.5", "m1"), row("b2", "bob", "100.64.0.9", "m2"),
                     row("c3", "alice", "100.64.0.7", "m3", rate="")]
        self.terminated = []

    def sessions(self, match=None):
        return [dict(r) for r in self.rows]

    def terminate(self, sid, hard=False):
        if sid == "zz":
            raise AccelError("not found")
        self.terminated.append((sid, hard))

    def version(self):
        return "accel-ppp version 1.14.0"

    def stat_dict(self):
        return parse_stat((FIXTURES / "show_stat_1.14.0.txt").read_text())

    def pppoe_interfaces(self):
        return "veth0: ..."


@pytest.fixture
def api(tmp_path, base_cfg):
    paths = Paths(tmp_path / "etc", tmp_path / "state")
    paths.etc.mkdir(parents=True)
    write_cfg(paths.config, base_cfg)
    url = f"sqlite:///{tmp_path / 'mgmt.db'}"
    db.upgrade(url)  # the Alembic migration itself is under test
    with db.make_sessionmaker(url)() as s:
        for role in auth.ROLES:
            s.add(db.Admin(username=role, role=role, password_hash=auth.hash_password(PW)))
        s.commit()
    node = Node(paths, FakeAccel(), FakeDaemon())
    node.mgr.health = lambda cfg: []
    client = TestClient(create_app(node, url), base_url="https://testserver")
    client.node, client.url = node, url
    return client


def login(client, user):
    r = client.post("/api/auth/login", json={"username": user, "password": PW},
                    headers={"X-Requested-With": "bng"})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()


def audit_rows(client, action):
    with db.make_sessionmaker(client.url)() as s:
        return [(a.admin, a.result) for a in s.scalars(select(db.AuditLog).where(db.AuditLog.action == action))]


def test_password_hash_roundtrip():
    h = auth.hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert auth.verify_password(PW, h) and not auth.verify_password(PW + "x", h)
    assert not auth.verify_password(PW, "garbage")


def test_parse_stat_fixture():
    s = parse_stat((FIXTURES / "show_stat_1.14.0.txt").read_text())
    assert s["uptime"] == "0.01:36:16"
    assert s["sessions"] == {"starting": "0", "active": "0", "finishing": "0"}
    assert s["pppoe"]["recv PADR(dup)"] == "14(0)"


def test_login_needs_custom_header_and_is_audited(api):
    r = api.post("/api/auth/login", json={"username": "read_only", "password": PW})
    assert r.status_code == 403
    assert api.post("/api/auth/login", json={"username": "read_only", "password": "nope"},
                    headers={"X-Requested-With": "bng"}).status_code == 401
    me = login(api, "read_only")
    assert me["permissions"] == ["view_sessions"]
    assert ("read_only", "failed") in audit_rows(api, "login") and ("read_only", "ok") in audit_rows(api, "login")
    cookie = api.cookies.get(auth.COOKIE)
    with db.make_sessionmaker(api.url)() as s:  # only the hash is stored
        assert not s.scalar(select(db.AuthSession).where(db.AuthSession.token_hash == cookie))


def test_bruteforce_is_throttled(api):
    for _ in range(auth.MAX_FAILS_PER_USER):
        api.post("/api/auth/login", json={"username": "read_only", "password": "nope"},
                 headers={"X-Requested-With": "bng"})
    r = api.post("/api/auth/login", json={"username": "read_only", "password": PW},
                 headers={"X-Requested-With": "bng"})
    assert r.status_code == 429


def test_unauthenticated_and_csrf(api):
    assert api.get("/api/sessions").status_code == 401
    login(api, "noc_operator")
    token = api.headers.pop("X-CSRF-Token")
    assert api.post("/api/sessions/a1/disconnect", json={}).status_code == 403
    api.headers["X-CSRF-Token"] = token
    assert api.post("/api/sessions/a1/disconnect", json={}).status_code == 200


def test_sessions_list_direction_duplicates_paging(api):
    login(api, "read_only")
    r = api.get("/api/sessions", params={"page_size": 2}).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    a1 = api.get("/api/sessions/a1").json()
    assert (a1["upload_bytes"], a1["download_bytes"]) == (10, 990)  # rx on pppN = from subscriber
    assert (a1["rate_down_kbit"], a1["rate_up_kbit"]) == (100000, 20000)
    assert a1["duplicate"] is True  # alice twice
    assert api.get("/api/sessions", params={"search": "bob"}).json()["total"] == 1
    assert api.get("/api/sessions", params={"search": "a;b"}).status_code == 422
    assert api.get("/api/sessions/x y").status_code in (404, 422)
    ips = [i["ip"] for i in api.get("/api/sessions", params={"sort": "ip"}).json()["items"]]
    assert ips == ["100.64.0.5", "100.64.0.7", "100.64.0.9"]


def test_disconnect_rbac_and_audit(api):
    login(api, "read_only")
    assert api.post("/api/sessions/a1/disconnect", json={}).status_code == 403
    login(api, "noc_operator")
    assert api.post("/api/sessions/a1/disconnect", json={"hard": True}).json() == {"sid": "a1", "mode": "hard"}
    assert api.node.accel.terminated == [("a1", True)]
    assert api.post("/api/sessions/zz/disconnect", json={}).status_code == 502
    assert ("noc_operator", "ok") in audit_rows(api, "session_disconnect")
    node_audit = [json.loads(x) for x in api.node.paths.audit_log.read_text().splitlines()]
    assert node_audit[-1]["source"] == "api:testclient" and node_audit[-1]["admin"] == "noc_operator"


def test_config_apply_permissions_and_versioning(api, base_cfg):
    login(api, "noc_operator")
    cfg = api.get("/api/config").json()["config"]
    assert api.post("/api/config/apply", json={"config": cfg}).status_code == 403
    login(api, "network_admin")
    assert api.post("/api/config/apply", json={"config": cfg}).json()["result"] == "no changes"
    bad = dict(cfg, uplink="eth0; rm -rf /")
    assert api.post("/api/config/apply", json={"config": bad}).status_code == 422
    v = api.post("/api/config/validate", json={"config": dict(cfg, dns=["1.1.1.1"])}).json()
    assert v["changed"] == ["dns"] and v["required_permissions"] == ["apply_config", "change_network"]
    r = api.post("/api/config/apply", json={"config": dict(cfg, dns=["1.1.1.1"])})
    assert r.status_code == 200 and r.json()["result"] == "applied as version 1", r.text
    r = api.put("/api/qos/config", json={"shaper": {"attr": "Filter-Id", "max_rate_mbit": 1000}})
    assert r.json()["changed"] == ["shaper"]
    assert [m["source"] for m in api.get("/api/config/history").json()] == ["api:testclient"] * 2
    assert "max_rate_mbit: 1000" in api.get("/api/config/versions/2/diff").text
    assert api.post("/api/config/rollback", json={}).json()["result"] == "applied as version 3"


def test_needed_permissions():
    from app.api.main import _needed
    assert _needed({"nat", "shaper"}) == {"apply_config", "change_nat", "change_qos"}
    assert _needed({"aaa", "radius"}) == {"apply_config", "change_radius"}


def test_users_admin(api):
    login(api, "network_admin")
    assert api.get("/api/users").status_code == 403
    login(api, "super_admin")
    new = {"username": "noc2", "password": "short", "role": "noc_operator"}
    assert api.post("/api/users", json=new).status_code == 422
    assert api.post("/api/users", json={**new, "password": PW}).status_code == 201
    assert api.post("/api/users", json={**new, "role": "root"}).status_code == 422
    assert api.patch("/api/users/super_admin", json={"role": "read_only"}).status_code == 409
    assert api.patch("/api/users/noc2", json={"disabled": True}).json()["disabled"] is True
    r = api.post("/api/auth/login", json={"username": "noc2", "password": PW}, headers={"X-Requested-With": "bng"})
    assert r.status_code == 401


def test_logout_and_me_rotates_csrf(api):
    old = login(api, "read_only")["csrf_token"]
    me = api.get("/api/auth/me").json()
    assert me["csrf_token"] != old
    api.headers["X-CSRF-Token"] = me["csrf_token"]
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/sessions").status_code == 401


def test_database_down_is_503(tmp_path, base_cfg):
    paths = Paths(tmp_path / "etc", tmp_path / "state")
    paths.etc.mkdir(parents=True)
    write_cfg(paths.config, base_cfg)
    app = create_app(Node(paths, FakeAccel(), FakeDaemon()), f"sqlite:///{tmp_path / 'missing' / 'x.db'}")
    c = TestClient(app, base_url="https://testserver")
    r = c.post("/api/auth/login", json={"username": "a", "password": "b"}, headers={"X-Requested-With": "bng"})
    assert r.status_code == 503


def test_no_generic_execution_endpoint(api):
    paths = [r.path for r in api.app.routes]
    assert not [p for p in paths if any(w in p for w in ("exec", "shell", "command", "run"))]


def test_session_rates_and_churn():
    from app.monitoring.sampler import SessionRates
    sr = SessionRates()
    rows = [{"sid": "a", "upload_bytes": 0, "download_bytes": 0}]
    sr.update(rows, t=100.0)
    assert rows[0]["download_mbps"] is None  # first read is a baseline
    rows = [{"sid": "a", "upload_bytes": 125_000, "download_bytes": 1_250_000}, {"sid": "b", "upload_bytes": 0, "download_bytes": 0}]
    sr.update(rows, t=101.0)
    assert (rows[0]["download_mbps"], rows[0]["upload_mbps"]) == (10.0, 1.0)
    assert rows[0]["peak_download_mbps"] == 10.0
    sr.update([{"sid": "b", "upload_bytes": 0, "download_bytes": 0}], t=102.0)
    assert sr.per_minute() == {"logins_per_min": 1, "logouts_per_min": 1}


def test_vlan_from_inbound_interface():
    from app.api.main import _vlan
    assert (_vlan("ens18.4044"), _vlan("bnglab0"), _vlan("")) == (4044, None, None)


def test_config_yaml_keeps_comments(api):
    login(api, "network_admin")
    text = api.get("/api/config").json()["yaml"] + "dns: [1.1.1.1]   # resolver for subscribers\n"
    r = api.post("/api/config/apply", json={"yaml": text})
    assert r.status_code == 200, r.text
    assert "# resolver for subscribers" in api.node.paths.config.read_text()
    commented = text + "# only a comment\n"
    assert api.post("/api/config/apply", json={"yaml": commented}).json()["result"] == "applied as version 2"
    assert api.post("/api/config/apply", json={"yaml": commented}).json()["result"] == "no changes"
    assert api.post("/api/config/apply", json={"yaml": "- not a mapping"}).status_code == 422
    assert api.post("/api/config/apply", json={}).status_code == 422


def ws_headers(client, origin="https://testserver"):
    # TestClient always connects ws:// (httpx then withholds the Secure cookie)
    return {"origin": origin, "cookie": f"{auth.COOKIE}={client.cookies.get(auth.COOKIE)}"}


def test_ws_requires_login_and_same_origin(api):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/api/ws/metrics", headers={"origin": "https://testserver"}) as ws:
            ws.receive_json()
    login(api, "read_only")
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/api/ws/metrics", headers=ws_headers(api, "https://evil.example")) as ws:
            ws.receive_json()
    with api.websocket_connect("/api/ws/metrics", headers=ws_headers(api)) as ws:
        snap = ws.receive_json()
    assert snap["sessions"]["total"] == 3 and snap["sessions"]["duplicates"] == 2


def test_ws_sessions_incremental(api):
    login(api, "read_only")
    with api.websocket_connect("/api/ws/sessions", headers=ws_headers(api)) as ws:
        ws.send_json({"sids": ["a1", "nope", "bad sid"]})
        msgs = [ws.receive_json() for _ in range(3)]
    ups = [u for m in msgs for u in m["updates"]]
    assert {u["sid"] for u in ups} <= {"a1", "nope"}
    assert any(u["sid"] == "a1" and u["state"] == "active" for u in ups)
    assert any(u.get("gone") for u in ups if u["sid"] == "nope")


def test_spa_fallback(api, tmp_path, monkeypatch):
    from app.api import main
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>app</html>")
    (web / "assets" / "x.js").write_text("js")
    monkeypatch.setattr(main, "WEB_DIR", web)
    assert api.get("/sessions/abc").text == "<html>app</html>"
    assert "frame-ancestors 'none'" in api.get("/").headers["content-security-policy"]
    assert api.get("/assets/x.js").text == "js"
    assert api.get("/api/nope").status_code == 404
    assert "app" in api.get("/..%2F..%2Fetc%2Fpasswd").text
