"""Userspace PPPoE subscriber generator for session-scale benchmarks.

Speaks real PPPoE discovery, LCP, PAP/CHAP-MD5 and IPCP on the wire from one
process, one locally administered MAC per session, so the BNG builds a real
kernel PPPoE session + ppp interface + shaper for every subscriber while the
client side keeps no kernel state (pppd costs ~1.25 MB per session; 30k would not
fit in RAM). Carries no data traffic: traffic tests use real pppd sessions.

Run inside the access namespace:
  ip netns exec bnglab python -m app.bench.loadgen --iface bnglab1 --sessions 1000 \
      --user benchuser --password-file F
Prints one JSON progress line per second; on SIGTERM/SIGINT sends PADT for every
session and prints a final JSON line with per-session setup latencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time

DISC, SESS = 0x8863, 0x8864
PADI, PADO, PADR, PADS, PADT = 0x09, 0x07, 0x19, 0x65, 0xA7
LCP, PAP, CHAP, IPCP = 0xC021, 0xC023, 0xC223, 0x8021
CONF_REQ, CONF_ACK, CONF_NAK, CONF_REJ, TERM_REQ, TERM_ACK, PROTO_REJ, ECHO_REQ, ECHO_REP = 1, 2, 3, 4, 5, 6, 8, 9, 10
BCAST = b"\xff" * 6
RETRY_S, MAX_TRIES = 3.0, 5


MAC_PREFIX = b"\x02\xb0"  # locally administered; one MAC per generated subscriber
ETH_P_ALL, SO_ATTACH_FILTER = 3, 26

# classic BPF: accept PPPoE discovery/session frames addressed to a generated MAC, drop the rest
BPF = [
    (0x28, 0, 0, 12),              # ldh [12]            ethertype
    (0x15, 1, 0, DISC),            # jeq 0x8863 -> 3
    (0x15, 0, 4, SESS),            # jeq 0x8864 -> 3 else drop
    (0x30, 0, 0, 0),               # ldb [0]             dst MAC byte 0
    (0x15, 0, 2, MAC_PREFIX[0]),   # jeq -> 5 else drop
    (0x30, 0, 0, 1),               # ldb [1]
    (0x15, 1, 0, MAC_PREFIX[1]),   # jeq -> accept else drop
    (0x06, 0, 0, 0),               # ret 0      drop
    (0x06, 0, 0, 0xFFFF),          # ret 65535  accept
]


class _Filter:
    def __init__(self, prog):
        import ctypes
        self.buf = ctypes.create_string_buffer(b"".join(struct.pack("HBBI", *i) for i in prog))  # kept alive
        self.fprog = struct.pack("HP", len(prog), ctypes.addressof(self.buf))


FILTER = _Filter(BPF)


def mac(i: int) -> bytes:
    return MAC_PREFIX + struct.pack("!I", i)


def tags(data: bytes) -> dict[int, bytes]:
    out, i = {}, 0
    while i + 4 <= len(data):
        t, n = struct.unpack_from("!HH", data, i)
        out.setdefault(t, data[i + 4:i + 4 + n])
        i += 4 + n
    return out


def tag(t: int, v: bytes) -> bytes:
    return struct.pack("!HH", t, len(v)) + v


def opts(data: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i + 2 <= len(data):
        t, n = data[i], data[i + 1]
        if n < 2:
            break
        out.append((t, data[i + 2:i + n]))
        i += n
    return out


def opt(t: int, v: bytes) -> bytes:
    return bytes([t, len(v) + 2]) + v


def cp(code: int, ident: int, body: bytes) -> bytes:
    return struct.pack("!BBH", code, ident, len(body) + 4) + body


class Session:
    __slots__ = ("i", "mac", "state", "ac", "sid", "cookie", "relay", "t0", "t_up", "last", "tries", "magic",
                 "lcp_ours", "lcp_theirs", "ipcp_ours", "ipcp_theirs", "ip", "ident", "auth", "fail")

    def __init__(self, i: int, now: float):
        self.i, self.mac, self.state = i, mac(i), "padi"
        self.ac = self.cookie = self.relay = None
        self.sid, self.t0, self.t_up, self.last, self.tries = 0, now, None, now, 0
        self.magic = os.urandom(4)
        self.lcp_ours = self.lcp_theirs = self.ipcp_ours = self.ipcp_theirs = False
        self.ip, self.ident, self.auth, self.fail = b"\0\0\0\0", 0, PAP, None


class LoadGen:
    """State machine for many sessions; `send(frame)` is injected so tests can drive it."""

    def __init__(self, send, n: int, user: str, password: str, rate: float = 200.0, window: int = 500, mru: int = 1492):
        self.send, self.n, self.user, self.pw = send, n, user.encode(), password.encode()
        self.rate, self.window, self.mru = rate, window, mru
        self.by_mac: dict[bytes, Session] = {}
        self.by_sid: dict[int, Session] = {}
        self.pending: set[Session] = set()  # in setup; only these need timers
        self.started = 0
        self.t_start: float | None = None
        self.up = self.failed = self.dropped = 0
        self.reasons: dict[str, int] = {}
        self.retx: dict[str, int] = {}  # retransmissions by state: where the BNG (or the wire) lost packets

    # --- frames -----------------------------------------------------------
    def disc(self, s: Session, dst: bytes, code: int, payload: bytes, sid: int = 0) -> None:
        self.send(dst + s.mac + struct.pack("!HBBHH", DISC, 0x11, code, sid, len(payload)) + payload)

    def ppp(self, s: Session, proto: int, body: bytes) -> None:
        data = struct.pack("!H", proto) + body
        self.send(s.ac + s.mac + struct.pack("!HBBHH", SESS, 0x11, 0, s.sid, len(data)) + data)

    def _uniq(self, s: Session) -> bytes:
        return tag(0x0103, struct.pack("!I", s.i))

    # --- per-state (re)transmit ------------------------------------------
    def kick(self, s: Session, now: float) -> None:
        s.last = now
        s.ident = (s.ident + 1) & 0xFF
        if s.state == "padi":
            self.disc(s, BCAST, PADI, tag(0x0101, b"") + self._uniq(s))
        elif s.state == "padr":
            p = tag(0x0101, b"") + self._uniq(s) + (tag(0x0104, s.cookie) if s.cookie is not None else b"")
            p += tag(0x0110, s.relay) if s.relay is not None else b""
            self.disc(s, s.ac, PADR, p)
        elif s.state == "lcp":
            if not s.lcp_ours:
                self.ppp(s, LCP, cp(CONF_REQ, s.ident, opt(1, struct.pack("!H", self.mru)) + opt(5, s.magic)))
        elif s.state == "auth" and s.auth == PAP:
            self.ppp(s, PAP, cp(1, s.ident, bytes([len(self.user)]) + self.user + bytes([len(self.pw)]) + self.pw))
        elif s.state == "ipcp" and not s.ipcp_ours:
            self.ppp(s, IPCP, cp(CONF_REQ, s.ident, opt(3, s.ip)))

    def advance(self, s: Session, state: str, now: float) -> None:
        s.state, s.tries = state, 0
        self.kick(s, now)

    def fail(self, s: Session, why: str) -> None:
        if s.state in ("up", "failed"):
            return
        s.state, s.fail = "failed", why
        self.pending.discard(s)
        self.failed += 1
        self.reasons[why] = self.reasons.get(why, 0) + 1
        if s.sid:
            self.disc(s, s.ac, PADT, b"", s.sid)
            self.by_sid.pop(s.sid, None)

    # --- receive ----------------------------------------------------------
    def handle(self, frame: bytes, now: float) -> None:
        if len(frame) < 20:
            return
        dst, src, (etype, vt, code, sid, ln) = frame[:6], frame[6:12], struct.unpack_from("!HBBHH", frame, 12)
        payload = frame[20:20 + ln]
        if etype == DISC:
            s = self.by_mac.get(dst)
            if not s:
                return
            t = tags(payload)
            if code == PADO and s.state == "padi":
                if any(k in t for k in (0x0201, 0x0202, 0x0203)):
                    return self.fail(s, "pado-error")
                s.ac, s.cookie, s.relay = src, t.get(0x0104), t.get(0x0110)
                self.advance(s, "padr", now)
            elif code == PADS and s.state == "padr":
                if not sid or any(k in t for k in (0x0201, 0x0202, 0x0203)):
                    return self.fail(s, "pads-error")
                s.sid = sid
                self.by_sid[sid] = s
                self.advance(s, "lcp", now)
            elif code == PADT and s.sid == sid:
                self._down(s, "padt")
        elif etype == SESS and code == 0 and len(payload) >= 2:
            s = self.by_sid.get(sid)
            if s and s.mac == dst:
                self._ppp(s, struct.unpack_from("!H", payload)[0], payload[2:], now)

    def _down(self, s: Session, why: str) -> None:
        self.by_sid.pop(s.sid, None)
        if s.state == "up":
            s.state = "down"
            self.up -= 1
            self.dropped += 1
            self.reasons["down-" + why] = self.reasons.get("down-" + why, 0) + 1
        else:
            self.fail(s, why)

    def _ppp(self, s: Session, proto: int, pkt: bytes, now: float) -> None:
        if len(pkt) < 4:
            return
        code, ident, ln = struct.unpack_from("!BBH", pkt)
        body = pkt[4:ln]
        if proto == LCP:
            if code == CONF_REQ:
                o = opts(body)
                bad = [(t, v) for t, v in o if t == 3 and v not in (b"\xc0\x23", b"\xc2\x23\x05")]
                if bad:  # MS-CHAP etc.: ask for PAP
                    return self.ppp(s, LCP, cp(CONF_NAK, ident, opt(3, struct.pack("!H", PAP))))
                for t, v in o:
                    if t == 3:
                        s.auth = struct.unpack("!H", v[:2])[0]
                self.ppp(s, LCP, cp(CONF_ACK, ident, body))
                s.lcp_theirs = True
            elif code == CONF_ACK:
                s.lcp_ours = True
            elif code in (CONF_NAK, CONF_REJ):
                s.tries = 0
                self.kick(s, now)
            elif code == ECHO_REQ:
                self.ppp(s, LCP, cp(ECHO_REP, ident, s.magic + body[4:]))
            elif code == TERM_REQ:
                self.ppp(s, LCP, cp(TERM_ACK, ident, b""))
                return self._down(s, "lcp-terminate")
            if s.state == "lcp" and s.lcp_ours and s.lcp_theirs:
                self.advance(s, "auth", now)
        elif proto == PAP and s.state == "auth":
            if code == 2:
                self.advance(s, "ipcp", now)
            elif code == 3:
                self.fail(s, "auth-reject")
        elif proto == CHAP and s.state == "auth":
            if code == 1 and body:
                n = body[0]
                h = hashlib.md5(bytes([ident]) + self.pw + body[1:1 + n]).digest()
                self.ppp(s, CHAP, cp(2, ident, bytes([len(h)]) + h + self.user))
            elif code == 3:
                self.advance(s, "ipcp", now)
            elif code == 4:
                self.fail(s, "auth-reject")
        elif proto == IPCP and s.state in ("auth", "ipcp"):
            if code == CONF_REQ:
                self.ppp(s, IPCP, cp(CONF_ACK, ident, body))
                s.ipcp_theirs = True
            elif code == CONF_NAK:
                for t, v in opts(body):
                    if t == 3:
                        s.ip = v
                self.kick(s, now)
            elif code == CONF_ACK:
                s.ipcp_ours = True
            elif code == CONF_REJ:
                return self.fail(s, "ipcp-reject")
            if s.state == "ipcp" and s.ipcp_ours and s.ipcp_theirs:
                s.state, s.t_up = "up", now
                self.pending.discard(s)
                self.up += 1
        elif proto not in (PAP, CHAP, IPCP):  # IPV6CP, CCP...: not supported here
            self.ppp(s, LCP, cp(PROTO_REJ, s.ident, struct.pack("!H", proto) + pkt))

    # --- clock ------------------------------------------------------------
    def tick(self, now: float) -> None:
        if self.t_start is None:
            self.t_start = now
        due = min(self.n, int((now - self.t_start) * self.rate) + 1)
        while self.started < due and len(self.pending) < self.window:
            s = Session(self.started, now)
            self.by_mac[s.mac] = s
            self.pending.add(s)
            self.started += 1
            self.kick(s, now)
        for s in list(self.pending):
            if now - s.last < RETRY_S:
                continue
            s.tries += 1
            self.retx[s.state] = self.retx.get(s.state, 0) + 1
            if s.tries >= MAX_TRIES:
                self.fail(s, "timeout-" + s.state)
            else:
                self.kick(s, now)

    def done(self) -> bool:
        return self.started == self.n and not self.pending

    def terminate(self) -> None:
        for s in self.by_mac.values():
            if s.sid and s.state not in ("failed", "down"):
                self.disc(s, s.ac, PADT, b"", s.sid)

    def progress(self, now: float) -> dict:
        return {"t": round(now - (self.t_start or now), 2), "started": self.started, "up": self.up,
                "failed": self.failed, "dropped": self.dropped, "reasons": self.reasons, "retransmits": self.retx}

    def latencies(self) -> list[float]:
        return sorted(round(s.t_up - s.t0, 4) for s in self.by_mac.values() if s.t_up is not None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True)
    ap.add_argument("--sessions", type=int, required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--rate", type=float, default=200.0, help="new sessions per second")
    ap.add_argument("--window", type=int, default=500, help="max sessions in setup at once")
    a = ap.parse_args()

    # One socket for discovery and session frames: two sockets would let an LCP frame be read
    # before the PADS that announced its session id (it was then dropped and the session waited
    # for the BNG's 3 s LCP restart). The BPF filter keeps other traffic (pppd data) in the kernel.
    so = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    so.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, FILTER.fprog)
    so.setsockopt(socket.SOL_SOCKET, 33, 16 << 20)  # SO_RCVBUFFORCE
    so.setsockopt(socket.SOL_SOCKET, 32, 16 << 20)  # SO_SNDBUFFORCE
    so.bind((a.iface, ETH_P_ALL))
    so.setblocking(False)
    subprocess.run(["ip", "link", "set", a.iface, "promisc", "on"], check=True)  # real NICs filter unicast MACs
    out, socks = so, [so]

    def send(frame: bytes) -> None:
        try:
            out.send(frame)
        except BlockingIOError:
            select.select([], [out], [], 0.05)
            out.send(frame)

    g = LoadGen(send, a.sessions, a.user, open(a.password_file).read().strip(), a.rate, a.window)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    next_report = next_tick = time.monotonic()
    while not stop:
        r, _, _ = select.select(socks, [], [], 0.05)
        now = time.monotonic()
        for so in r:
            for _ in range(512):
                try:
                    g.handle(so.recv(2048), now)
                except BlockingIOError:
                    break
        if now >= next_tick:
            g.tick(now)
            next_tick = now + 0.05
        if now >= next_report:
            print(json.dumps({"progress": g.progress(now), "done": g.done()}), flush=True)
            next_report = now + 1.0
    g.terminate()
    print(json.dumps({"final": g.progress(time.monotonic()), "latencies": g.latencies()}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
