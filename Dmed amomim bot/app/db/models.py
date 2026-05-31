import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base


class FeedbackType(str, enum.Enum):
    employee = "employee"
    implementation = "implementation"


class FeedbackStatus(str, enum.Enum):
    new = "new"
    reviewed = "reviewed"
    in_progress = "in_progress"
    closed = "closed"


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(500))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="institution")
    employees: Mapped[list["Employee"]] = relationship(back_populates="institution")
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="institution")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id"))
    telegram_link: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(5), default="ru", server_default="ru")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    institution: Mapped[Institution | None] = relationship(back_populates="users")
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="user")


class Admin(Base):
    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(255))
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    institution: Mapped[Institution] = relationship(back_populates="employees")
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="employee")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    feedback_type: Mapped[FeedbackType] = mapped_column(Enum(FeedbackType), nullable=False)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    ratings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text()), default=list, server_default="{}")
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(FeedbackStatus),
        default=FeedbackStatus.new,
        server_default=FeedbackStatus.new.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="feedback")
    institution: Mapped[Institution] = relationship(back_populates="feedback")
    employee: Mapped[Employee | None] = relationship(back_populates="feedback")


class LinkVisit(Base):
    __tablename__ = "link_visits"
    __table_args__ = (UniqueConstraint("user_id", "institution_id", "token", name="uq_link_visit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
