"""admins, auth_sessions, audit_logs

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    ts = lambda: sa.DateTime(timezone=True)  # noqa: E731
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("disabled", sa.Boolean, nullable=False),
        sa.Column("created_at", ts(), nullable=False),
        sa.Column("last_login_at", ts(), nullable=True),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("admin_id", sa.Integer, sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_ip", sa.String(45), nullable=False),
        sa.Column("created_at", ts(), nullable=False),
        sa.Column("last_seen_at", ts(), nullable=False),
    )
    op.create_index("ix_auth_sessions_admin_id", "auth_sessions", ["admin_id"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("timestamp", ts(), nullable=False),
        sa.Column("admin", sa.String(64), nullable=True),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(255), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON, nullable=True),
    )
    for col in ("timestamp", "admin", "source_ip", "action"):
        op.create_index(f"ix_audit_logs_{col}", "audit_logs", [col])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("auth_sessions")
    op.drop_table("admins")
