"""bng-api: typed management API for one node (uvicorn app.api.main:app).

Management plane only: every operation goes through the same modules as
bngctl (AccelCmd, ConfigManager, health). There is no endpoint that runs a
shell command or passes operator text to tc/nft; if this service or its
database stops, PPPoE sessions and forwarding are unaffected.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import os
import socket
import tempfile
import threading
import time
from ipaddress import IPv4Address
from pathlib import Path
from typing import Literal

import yaml
from fastapi import Body, Depends, FastAPI, HTTPException, Path as PathParam, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.accel.cmd import AccelCmd, AccelError, AccelService
from app.api import auth
from app.api.auth import Principal, audit, get_db, require
from app.config.manager import ApplyError, ConfigManager, Paths
from app.config.model import BngConfig, Shaper, load
from app.db import Admin, AuditLog, db_url, make_sessionmaker
from app.monitoring import health
from app.networking import firewall
from app.networking.interfaces import list_interfaces

SID = r"^[0-9A-Za-z]{1,32}$"
SEARCH = r"^[A-Za-z0-9_.@:\-]{1,64}$"
USERNAME = r"^[A-Za-z0-9_.@\-]{1,64}$"
SESSIONS_TTL = 2.0  # seconds; one `show sessions` serves every poller in that window

# which permission a change to each top-level config key needs (besides apply_config)
SECTION_PERMISSION = {"shaper": "change_qos", "radius": "change_radius", "aaa": "change_radius",
                      "nat": "change_nat"}


class Node:
    """Everything the handlers touch, injectable for tests."""

    def __init__(self, paths: Paths = Paths(), accel: AccelCmd | None = None, service=None):
        self.paths = paths
        self.accel = accel or AccelCmd()
        self.service = service or AccelService(self.accel)
        self.mgr = ConfigManager(paths, self.service, lambda c: health.critical_failures(c, self.accel))
        self._cache: tuple[float, list[dict]] = (0.0, [])
        self._lock = threading.Lock()

    def config(self) -> BngConfig:
        return load(self.paths.config)

    def secret(self) -> str | None:
        try:
            return self.paths.secret.read_text().strip()
        except OSError:
            return None

    def sessions(self) -> list[dict]:
        with self._lock:
            t, rows = self._cache
            if time.monotonic() - t > SESSIONS_TTL:
                rows = [_session(r) for r in self.accel.sessions()]
                _mark_duplicates(rows)
                self._cache = (time.monotonic(), rows)
            return rows

    def forget_sessions(self) -> None:
        with self._lock:
            self._cache = (0.0, [])


def _int(s: str) -> int | None:
    return int(s) if s.isdigit() else None


def _session(r: dict) -> dict:
    """accel row -> API row. Direction is explicit: accel counts on pppN, so rx
    is what the subscriber sent (upload) and tx what it received (download)."""
    down, _, up = r["rate-limit"].partition("/")
    return {
        "sid": r["sid"], "username": r["username"], "ip": r["ip"] or None, "ip6": r["ip6"] or None,
        "ip6_delegated": r["ip6-dp"] or None, "mac": r["calling-sid"], "called_sid": r["called-sid"],
        "ifname": r["ifname"], "state": r["state"], "uptime_s": _int(r["uptime-raw"]),
        "upload_bytes": _int(r["rx-bytes-raw"]), "download_bytes": _int(r["tx-bytes-raw"]),
        "rate_limit": r["rate-limit"] or None,
        "rate_down_kbit": _int(down), "rate_up_kbit": _int(up) if up else _int(down),
        "duplicate": False,
    }


def _mark_duplicates(rows: list[dict]) -> None:
    """Same username or same MAC twice among live sessions (stale session / shared account)."""
    for key in ("username", "mac"):
        seen: dict[str, list[dict]] = {}
        for r in rows:
            if r[key]:
                seen.setdefault(r[key], []).append(r)
        for group in seen.values():
            if len(group) > 1:
                for r in group:
                    r["duplicate"] = True


def _proc(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _host() -> dict:
    load_avg = (_proc("/proc/loadavg") or "").split()[:3]
    mem = {}
    for line in (_proc("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            mem[k] = int(v.split()[0]) * 1024
    up = (_proc("/proc/uptime") or "").split()
    return {"hostname": socket.gethostname(), "uptime_s": float(up[0]) if up else None,
            "load": [float(x) for x in load_avg] or None,
            "mem_total_bytes": mem.get("MemTotal"), "mem_available_bytes": mem.get("MemAvailable"),
            "cpus": os.cpu_count()}


def _check(c: health.Check) -> dict:
    return {"name": c.name, "status": c.status, "detail": c.detail}


def _stat(node: Node) -> dict:
    try:
        return node.accel.stat_dict()
    except AccelError as e:
        raise HTTPException(503, f"accel-ppp unavailable: {e}") from e


# --- request bodies ---------------------------------------------------------

class LoginIn(BaseModel):
    username: str = Field(pattern=USERNAME)
    password: str = Field(min_length=1, max_length=1024)


class PasswordIn(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


class DisconnectIn(BaseModel):
    hard: bool = False


class ConfigIn(BaseModel):
    config: dict
    allow_restart: bool = False


class RollbackIn(BaseModel):
    version: int | None = Field(default=None, ge=1)
    allow_restart: bool = False


class QosIn(BaseModel):
    shaper: Shaper | None
    allow_restart: bool = False


class UserIn(BaseModel):
    username: str = Field(pattern=USERNAME)
    password: str = Field(max_length=1024)
    role: Literal[tuple(auth.ROLES)]  # type: ignore[valid-type]


class UserPatch(BaseModel):
    role: Literal[tuple(auth.ROLES)] | None = None  # type: ignore[valid-type]
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=1024)


# --- app ----------------------------------------------------------------------

class RateLimiter:
    """Token bucket per client IP.
    ponytail: in-process, so run bng-api with one worker; move to Redis if it ever scales out."""

    def __init__(self, rate: float = 20.0, burst: float = 60.0):
        self.rate, self.burst, self.buckets = rate, burst, {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        with self._lock:
            now = time.monotonic()
            tokens, last = self.buckets.get(ip, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= 1
            self.buckets[ip] = (tokens - 1 if ok else tokens, now)
            return ok


def create_app(node: Node | None = None, database_url: str | None = None) -> FastAPI:
    app = FastAPI(title="BNG management API", version="0.5.0", docs_url="/api/docs",
                  redoc_url=None, openapi_url="/api/openapi.json")
    app.state.node = node or Node()
    url = database_url or db_url()
    app.state.db_url = url
    app.state.sessionmaker = make_sessionmaker(url) if url else None
    app.state.cookie_secure = os.environ.get("BNG_API_COOKIE_SECURE", "1") != "0"
    limiter = RateLimiter()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not limiter.allow(auth.client_ip(request)):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(OperationalError)
    async def db_down(request: Request, exc: OperationalError):
        # PPPoE is unaffected; only management actions that need the DB fail.
        return JSONResponse({"detail": "management database unavailable"}, status_code=503)

    def the_node(request: Request) -> Node:
        return request.app.state.node

    # --- auth -------------------------------------------------------------
    @app.get("/api/livez", include_in_schema=False)
    def livez():
        return {"ok": True}

    @app.post("/api/auth/login", tags=["auth"])
    def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
        if request.headers.get("x-requested-with") != "bng":
            raise HTTPException(403, "missing X-Requested-With: bng")
        admin, token, csrf = auth.login(db, body.username, body.password, auth.client_ip(request))
        response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict",
                            secure=request.app.state.cookie_secure, path="/api",
                            max_age=int(auth.ABSOLUTE.total_seconds()))
        return {"username": admin.username, "role": admin.role, "csrf_token": csrf,
                "permissions": sorted(auth.ROLES[admin.role])}

    @app.post("/api/auth/logout", tags=["auth"])
    def logout(request: Request, response: Response, p: Principal = Depends(require()),
               db: Session = Depends(get_db)):
        auth.logout(db, request.cookies.get(auth.COOKIE, ""))
        audit(db, "logout", "ok", admin=p.username, ip=p.ip)
        response.delete_cookie(auth.COOKIE, path="/api")
        return {"ok": True}

    @app.get("/api/auth/me", tags=["auth"])
    def me(request: Request, p: Principal = Depends(require()), db: Session = Depends(get_db)):
        """Also re-issues the CSRF token, e.g. after a browser reload lost it."""
        return {"username": p.username, "role": p.role, "permissions": sorted(p.permissions),
                "csrf_token": auth.rotate_csrf(db, request.cookies.get(auth.COOKIE, ""))}

    @app.post("/api/auth/password", tags=["auth"])
    def change_password(body: PasswordIn, request: Request, p: Principal = Depends(require()),
                        db: Session = Depends(get_db)):
        admin = db.scalar(select(Admin).where(Admin.username == p.username))
        if not auth.verify_password(body.current, admin.password_hash):
            audit(db, "password_change", "failed", admin=p.username, ip=p.ip)
            raise HTTPException(403, "current password is wrong")
        _policy(body.new)
        admin.password_hash = auth.hash_password(body.new)
        auth.drop_other_sessions(db, admin.id, request.cookies.get(auth.COOKIE, ""))
        audit(db, "password_change", "ok", admin=p.username, ip=p.ip)
        return {"ok": True}

    # --- system -----------------------------------------------------------
    @app.get("/api/system/status", tags=["system"])
    def system_status(node: Node = Depends(the_node), _: Principal = Depends(require())):
        cfg = node.config()
        try:
            version, stat = node.accel.version(), node.accel.stat_dict()
        except AccelError as e:
            version, stat = None, {"error": str(e)}
        return {"node": cfg.node, "accel_active": node.service.is_active(), "accel_version": version,
                "accel": stat, "host": _host()}

    @app.get("/api/health", tags=["system"])
    def health_all(request: Request, node: Node = Depends(the_node), _: Principal = Depends(require())):
        checks = health.run_all(node.config(), node.accel, node.secret(), request.app.state.db_url)
        return {"ok": not any(c.status == "FAIL" for c in checks), "checks": [_check(c) for c in checks]}

    @app.get("/api/interfaces", tags=["system"])
    def interfaces(_: Principal = Depends(require())):
        return list_interfaces()

    @app.get("/api/metrics", tags=["system"])
    def metrics(node: Node = Depends(the_node), _: Principal = Depends(require())):
        """Point-in-time counters for dashboards; long-term series belong in Prometheus/Zabbix."""
        cfg = node.config()
        stat, rows = _stat(node), node.sessions()
        uplink = next((n for n in list_interfaces() if n["name"] == cfg.uplink), None)
        ct = {k: _int((_proc(f"/proc/sys/net/netfilter/nf_conntrack_{k}") or "").strip()) for k in ("count", "max")}
        try:
            nat = firewall.nat_counters()
        except OSError:
            nat = []
        return {"timestamp": time.time(), "sessions": stat.get("sessions", {}),
                "pppoe": stat.get("pppoe", {}),
                "subscriber_upload_bytes": sum(r["upload_bytes"] or 0 for r in rows),
                "subscriber_download_bytes": sum(r["download_bytes"] or 0 for r in rows),
                "uplink": uplink, "conntrack": ct, "nat": nat}

    # --- sessions ---------------------------------------------------------
    @app.get("/api/sessions", tags=["sessions"])
    def sessions(node: Node = Depends(the_node), _: Principal = Depends(require("view_sessions")),
                 search: str | None = Query(None, pattern=SEARCH,
                                            description="substring of username, IP, MAC or interface"),
                 state: Literal["start", "active", "finish"] | None = None,
                 duplicates: bool = False,
                 sort: Literal["username", "ip", "uptime_s", "upload_bytes", "download_bytes"] = "username",
                 desc: bool = False,
                 page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)):
        try:
            rows = node.sessions()
        except AccelError as e:
            raise HTTPException(503, f"accel-ppp unavailable: {e}") from e
        if search:
            s = search.lower()
            rows = [r for r in rows if any(s in (r[k] or "").lower() for k in ("username", "ip", "mac", "ifname"))]
        if state:
            rows = [r for r in rows if r["state"] == state]
        if duplicates:
            rows = [r for r in rows if r["duplicate"]]
        key = (lambda r: int(IPv4Address(r["ip"])) if r["ip"] else -1) if sort == "ip" else \
              (lambda r: r[sort] if r[sort] is not None else "" if sort == "username" else -1)
        rows = sorted(rows, key=key, reverse=desc)
        start = (page - 1) * page_size
        return {"total": len(rows), "page": page, "page_size": page_size, "items": rows[start:start + page_size]}

    @app.get("/api/sessions/{sid}", tags=["sessions"])
    def session(sid: str = PathParam(pattern=SID), node: Node = Depends(the_node),
                _: Principal = Depends(require("view_sessions"))):
        for r in node.sessions():
            if r["sid"] == sid:
                return r
        raise HTTPException(404, "no such session")

    @app.post("/api/sessions/{sid}/disconnect", tags=["sessions"])
    def disconnect(sid: str = PathParam(pattern=SID), body: DisconnectIn = Body(DisconnectIn()),
                   node: Node = Depends(the_node), p: Principal = Depends(require("disconnect_sessions")),
                   db: Session = Depends(get_db)):
        mode = "hard" if body.hard else "soft"
        try:
            node.accel.terminate(sid, hard=body.hard)
        except AccelError as e:
            audit(db, "session_disconnect", "failed", admin=p.username, ip=p.ip, target=sid, error=str(e))
            raise HTTPException(502, str(e)) from e
        node.forget_sessions()
        audit(db, "session_disconnect", "ok", admin=p.username, ip=p.ip, target=sid, mode=mode)
        node.mgr.audit(admin=p.username, source=f"api:{p.ip}", action="session_disconnect", result="ok",
                       session_id=sid, mode=mode)
        return {"sid": sid, "mode": mode}

    # --- subsystems -------------------------------------------------------
    @app.get("/api/pppoe/status", tags=["pppoe"])
    def pppoe_status(node: Node = Depends(the_node), _: Principal = Depends(require())):
        cfg = node.config()
        return {"check": _check(health.check_pppoe(cfg, node.accel)),
                "interfaces": [i.model_dump() for i in cfg.pppoe.interfaces],
                "stats": _stat(node).get("pppoe", {})}

    @app.get("/api/radius/status", tags=["radius"])
    def radius_status(node: Node = Depends(the_node), _: Principal = Depends(require())):
        cfg = node.config()
        stat = _stat(node)
        radius = cfg.radius.model_dump(mode="json") if cfg.radius else None  # never contains the secret
        return {"aaa": cfg.aaa, "radius": radius, "check": _check(health.check_radius(cfg, node.secret())),
                "stats": {k: v for k, v in stat.items() if k.startswith("radius")}}

    @app.get("/api/qos/status", tags=["qos"])
    def qos_status(node: Node = Depends(the_node), _: Principal = Depends(require())):
        """Per-subscriber rates as RADIUS dictated them; plans = active sessions per rate."""
        cfg = node.config()
        plans: dict[str, int] = {}
        for r in node.sessions():
            if r["state"] == "active":
                plans[r["rate_limit"] or "unshaped"] = plans.get(r["rate_limit"] or "unshaped", 0) + 1
        return {"shaper": cfg.shaper.model_dump() if cfg.shaper else None,
                "check": _check(health.check_rates(cfg, node.accel)), "plans": plans}

    @app.get("/api/qos/config", tags=["qos"])
    def qos_config(node: Node = Depends(the_node), _: Principal = Depends(require())):
        s = node.config().shaper
        return {"shaper": s.model_dump() if s else None}

    @app.put("/api/qos/config", tags=["qos"])
    def qos_put(body: QosIn, node: Node = Depends(the_node),
                p: Principal = Depends(require("change_qos", "apply_config")), db: Session = Depends(get_db)):
        data = node.config().model_dump(mode="json", exclude_none=True)
        data["shaper"] = body.shaper.model_dump(mode="json", exclude_none=True) if body.shaper else None
        return _apply(node, db, p, data, body.allow_restart)

    @app.get("/api/nat/status", tags=["nat"])
    def nat_status(node: Node = Depends(the_node), _: Principal = Depends(require())):
        cfg = node.config()
        try:
            counters = firewall.nat_counters()
        except OSError:
            counters = []
        return {"check": _check(health.check_nat(cfg)), "conntrack": _check(health.check_conntrack()),
                "counters": counters}

    @app.get("/api/nat/pools", tags=["nat"])
    def nat_pools(node: Node = Depends(the_node), _: Principal = Depends(require())):
        cfg = node.config()
        return [{**p.model_dump(mode="json"), "snat_target": p.snat_target()} for p in (cfg.nat.pools if cfg.nat else [])]

    @app.get("/api/ip-pools", tags=["pppoe"])
    def ip_pools(node: Node = Depends(the_node), _: Principal = Depends(require())):
        cfg = node.config()
        ips = [IPv4Address(r["ip"]) for r in node.sessions() if r["ip"]]
        out = []
        for p in cfg.ip_pools.pools:
            usable = sum(1 for a in p.network if int(a) & 255 not in (0, 255))
            used = sum(1 for a in ips if a in p.network)
            out.append({**p.model_dump(mode="json"), "usable": usable, "used": used})
        return {"gw_ip_address": str(cfg.ip_pools.gw_ip_address), "default": cfg.ip_pools.default, "pools": out}

    # --- configuration ----------------------------------------------------
    @app.get("/api/config", tags=["config"])
    def config(node: Node = Depends(the_node), _: Principal = Depends(require())):
        hist = node.mgr.history()
        return {"version": hist[-1]["version"] if hist else None,
                "config": yaml.safe_load(node.paths.config.read_text(encoding="utf-8"))}

    @app.get("/api/config/history", tags=["config"])
    def config_history(node: Node = Depends(the_node), _: Principal = Depends(require())):
        return node.mgr.history()

    @app.get("/api/config/versions/{version}", tags=["config"], response_class=PlainTextResponse)
    def config_version(version: int = PathParam(ge=1), node: Node = Depends(the_node),
                       _: Principal = Depends(require())):
        """Download a version's config.yaml."""
        f = node.paths.versions / f"{version:04d}" / "config.yaml"
        if not f.exists():
            raise HTTPException(404, "no such version")
        return PlainTextResponse(f.read_text(encoding="utf-8"), media_type="application/yaml",
                                 headers={"Content-Disposition": f'attachment; filename="bng-config-v{version}.yaml"'})

    @app.get("/api/config/versions/{version}/diff", tags=["config"], response_class=PlainTextResponse)
    def config_version_diff(version: int = PathParam(ge=1), against: int | None = Query(None, ge=1),
                            node: Node = Depends(the_node), _: Principal = Depends(require())):
        """Unified diff of config.yaml from `against` (default: the previous version) to `version`."""
        def text(v: int) -> str:
            f = node.paths.versions / f"{v:04d}" / "config.yaml"
            if not f.exists():
                raise HTTPException(404, f"no version {v}")
            return f.read_text(encoding="utf-8")
        new = text(version)
        base = against or version - 1
        old = text(base) if base >= 1 else ""
        return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"v{base}", f"v{version}"))

    @app.post("/api/config/validate", tags=["config"])
    def config_validate(body: ConfigIn, node: Node = Depends(the_node), p: Principal = Depends(require())):
        cfg = _model(body.config)
        with _candidate(node, body.config) as f:
            try:
                diff = node.mgr.diff(f)
            except ApplyError as e:
                raise HTTPException(422, str(e)) from e
        changed = _changed(node, cfg)
        return {"valid": True, "changed": sorted(changed), "required_permissions": sorted(_needed(changed)),
                "allowed": _needed(changed) <= p.permissions, "accel_conf_diff": diff}

    @app.post("/api/config/apply", tags=["config"])
    def config_apply(body: ConfigIn, node: Node = Depends(the_node),
                     p: Principal = Depends(require("apply_config")), db: Session = Depends(get_db)):
        return _apply(node, db, p, body.config, body.allow_restart)

    @app.post("/api/config/rollback", tags=["config"])
    def config_rollback(body: RollbackIn = Body(RollbackIn()), node: Node = Depends(the_node),
                        p: Principal = Depends(require("rollback_config")), db: Session = Depends(get_db)):
        try:
            result = node.mgr.rollback(body.version, p.username, f"api:{p.ip}", body.allow_restart)
        except ApplyError as e:
            audit(db, "config_rollback", "failed", admin=p.username, ip=p.ip, target=str(body.version), error=str(e))
            raise HTTPException(409, str(e)) from e
        node.forget_sessions()
        audit(db, "config_rollback", "ok", admin=p.username, ip=p.ip, target=str(body.version), outcome=result)
        return {"result": result}

    # --- audit and users --------------------------------------------------
    @app.get("/api/audit", tags=["audit"])
    def audit_log(_: Principal = Depends(require("view_logs")), db: Session = Depends(get_db),
                  action: str | None = Query(None, pattern=r"^[a-z_]{1,64}$"),
                  admin: str | None = Query(None, pattern=USERNAME),
                  limit: int = Query(100, ge=1, le=1000)):
        """Management audit (logins, API actions). Config applies from bngctl are in /api/audit/node."""
        q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if action:
            q = q.where(AuditLog.action == action)
        if admin:
            q = q.where(AuditLog.admin == admin)
        return [{"id": a.id, "timestamp": a.timestamp, "admin": a.admin, "source_ip": a.source_ip,
                 "action": a.action, "target": a.target, "result": a.result, "detail": a.detail}
                for a in db.scalars(q)]

    @app.get("/api/audit/node", tags=["audit"])
    def audit_node(node: Node = Depends(the_node), _: Principal = Depends(require("view_logs")),
                   limit: int = Query(100, ge=1, le=1000)):
        """The node's own audit trail (CLI and API config changes, disconnects), newest first."""
        f = node.paths.audit_log
        lines = f.read_text(encoding="utf-8").splitlines()[-limit:] if f.exists() else []
        return [json.loads(line) for line in reversed(lines)]

    @app.get("/api/users", tags=["users"])
    def users(_: Principal = Depends(require("manage_users")), db: Session = Depends(get_db)):
        return [_user(u) for u in db.scalars(select(Admin).order_by(Admin.username))]

    @app.post("/api/users", tags=["users"], status_code=201)
    def user_create(body: UserIn, p: Principal = Depends(require("manage_users")), db: Session = Depends(get_db)):
        if db.scalar(select(Admin).where(Admin.username == body.username)):
            raise HTTPException(409, "username exists")
        _policy(body.password)
        u = Admin(username=body.username, role=body.role, password_hash=auth.hash_password(body.password))
        db.add(u)
        db.commit()
        audit(db, "user_create", "ok", admin=p.username, ip=p.ip, target=body.username, role=body.role)
        return _user(u)

    @app.patch("/api/users/{username}", tags=["users"])
    def user_update(body: UserPatch, username: str = PathParam(pattern=USERNAME),
                    p: Principal = Depends(require("manage_users")), db: Session = Depends(get_db)):
        u = db.scalar(select(Admin).where(Admin.username == username))
        if not u:
            raise HTTPException(404, "no such user")
        if username == p.username and (body.disabled or (body.role and body.role != p.role)):
            raise HTTPException(409, "you cannot disable or demote yourself")
        changes = body.model_dump(exclude_none=True, exclude={"password"})
        for k, v in changes.items():
            setattr(u, k, v)
        if body.password is not None:
            _policy(body.password)
            u.password_hash = auth.hash_password(body.password)
            changes["password"] = "changed"
        if body.password is not None or body.disabled:
            auth.drop_other_sessions(db, u.id, "")
        db.commit()
        audit(db, "user_update", "ok", admin=p.username, ip=p.ip, target=username, **changes)
        return _user(u)

    return app


