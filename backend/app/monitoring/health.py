"""`bngctl health`: one line per subsystem. SKIP means "not built yet", never PASS."""
from __future__ import annotations

import json
import re
import socket
import subprocess
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.accel.cmd import AccelCmd, AccelError
from app.config.model import BngConfig
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


def check_radius(cfg: BngConfig, secret: str | dict[str, str] | None) -> Check:
    """Status-Server (RFC 5997) to each server with its own secret. Servers that do not implement
    Status-Server set radius.status_server: false; they are then not probed (SKIP), not failed."""
    if cfg.aaa != "radius":
        return Check("RADIUS", "SKIP", "aaa=lab (local chap-secrets)")
    keys = secret if isinstance(secret, dict) else ({"": secret} if secret else {})
    if not keys:
        return Check("RADIUS", "FAIL", "secret unreadable (run as root) or missing")
    if not cfg.radius.status_server:
        return Check("RADIUS", "SKIP", "Status-Server probing off (radius.status_server: false); "
                                       "use bngctl radius test --user to check authentication")
    parts, ok = [], False
    for s in cfg.radius.servers:
        key = keys.get(str(s.address)) or keys.get("")
        if not key:
            parts.append(f"{s.address} no secret")
            continue
        r = probe.status_server(str(s.address), s.auth_port, key.encode(), cfg.radius.nas_identifier,
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


def check_dns(cfg: BngConfig, port: int = 53, timeout: float = 2.0) -> Check:
    """The resolvers handed to subscribers must answer (a root priming query: no third-party name needed)."""
    if not cfg.dns:
        return Check("DNS", "SKIP", "no dns servers configured")
    query = bytes.fromhex("133701000001000000000000" "00" "0002" "0001")  # id 0x1337, RD, ". IN NS"
    down = []
    for ip in map(str, cfg.dns):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            try:
                s.sendto(query, (ip, port))
                if s.recvfrom(512)[0][:2] == query[:2]:
                    continue
            except OSError:
                pass
        down.append(ip)
    return _ok("DNS", not down, f"no answer from {', '.join(down)}" if down else ", ".join(map(str, cfg.dns)))


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


def check_rates(cfg: BngConfig, accel) -> Check:
    """Rates RADIUS dictated, as accel-ppp actually applied them (kbit/s)."""
    s = cfg.shaper
    if not s:
        return Check("Rate limits", "SKIP", "no shaper configured")
    try:
        # starting/finishing sessions have no shaper yet (seen on bng01 during teardown)
        rows = [r for r in accel.sessions() if r["state"] == "active"]
    except AccelError as e:
        return Check("Rate limits", "FAIL", str(e))
    over, missing = [], []
    for r in rows:
        down, _, up = r["rate-limit"].partition("/")
        if not down.isdigit():
            missing.append(r["username"])
        elif s.max_rate_mbit and max(int(down), int(up or 0)) > s.max_rate_mbit * 1000:
            over.append(f"{r['username']} {r['rate-limit']}")
    problems = []
    if over:
        problems.append(f"{len(over)} above {s.max_rate_mbit} Mbit ({', '.join(over[:5])}); "
                        "a 'G' rate suffix is read x10 by accel-ppp 1.14.0 - send M")
    if missing and s.require_rate:
        problems.append(f"{len(missing)} without a RADIUS rate ({', '.join(missing[:5])})")
    return _ok("Rate limits", not problems, "; ".join(problems) or f"{len(rows)} sessions checked")


EASYWALL_TOML = Path("/etc/easywall/easywall.toml")


def check_easywall() -> Check:
    """easywall's forward chain is a base chain: anything but routing.mode = "open"
    lets it drop subscriber traffic that bng_filter accepted (a drop at a hook is final)."""
    try:
        with open(EASYWALL_TOML, "rb") as f:
            mode = tomllib.load(f).get("routing", {}).get("mode", "closed")
    except FileNotFoundError:
        return Check("easywall", "SKIP", "not installed")
    except (OSError, tomllib.TOMLDecodeError) as e:
        return Check("easywall", "FAIL", f"cannot read {EASYWALL_TOML}: {e}")
    if mode != "open":
        return Check("easywall", "FAIL", f'routing.mode = "{mode}" would drop subscriber traffic; set "open"')
    active = _run("systemctl", "is-active", "--quiet", "easywall-core.service").returncode == 0
    return _ok("easywall", active, 'core active, routing.mode = "open"' if active else "easywall-core not active")


API_URL = "http://127.0.0.1:8080/api/livez"


API_UNIT = Path("/etc/systemd/system/bng-api.service")


def check_api() -> Check:
    if not API_UNIT.exists():
        return Check("API", "SKIP", "bng-api not installed")
    try:
        with urllib.request.urlopen(API_URL, timeout=3) as r:
            return _ok("API", r.status == 200, f"{API_URL} {r.status}")
    except OSError as e:
        return Check("API", "FAIL", f"{API_URL}: {e}")


def check_database(url: str | None) -> Check:
    if not url:
        return Check("Database", "SKIP", "not configured (run as root to read the DB URL)")
    from sqlalchemy import create_engine, text  # management extra; not needed by the data plane

    from app.db import _connect_args
    try:
        engine = create_engine(url, connect_args=_connect_args(url))
        with engine.connect() as c:
            rev = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        engine.dispose()
        return Check("Database", "PASS", f"schema {rev}")
    except Exception as e:  # driver errors carry no common base we can import lazily
        return Check("Database", "FAIL", str(e).splitlines()[0])


def critical_failures(cfg: BngConfig, accel: AccelCmd) -> list[str]:
    """Checks that gate a config apply; anything failing here triggers rollback."""
    checks = [check_service(), check_cli(accel), check_pppoe(cfg, accel)]
    return [f"{c.name}: {c.detail}" for c in checks if c.status == "FAIL"]


def run_all(cfg: BngConfig, accel: AccelCmd, secret: str | dict[str, str] | None,
            db_url: str | None = None) -> list[Check]:
    return [
        check_service(), check_cli(accel, wait_s=0), check_pppoe(cfg, accel), check_radius(cfg, secret),
        check_nic(cfg), check_route(), check_dns(cfg), check_nftables(), check_conntrack(),
        check_rates(cfg, accel), check_nat(cfg), check_easywall(),
        check_api(), check_database(db_url),
    ]


def format_report(checks: list[Check]) -> str:
    return "\n".join(f"{c.name:<16}{c.status:<6}{c.detail}".rstrip() for c in checks)
