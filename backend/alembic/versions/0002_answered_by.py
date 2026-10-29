"""What picked up the phone: a person, or a machine.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("answered_by", sa.String(24), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "answered_by")
