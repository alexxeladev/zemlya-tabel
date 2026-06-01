from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


class _JSONB(TypeDecorator):
    """JSONB on PostgreSQL, JSON elsewhere (SQLite for tests)."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    before: Mapped[Any | None] = mapped_column(_JSONB, nullable=True)
    after: Mapped[Any | None] = mapped_column(_JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.now())

    actor = relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_actor_id", "actor_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )
