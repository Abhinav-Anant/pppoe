import hashlib
import struct

from app.bench import loadgen as lg
from tests.test_api import api  # noqa: F401  (fixture)

AC = b"\x02\xac\x00\x00\x00\x01"


class FakeAC:
    """Answers like accel-ppp: PADO/PADS, LCP (asks for CHAP-MD5 or PAP), auth, IPCP Nak then Ack."""

    def __init__(self, auth=lg.CHAP, password=b"pw"):
        self.auth, self.pw, self.out, self.gen, self.next_sid = auth, password, [], None, 1
        self.challenge, self.inbox = {}, []

    def reply(self, dst, etype, code, sid, payload):
        self.inbox.append(dst + AC + struct.pack("!HBBHH", etype, 0x11, code, sid, len(payload)) + payload)

    def pump(self):  # deliver after send() returns, like a real wire
        while self.inbox:
            self.gen.handle(self.inbox.pop(0), 0.0)

    def ppp(self, dst, sid, proto, body):
        self.reply(dst, lg.SESS, 0, sid, struct.pack("!H", proto) + body)

    def __call__(self, frame):
        self.out.append(frame)
        src, (etype, _, code, sid, ln) = frame[6:12], struct.unpack_from("!HBBHH", frame, 12)
        p = frame[20:20 + ln]
        if etype == lg.DISC and code == lg.PADI:
            self.reply(src, lg.DISC, lg.PADO, 0, lg.tag(0x0104, b"cookie") + lg.tag(0x0103, lg.tags(p)[0x0103]))
        elif etype == lg.DISC and code == lg.PADR:
            assert lg.tags(p)[0x0104] == b"cookie"
            sid, self.next_sid = self.next_sid, self.next_sid + 1
            self.reply(src, lg.DISC, lg.PADS, sid, b"")
            auth = struct.pack("!H", self.auth) + (b"\x05" if self.auth == lg.CHAP else b"")
            self.ppp(src, sid, lg.LCP, lg.cp(lg.CONF_REQ, 1, lg.opt(3, auth) + lg.opt(5, b"MAGI")))
        elif etype == lg.SESS:
            proto = struct.unpack_from("!H", p)[0]
            code, ident = p[2], p[3]
            body = p[6:]
            if proto == lg.LCP and code == lg.CONF_REQ:
                self.ppp(src, sid, lg.LCP, lg.cp(lg.CONF_ACK, ident, body))
            elif proto == lg.LCP and code == lg.CONF_ACK and self.auth == lg.CHAP:
                self.challenge[sid] = b"0123456789abcdef"
                self.ppp(src, sid, lg.CHAP, lg.cp(1, 7, bytes([16]) + self.challenge[sid] + b"bng"))
            elif proto == lg.CHAP and code == 2:
                ok = body[1:17] == hashlib.md5(bytes([ident]) + self.pw + self.challenge[sid]).digest()
                self.ppp(src, sid, lg.CHAP, lg.cp(3 if ok else 4, ident, b""))
                if ok:
                    self.ppp(src, sid, lg.IPCP, lg.cp(lg.CONF_REQ, 1, lg.opt(3, bytes([100, 72, 255, 254]))))
            elif proto == lg.PAP and code == 1:
                n = body[0]
                ok = body[2 + n:] == self.pw
                self.ppp(src, sid, lg.PAP, lg.cp(2 if ok else 3, ident, b""))
                if ok:
                    self.ppp(src, sid, lg.IPCP, lg.cp(lg.CONF_REQ, 1, lg.opt(3, bytes([100, 72, 255, 254]))))
            elif proto == lg.IPCP and code == lg.CONF_REQ:
                ip = lg.opts(body)[0][1]
                if ip == b"\0\0\0\0":
                    self.ppp(src, sid, lg.IPCP, lg.cp(lg.CONF_NAK, ident, lg.opt(3, bytes([100, 72, 0, sid]))))
                else:
                    self.ppp(src, sid, lg.IPCP, lg.cp(lg.CONF_ACK, ident, body))


def run(auth, pw=b"pw", n=5):
    ac = FakeAC(auth)
    g = lg.LoadGen(ac, n, "u", pw.decode(), rate=1000, window=n)
    ac.gen = g
    for t in (0.0, 1.0):
        g.tick(t)
        ac.pump()
    return g, ac


def test_sessions_come_up_chap_and_pap():
    for auth in (lg.CHAP, lg.PAP):
        g, _ = run(auth)
        assert (g.up, g.failed, g.done()) == (5, 0, True)
        assert sorted(s.ip[3] for s in g.by_mac.values()) == [1, 2, 3, 4, 5]


def test_wrong_password_fails_and_padt_on_terminate():
    g, _ = run(lg.CHAP, pw=b"bad", n=2)
    assert (g.up, g.failed, g.reasons) == (0, 2, {"auth-reject": 2})
    g, ac = run(lg.PAP, n=3)
    ac.out.clear()
    g.terminate()
    assert [struct.unpack_from("!HBBH", f, 12)[2] for f in ac.out] == [lg.PADT] * 3


