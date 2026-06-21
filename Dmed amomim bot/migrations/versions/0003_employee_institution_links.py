"""allow employees in multiple institutions

Revision ID: 0003_employee_institution_links
Revises: 0002_feedback_reviewer_identity
Create Date: 2026-06-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_employee_institution_links"
down_revision: str | None = "0002_feedback_reviewer_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employee_institutions",
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.PrimaryKeyConstraint("employee_id", "institution_id"),
    )
    op.create_index(op.f("ix_employee_institutions_institution_id"), "employee_institutions", ["institution_id"])
    op.execute(
        """
        INSERT INTO employee_institutions (employee_id, institution_id)
        SELECT id, institution_id
        FROM employees
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_employee_institutions_institution_id"), table_name="employee_institutions")
    op.drop_table("employee_institutions")
