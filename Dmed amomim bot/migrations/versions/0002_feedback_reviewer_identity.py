"""add reviewer identity fields

Revision ID: 0002_feedback_reviewer_identity
Revises: 0001_initial
Create Date: 2026-06-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_feedback_reviewer_identity"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feedback", sa.Column("reviewer_full_name", sa.String(length=255), nullable=True))
    op.add_column("feedback", sa.Column("reviewer_phone", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_feedback_reviewer_phone"), "feedback", ["reviewer_phone"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_reviewer_phone"), table_name="feedback")
    op.drop_column("feedback", "reviewer_phone")
    op.drop_column("feedback", "reviewer_full_name")
