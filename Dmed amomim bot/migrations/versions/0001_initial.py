"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


feedback_type_enum = postgresql.ENUM("employee", "implementation", name="feedbacktype")
feedback_status_enum = postgresql.ENUM("new", "reviewed", "in_progress", "closed", name="feedbackstatus")


def upgrade() -> None:
    feedback_type_enum.create(op.get_bind(), checkfirst=True)
    feedback_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "institutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=255), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("token_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(op.f("ix_institutions_token"), "institutions", ["token"], unique=True)

    op.create_table(
        "admins",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("telegram_link", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=5), server_default="ru", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
    )
    op.create_index(op.f("ix_users_institution_id"), "users", ["institution_id"])

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.String(length=255), nullable=True),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
    )
    op.create_index(op.f("ix_employees_institution_id"), "employees", ["institution_id"])

    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("feedback_type", feedback_type_enum, nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("ratings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", feedback_status_enum, server_default="new", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_feedback_created_at"), "feedback", ["created_at"])
    op.create_index(op.f("ix_feedback_feedback_type"), "feedback", ["feedback_type"])
    op.create_index(op.f("ix_feedback_institution_id"), "feedback", ["institution_id"])

    op.create_table(
        "link_visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"]),
        sa.UniqueConstraint("user_id", "institution_id", "token", name="uq_link_visit"),
    )


def downgrade() -> None:
    op.drop_table("link_visits")
    op.drop_index(op.f("ix_feedback_institution_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_feedback_type"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_created_at"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_index(op.f("ix_employees_institution_id"), table_name="employees")
    op.drop_table("employees")
    op.drop_index(op.f("ix_users_institution_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("admins")
    op.drop_index(op.f("ix_institutions_token"), table_name="institutions")
    op.drop_table("institutions")
    feedback_status_enum.drop(op.get_bind(), checkfirst=True)
    feedback_type_enum.drop(op.get_bind(), checkfirst=True)

