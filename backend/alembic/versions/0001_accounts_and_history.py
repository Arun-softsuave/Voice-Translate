"""Accounts, sign-in sessions, calls and their transcripts.

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("approved_at", sa.DateTime),
        sa.Column("approved_by", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_seen_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(255)),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone_number", sa.String(32)),
        sa.Column("source_lang", sa.String(16), nullable=False),
        sa.Column("target_lang", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("end_reason", sa.String(32)),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("answered_at", sa.DateTime),
        sa.Column("ended_at", sa.DateTime),
        sa.Column("duration_s", sa.Float),
        sa.Column("model_usd", sa.Float, nullable=False),
    )
    op.create_index("ix_calls_started_at", "calls", ["started_at"])
    op.create_index("ix_calls_user_started", "calls", ["user_id", "started_at"])
    op.create_table(
        "transcript_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer,
                  sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("owner", sa.String(1), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("lang", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("t_offset_s", sa.Float, nullable=False),
    )
    op.create_index("ix_transcript_lines_call_id", "transcript_lines", ["call_id"])


def downgrade() -> None:
    op.drop_table("transcript_lines")
    op.drop_table("calls")
    op.drop_table("auth_sessions")
    op.drop_table("users")
