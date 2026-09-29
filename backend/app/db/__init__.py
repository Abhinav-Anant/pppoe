"""Management-plane state (PostgreSQL in production, SQLite in tests).

Nothing in the data plane reads this: accel-ppp, nftables and the shaper run
from files rendered on the node, so the database can be down without touching
a PPPoE session. Jaze stays the subscriber database; nothing here duplicates it.
Schema changes go through Alembic (app/db/migrations); `bngctl db upgrade`.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_URL_FILE = Path("/etc/bng-platform/secrets/db.url")


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    """Server-side login session; the browser holds only the random token."""
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    source_ip: Mapped[str] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    admin: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def db_url() -> str | None:
    """BNG_DB_URL, else the root-only secrets file written by install.sh."""
    if url := os.environ.get("BNG_DB_URL"):
        return url
    try:
        return DB_URL_FILE.read_text().strip()
    except OSError:
        return None


def make_sessionmaker(url: str) -> sessionmaker:
    engine = create_engine(url, pool_pre_ping=True, connect_args=_connect_args(url))
    return sessionmaker(engine, expire_on_commit=False)


def _connect_args(url: str) -> dict:
    return {"connect_timeout": 3} if url.startswith("postgresql") else {}


def upgrade(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