def test_echo_reply_and_server_terminate():
    g, ac = run(lg.PAP, n=1)
    s = next(iter(g.by_mac.values()))
    ac.out.clear()
    ac.ppp(s.mac, s.sid, lg.LCP, lg.cp(lg.ECHO_REQ, 9, b"MAGIdata"))
    ac.pump()
    rep = ac.out[-1][22:]
    assert rep[0] == lg.ECHO_REP and rep[1] == 9 and rep[4:8] == s.magic and rep[8:] == b"data"
    ac.ppp(s.mac, s.sid, lg.LCP, lg.cp(lg.TERM_REQ, 3, b""))
    ac.pump()
    assert (g.up, g.dropped) == (0, 1)


def test_retransmit_then_timeout():
    sent = []
    g = lg.LoadGen(sent.append, 1, "u", "pw", rate=10, window=1)
    for t in range(0, 20, 3):
        g.tick(float(t) + 0.01)
    assert g.failed == 1 and g.reasons == {"timeout-padi": 1} and len(sent) == lg.MAX_TRIES


def test_verdict_thresholds_and_summary(tmp_path):
    import json

    from app.bench import runner
    s = {"target": 100, "established": 100, "dropped_during_hold": 0, "setup_rate_per_s": 50, "mem_used_mb": 9,
         "setup_latency_ms": {"p95": 4.0}, "cpu_hold": {}}
    ok = {"gbps": 0.99, "loss_percent": 0.1, "pps": 1, "host": {}}
    t = {"target_gbps": 1, "down": ok, "up": ok}
    assert runner.verdict(s, t) == ("PASS", [])
    res, why = runner.verdict({**s, "established": 99}, {**t, "up": {**ok, "gbps": 0.9}})
    assert res == "FAIL" and len(why) == 2
    assert runner.verdict(s, {**t, "down": {**ok, "loss_percent": None}})[0] == "FAIL"  # unmeasured is not a pass
    (tmp_path / "benchmark_100_1g.json").write_text(json.dumps({"name": "benchmark_100_1g", "sessions": s,
                                                                "traffic": t, "result": "PASS"}))
    assert "| [benchmark_100_1g](benchmark_100_1g.json) | 100/100 | 1 | 0.99 / 0.99 |" in \
        runner.write_summary(tmp_path).read_text()


def test_benchmark_and_tuning_api(api):
    import json

    from tests.test_api import login
    d = api.node.paths.state / "benchmark-results"
    d.mkdir(parents=True)
    doc = {"name": "benchmark_100_1g", "result": "FAIL", "fail_reasons": ["up: 0.5 of 1 Gbit/s delivered"],
           "sessions": {"target": 100, "established": 100},
           "traffic": {"target_gbps": 1, "down": {"gbps": 1.0, "loss_percent": 0}, "up": {"gbps": 0.5}}}
    (d / "benchmark_100_1g.json").write_text(json.dumps(doc))
    assert api.get("/api/benchmarks").status_code == 401
    login(api, "read_only")
    rows = api.get("/api/benchmarks").json()
    assert rows[0]["name"] == "benchmark_100_1g" and rows[0]["up_gbps"] == 0.5 and rows[0]["result"] == "FAIL"
    assert api.get("/api/benchmarks/benchmark_100_1g").json()["traffic"]["target_gbps"] == 1
    assert api.get("/api/benchmarks/..%2Fsecrets").status_code in (404, 422)
    assert "recommendations" in api.get("/api/system/tuning").json()


def _bpf_run(prog, pkt: bytes) -> int:
    """Tiny interpreter for the opcodes the loadgen filter uses."""
    pc = a = 0
    while True:
        code, jt, jf, k = prog[pc]
        if code == 0x28:
            a = int.from_bytes(pkt[k:k + 2], "big")
        elif code == 0x30:
            a = pkt[k]
        elif code == 0x15:
            pc += jt if a == k else jf
        elif code == 0x06:
            return k
        pc += 1


def test_bpf_filter_keeps_only_generated_pppoe():
    ours, other = lg.mac(7), b"\xfa\xfe\xe9\x90\xb7\x7f"
    frame = lambda dst, et: dst + AC + struct.pack("!H", et) + b"\x11\x00" + b"\0" * 20
    assert _bpf_run(lg.BPF, frame(ours, lg.DISC)) and _bpf_run(lg.BPF, frame(ours, lg.SESS))
    assert not _bpf_run(lg.BPF, frame(other, lg.SESS))  # pppd traffic sessions
    assert not _bpf_run(lg.BPF, frame(ours, 0x0800))
    assert not _bpf_run(lg.BPF, frame(b"\xff" * 6, lg.DISC))  # our own PADI broadcasts
