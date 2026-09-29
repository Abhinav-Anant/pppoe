"""`bngctl health`: one line per subsystem. SKIP means "not built yet", never PASS."""
from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app.accel.cmd import AccelCmd, AccelError
from app.config.model import BngConfig
from app.qos import cake
from app.radius import probe


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


def _run(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=10)


def _operstate(ifname: str) -> str:
    try:
        return Path(f"/sys/class/net/{ifname}/operstate").read_text().strip()
    except OSError:
        return "missing"


def _ok(name: str, ok: bool, detail: str = "") -> Check:
    return Check(name, "PASS" if ok else "FAIL", detail)


def check_service() -> Check:
    ok = _run("systemctl", "is-active", "--quiet", "accel-ppp.service").returncode == 0
    return _ok("ACCEL-PPP", ok, "" if ok else "accel-ppp.service is not active")


def check_cli(accel: AccelCmd, wait_s: float = 5.0) -> Check:
    deadline = time.monotonic() + wait_s
    while True:
        try:
            return Check("ACCEL-CLI", "PASS", accel.version())
        except AccelError as e:
            if time.monotonic() >= deadline:
                return Check("ACCEL-CLI", "FAIL", str(e))
            time.sleep(0.5)


def check_pppoe(cfg: BngConfig, accel) -> Check:
    try:
        listed = accel.pppoe_interfaces()
    except AccelError as e:
        return Check("PPPoE", "FAIL", str(e))
    names = [i.name for i in cfg.pppoe.interfaces]
    bad = [n for n in names
           if _operstate(n) not in ("up", "unknown")
           or not re.search(rf"(^|\s){re.escape(n)}([\s:,]|$)", listed, re.M)]
    return _ok("PPPoE", not bad, f"not serving: {', '.join(bad)}" if bad else ", ".join(names))


def check_radius(cfg: BngConfig, secret: str | None) -> Check:
    if cfg.aaa != "radius":
        return Check("RADIUS", "SKIP", "aaa=lab (local chap-secrets)")
    if not secret:
        return Check("RADIUS", "FAIL", "secret unreadable (run as root) or missing")
    parts, ok = [], False
    for s in cfg.radius.servers:
        r = probe.status_server(str(s.address), s.auth_port, secret.encode(), cfg.radius.nas_identifier,
                                timeout=2.0, tries=1)
        ok = ok or r is not None
        parts.append(f"{s.address} {r.rtt_ms:.0f} ms" if r else f"{s.address} no Status-Server reply")
    return _ok("RADIUS", ok, "; ".join(parts))


def check_nic(cfg: BngConfig) -> Check:
    state = _operstate(cfg.uplink)
    return _ok("NIC", state == "up", f"{cfg.uplink} {state}")


def check_route() -> Check:
    p = _run("ip", "-j", "route", "show", "default")
    routes = json.loads(p.stdout or "[]") if p.returncode == 0 else []
    detail = f"via {routes[0].get('gateway')} dev {routes[0].get('dev')}" if routes else "no default route"
    return _ok("Internet route", bool(routes), detail)


def check_dns() -> Check:
    try:
        socket.getaddrinfo("github.com", 443)
        return Check("DNS", "PASS")
    except OSError as e:
        return Check("DNS", "FAIL", str(e))


def check_nftables() -> Check:
    ok = _run("nft", "list", "table", "inet", "bng_filter").returncode == 0
    return _ok("nftables", ok, "" if ok else "table inet bng_filter not loaded")


def check_conntrack() -> Check:
    base = Path("/proc/sys/net/netfilter")
    try:
        count, limit = (int((base / f).read_text()) for f in ("nf_conntrack_count", "nf_conntrack_max"))
    except OSError:
        return Check("conntrack", "SKIP", "nf_conntrack not loaded")
    return _ok("conntrack", count < 0.9 * limit, f"{count}/{limit}")


def check_nat(cfg: BngConfig) -> Check:
    if not cfg.nat:
        return Check("NAT", "SKIP", "no nat section")
    try:
        fwd = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
    except OSError:
        fwd = False
    loaded = _run("nft", "list", "table", "ip", "bng_nat").returncode == 0
    ok = fwd and loaded
    return _ok("NAT", ok, "" if ok else f"ip_forward={int(fwd)}, bng_nat {'loaded' if loaded else 'missing'}")


def check_cake(cfg: BngConfig) -> Check:
    if not cfg.qos or not cfg.qos.cake.enabled:
        return Check("CAKE", "SKIP", "no qos.cake configured")
    st = cake.status(cfg)
    missing = [k.split(" ")[0] for k, v in st.items() if v is None]
    detail = ", ".join(f"{k.split(' ')[0]} {v['bandwidth_mbit']:.0f} Mbit" for k, v in st.items() if v)
    return _ok("CAKE", not missing, f"missing: {', '.join(missing)}" if missing else detail)


def critical_failures(cfg: BngConfig, accel: AccelCmd) -> list[str]:
    """Checks that gate a config apply; anything failing here triggers rollback."""
    checks = [check_service(), check_cli(accel), check_pppoe(cfg, accel)]
    return [f"{c.name}: {c.detail}" for c in checks if c.status == "FAIL"]


def run_all(cfg: BngConfig, accel: AccelCmd, secret: str | None) -> list[Check]:
    return [
        check_service(), check_cli(accel, wait_s=0), check_pppoe(cfg, accel), check_radius(cfg, secret),
        check_nic(cfg), check_route(), check_dns(), check_nftables(), check_conntrack(),
        check_nat(cfg), check_cake(cfg),
        Check("API", "SKIP", "Phase 5"), Check("Database", "SKIP", "Phase 5"),
    ]


def format_report(checks: list[Check]) -> str:
    return "\n".join(f"{c.name:<16}{c.status:<6}{c.detail}".rstrip() for c in checks)
