"""Administrator authentication and role-based access.

- Passwords: scrypt (stdlib), per-password salt, constant-time compare.
- Sessions: random token in an HttpOnly SameSite=Strict cookie; only its SHA-256
  is stored, so a database dump cannot be replayed as a login.
- CSRF: every state-changing request must echo the per-session token in
  X-CSRF-Token (login itself needs X-Requested-With, which forces a CORS preflight
  that this API never grants).
- Brute force: failed logins are counted from audit_logs (per IP and per username).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Admin, AuditLog, AuthSession, now

PERMISSIONS = ("view_sessions", "disconnect_sessions", "change_qos", "change_radius", "change_network",
               "change_nat", "apply_config", "rollback_config", "view_logs", "manage_users")
ROLES: dict[str, frozenset[str]] = {
    "super_admin": frozenset(PERMISSIONS),
    "network_admin": frozenset(PERMISSIONS) - {"manage_users"},
    "noc_operator": frozenset({"view_sessions", "disconnect_sessions", "view_logs"}),
    "read_only": frozenset({"view_sessions"}),
}

COOKIE = "bng_session"
IDLE = timedelta(minutes=30)
ABSOLUTE = timedelta(hours=12)
FAIL_WINDOW = timedelta(minutes=15)
MAX_FAILS_PER_USER, MAX_FAILS_PER_IP = 5, 20
MIN_PASSWORD = 12

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(16))  # equalises timing for unknown usernames


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def client_ip(request: Request) -> str:
    # bng-api binds to 127.0.0.1 behind an SSH tunnel / the Phase 6 reverse proxy;
    # proxy headers are not trusted until that proxy exists.
    return request.client.host if request.client else "unknown"


def audit(db: Session, action: str, result: str, admin: str | None = None, ip: str | None = None,
          target: str | None = None, **detail) -> None:
    db.add(AuditLog(admin=admin, source_ip=ip, action=action, target=target, result=result,
                    detail=detail or None))
    db.commit()


def get_db(request: Request):
    maker = request.app.state.sessionmaker
    if maker is None:
        raise HTTPException(503, "management database not configured")
    with maker() as db:
        yield db


def _recent_failures(db: Session, **where) -> int:
    col = {"admin": AuditLog.admin, "ip": AuditLog.source_ip}
    q = select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "login", AuditLog.result == "failed", AuditLog.timestamp > now() - FAIL_WINDOW)
    for k, v in where.items():
        q = q.where(col[k] == v)
    return db.scalar(q)


def login(db: Session, username: str, password: str, ip: str) -> tuple[Admin, str, str]:
    """-> (admin, session token, csrf token). Raises HTTPException 401/429."""
    if _recent_failures(db, ip=ip) >= MAX_FAILS_PER_IP or _recent_failures(db, admin=username) >= MAX_FAILS_PER_USER:
        audit(db, "login", "throttled", admin=username, ip=ip)
        raise HTTPException(429, "too many failed logins; try again later")
    admin = db.scalar(select(Admin).where(Admin.username == username))
    ok = verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
    if not admin or not ok or admin.disabled:
        audit(db, "login", "failed", admin=username, ip=ip,
              reason="disabled" if admin and ok else "bad credentials")
        raise HTTPException(401, "invalid username or password")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_sha(token), csrf_hash=_sha(csrf), admin_id=admin.id, source_ip=ip))
    admin.last_login_at = now()
    db.commit()
    audit(db, "login", "ok", admin=username, ip=ip)
    return admin, token, csrf


def logout(db: Session, token: str) -> None:
    if s := _session(db, token):
        db.delete(s)
        db.commit()


def _session(db: Session, token: str) -> AuthSession | None:
    return db.scalar(select(AuthSession).where(AuthSession.token_hash == _sha(token))) if token else None


def rotate_csrf(db: Session, token: str) -> str:
    csrf = secrets.token_urlsafe(32)
    _session(db, token).csrf_hash = _sha(csrf)
    db.commit()
    return csrf


def drop_other_sessions(db: Session, admin_id: int, keep_token: str) -> None:
    """After a password change / disable: every other login of that admin ends."""
    q = AuthSession.__table__.delete().where(AuthSession.admin_id == admin_id)
    if keep_token:
        q = q.where(AuthSession.token_hash != _sha(keep_token))
    db.execute(q)
    db.commit()


class Principal:
    def __init__(self, admin: Admin, ip: str):
        self.username, self.role, self.ip = admin.username, admin.role, ip
        self.permissions = ROLES.get(admin.role, frozenset())


def _aware(dt):
    # SQLite returns naive datetimes; PostgreSQL returns aware ones.
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)


def authenticate(request: Request, db: Session) -> Principal:
    token = request.cookies.get(COOKIE)
    s = _session(db, token)
    t = now()
    if s and (t - _aware(s.last_seen_at) > IDLE or t - _aware(s.created_at) > ABSOLUTE):
        db.delete(s)
        db.commit()
        s = None
    admin = db.get(Admin, s.admin_id) if s else None
    if not admin or admin.disabled:
        raise HTTPException(401, "not logged in")
    if request.scope["type"] == "http" and request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(_sha(sent), s.csrf_hash):
            raise HTTPException(403, "missing or wrong X-CSRF-Token")
    s.last_seen_at = t
    db.commit()
    return Principal(admin, client_ip(request))


def require(*perms: str):
    """Dependency: logged in and holding every permission in `perms`."""
    def dep(request: Request, db: Session = Depends(get_db)) -> Principal:
        p = authenticate(request, db)
        missing = [x for x in perms if x not in p.permissions]
        if missing:
            raise HTTPException(403, f"role {p.role} lacks {', '.join(missing)}")
        return p
    return dep
