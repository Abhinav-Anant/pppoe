"""bngctl - operator CLI for one BNG node.

Each sub-command is a typed operation; no operator text reaches a shell.
Mutating commands need root and are written to the audit log.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

from app.accel.cmd import SID_RE, AccelCmd, AccelService
from app.config.manager import ApplyError, ConfigManager, Paths
from app.config.model import load
from app.monitoring import health
from app.networking import firewall, guard
from app.networking.interfaces import list_interfaces
from app.radius import probe

SEARCH_RE = re.compile(r"^[A-Za-z0-9_.@:\-]{1,64}$")


def _admin() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _source() -> str:
    ssh = os.environ.get("SSH_CLIENT")
    return f"cli:{ssh.split()[0]}" if ssh else "cli:local"


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this command changes the system; run it with sudo")


def _secret(paths: Paths) -> str | None:
    try:
        return paths.secret.read_text().strip()
    except OSError:
        return None


def _db_url() -> str | None:
    try:
        from app.db import db_url  # management extra; core commands must work without it
    except ImportError:
        return None
    return db_url()


def _show(v: bytes) -> str:
    try:
        s = v.decode()
        return s if s.isprintable() else v.hex()
    except UnicodeDecodeError:
        return v.hex()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bngctl", description="BNG node control")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    x = sub.add_parser("sessions")
    x.add_argument("--search")
    x.add_argument("--json", action="store_true")
    ses = sub.add_parser("session").add_subparsers(dest="action", required=True)
    ses.add_parser("show").add_argument("sid")
    x = ses.add_parser("disconnect")
    x.add_argument("sid")
    x.add_argument("--hard", action="store_true")
    rad = sub.add_parser("radius").add_subparsers(dest="action", required=True)
    rad.add_parser("test").add_argument("--user")
    cfg = sub.add_parser("config").add_subparsers(dest="action", required=True)
    for name in ("validate", "diff", "apply"):
        x = cfg.add_parser(name)
        x.add_argument("file", nargs="?", type=Path)
        if name == "apply":
            x.add_argument("--allow-restart", action="store_true")
    x = cfg.add_parser("rollback")
    x.add_argument("version", nargs="?", type=int)
    x.add_argument("--allow-restart", action="store_true")
    cfg.add_parser("history")
    sub.add_parser("health")
    sub.add_parser("interfaces")
    sub.add_parser("backup")
    x = sub.add_parser("restore")
    x.add_argument("archive", type=Path)
    x.add_argument("--allow-restart", action="store_true")
    sub.add_parser("nat").add_subparsers(dest="action", required=True).add_parser("status")
    fw = sub.add_parser("firewall").add_subparsers(dest="action", required=True)
    fw.add_parser("apply")
    fw.add_parser("confirm")
    sub.add_parser("db").add_subparsers(dest="action", required=True).add_parser("upgrade")
    adm = sub.add_parser("admin").add_subparsers(dest="action", required=True)
    adm.add_parser("list")
    for name in ("create", "passwd", "disable", "enable"):
        x = adm.add_parser(name)
        x.add_argument("username")
        if name == "create":
            x.add_argument("--role", required=True)
        if name in ("create", "passwd"):
            x.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = Paths()
    accel = AccelCmd()
    mgr = ConfigManager(paths, AccelService(accel), lambda c: health.critical_failures(c, accel))
    try:
        return _dispatch(args, paths, accel, mgr)
    except (ApplyError, RuntimeError, ValueError, OSError) as e:
        print(f"bngctl: {e}", file=sys.stderr)
        return 1


def _dispatch(a, paths: Paths, accel: AccelCmd, mgr: ConfigManager) -> int:
    who = {"admin": _admin(), "source": _source()}

    if a.cmd == "status":
        print("accel-ppp.service:", "active" if AccelService(accel).is_active() else "INACTIVE")
        print(accel.version())
        print(accel.stat())
        return 0

    if a.cmd == "sessions":
        match = None
        if a.search:
            if not SEARCH_RE.match(a.search):
                raise ValueError("search may contain only letters, digits and _.@:-")
            match = ("username", a.search)
        rows = accel.sessions(match)
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            cols = ("sid", "username", "ip", "calling-sid", "ifname", "state", "uptime-raw", "rate-limit")
            print("  ".join(f"{c:<18}" for c in cols))
            for r in rows:
                print("  ".join(f"{r[c]:<18}" for c in cols))
            print(f"{len(rows)} session(s)")
        return 0

    if a.cmd == "session":
        if not SID_RE.match(a.sid):
            raise ValueError("invalid session id")
        if a.action == "show":
            rows = accel.sessions(("sid", f"^{a.sid}$"))
            if not rows:
                raise ValueError(f"no session {a.sid}")
            for k, v in rows[0].items():
                print(f"{k:<14}{v}")
            return 0
        _require_root()
        accel.terminate(a.sid, hard=a.hard)
        mode = "hard" if a.hard else "soft"
        mgr.audit(**who, action="session_disconnect", result="ok", session_id=a.sid, mode=mode)
        print(f"session {a.sid} terminated ({mode})")
        return 0

    if a.cmd == "radius":
        cfg = load(paths.config)
        if cfg.aaa != "radius":
            raise ValueError("aaa=lab: no RADIUS servers configured")
        secret = _secret(paths)
        if not secret:
            raise ValueError(f"cannot read {paths.secret} (run with sudo)")
        password = getpass.getpass(f"password for {a.user}: ") if a.user else None
        rc = 1
        for s in cfg.radius.servers:
            host, key = str(s.address), secret.encode()
            if a.user:
                r = probe.access_request(host, s.auth_port, key, a.user, password,
                                         str(cfg.radius.nas_ip_address), cfg.radius.nas_identifier)
            else:
                r = probe.status_server(host, s.auth_port, key, cfg.radius.nas_identifier)
            if r is None:
                print(f"{host}:{s.auth_port}  no valid reply (timeout, wrong secret, or Status-Server unsupported)")
                continue
            rc = 0
            print(f"{host}:{s.auth_port}  {r.name}  {r.rtt_ms:.0f} ms")
            for t, v in r.attrs:
                print(f"    attr {t}: {_show(v)}")
        return rc

    if a.cmd == "config":
        if a.action == "history":
            for m in mgr.history():
                print(f"v{m['version']:<5}{m['timestamp']}  {m['admin']:<12}{m['source']}"
                      + ("  (restart)" if m.get("restart") else ""))
            return 0
        if a.action == "rollback":
            _require_root()
            print(mgr.rollback(a.version, who["admin"], who["source"], a.allow_restart))
            return 0
        f = a.file or paths.config
        if a.action == "validate":
            mgr.build(f)
            print(f"{f}: valid")
            return 0
        if a.action == "diff":
            print(mgr.diff(f) or "no changes")
            return 0
        _require_root()
        print(mgr.apply(f, who["admin"], who["source"], a.allow_restart))
        return 0

    if a.cmd == "health":
        checks = health.run_all(load(paths.config), accel, _secret(paths), _db_url())
        print(health.format_report(checks))
        return 1 if any(c.status == "FAIL" for c in checks) else 0

    if a.cmd == "interfaces":
        print(f"{'name':<12}{'state':<9}{'mac':<19}{'mtu':>6}{'speed':>8}{'q rx/tx':>9}"
              f"{'rx bytes':>16}{'tx bytes':>16}{'err':>7}{'drop':>7}")
        for n in list_interfaces():
            speed = f"{n['speed_mbps']}M" if n["speed_mbps"] else "-"
            queues = f"{n['rx_queues']}/{n['tx_queues']}"
            print(f"{n['name']:<12}{n['state']:<9}{n['mac']:<19}{n['mtu']:>6}{speed:>8}{queues:>9}"
                  f"{n['rx_bytes']:>16}{n['tx_bytes']:>16}"
                  f"{n['rx_errors'] + n['tx_errors']:>7}{n['rx_dropped'] + n['tx_dropped']:>7}")
        return 0

    if a.cmd == "backup":
        _require_root()
        print(mgr.backup_archive())
        return 0

    if a.cmd == "restore":
        _require_root()
        print(mgr.restore_archive(a.archive, who["admin"], who["source"], a.allow_restart))
        return 0

    if a.cmd == "nat":
        cfg = load(paths.config)
        print(health.format_report([health.check_nat(cfg), health.check_conntrack()]))
        for p in (cfg.nat.pools if cfg.nat else []):
            print(f"pool {p.name:<12} {', '.join(map(str, p.subscribers))} -> {p.snat_target()}")
        for c in firewall.nat_counters():
            print(f"counter {c['pool']:<10} packets={c['packets']} bytes={c['bytes']}")
        return 0

    if a.cmd == "firewall":
        _require_root()
        if a.action == "apply":
            firewall.apply(load(paths.config))
            print(f"firewall loaded. It reverts in {guard.CONFIRM_SECONDS}s unless you open a NEW ssh "
                  "session and run: sudo bngctl firewall confirm")
        else:
            firewall.confirm()
            print("firewall confirmed and persisted")
        mgr.audit(**who, action=f"firewall_{a.action}", result="ok")
        return 0

    if a.cmd in ("db", "admin"):
        _require_root()
        url = _db_url()
        if not url:
            raise ValueError("no management database configured (/etc/bng-platform/secrets/db.url)")
        return _manage(a, url, who)
    return 2


def _read_password(a) -> str:
    from app.api.auth import check_password_policy

    if a.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass(f"new password for {a.username}: ")
        if getpass.getpass("repeat: ") != pw:
            raise ValueError("passwords do not match")
    check_password_policy(pw)
    return pw


def _manage(a, url: str, who: dict) -> int:
    from sqlalchemy import select

    from app import db
    from app.api import auth

    if a.cmd == "db":
        db.upgrade(url)
        print("database schema is current")
        return 0
    with db.make_sessionmaker(url)() as s:
        if a.action == "list":
            for u in s.scalars(select(db.Admin).order_by(db.Admin.username)):
                print(f"{u.username:<24}{u.role:<16}{'disabled' if u.disabled else 'active':<10}"
                      f"{u.last_login_at or ''}")
            return 0
        user = s.scalar(select(db.Admin).where(db.Admin.username == a.username))
        if a.action == "create":
            if user:
                raise ValueError(f"admin {a.username} exists")
            if a.role not in auth.ROLES:
                raise ValueError(f"role must be one of {', '.join(auth.ROLES)}")
            if not re.fullmatch(r"[A-Za-z0-9_.@\-]{1,64}", a.username):
                raise ValueError("username may contain only letters, digits and _.@-")
            s.add(db.Admin(username=a.username, role=a.role, password_hash=auth.hash_password(_read_password(a))))
        elif not user:
            raise ValueError(f"no admin {a.username}")
        elif a.action == "passwd":
            user.password_hash = auth.hash_password(_read_password(a))
            s.execute(db.AuthSession.__table__.delete().where(db.AuthSession.admin_id == user.id))
        else:
            user.disabled = a.action == "disable"
        s.commit()
        auth.audit(s, f"admin_{a.action}", "ok", admin=who["admin"], ip=who["source"], target=a.username)
    print(f"admin {a.username}: {a.action} done")
    return 0