def _user(u: Admin) -> dict:
    return {"username": u.username, "role": u.role, "disabled": u.disabled,
            "created_at": u.created_at, "last_login_at": u.last_login_at}


def _policy(password: str) -> None:
    try:
        auth.check_password_policy(password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _model(data: dict) -> BngConfig:
    try:
        return BngConfig.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()]) from e


def _changed(node: Node, new: BngConfig) -> set[str]:
    old = node.config().model_dump(mode="json")
    cur = new.model_dump(mode="json")
    return {k for k in old.keys() | cur.keys() if old.get(k) != cur.get(k)}


def _needed(changed: set[str]) -> set[str]:
    return {"apply_config"} | {SECTION_PERMISSION.get(k, "change_network") for k in changed}


@contextlib.contextmanager
def _candidate(node: Node, data: dict):
    """The candidate as a root-only (mkstemp: 0600) YAML file, for ConfigManager."""
    node.paths.state.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="api-candidate-", suffix=".yaml", dir=node.paths.state)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    try:
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def _apply(node: Node, db: Session, p: Principal, data: dict, allow_restart: bool) -> dict:
    cfg = _model(data)
    changed = _changed(node, cfg)
    missing = _needed(changed) - p.permissions
    if missing:
        raise HTTPException(403, f"changing {', '.join(sorted(changed))} needs {', '.join(sorted(missing))}")
    with _candidate(node, data) as f:
        try:
            result = node.mgr.apply(f, p.username, f"api:{p.ip}", allow_restart)
        except ApplyError as e:
            audit(db, "config_apply", "failed", admin=p.username, ip=p.ip, changed=sorted(changed), error=str(e))
            raise HTTPException(409, str(e)) from e
    node.forget_sessions()
    audit(db, "config_apply", "ok", admin=p.username, ip=p.ip, changed=sorted(changed), outcome=result)
    return {"result": result, "changed": sorted(changed)}

