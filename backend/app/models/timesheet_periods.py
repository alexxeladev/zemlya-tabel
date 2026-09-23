from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.departments import Department
    from app.models.employees import Employee


class TimesheetPeriod(Base):
    __tablename__ = "timesheet_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)

    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    department: Mapped[Department | None] = relationship("Department", foreign_keys=[department_id])
    submitted_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[submitted_by_id]
    )
    reviewed_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[reviewed_by_id]
    )
    closed_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[closed_by_id]
    )

    __table_args__ = (
        Index("ix_period_department_year_month", "department_id", "year", "month"),
        # Один период на (отдел, год, месяц). Два ЧАСТИЧНЫХ индекса, а не один
        # обычный: в SQL NULL не равен NULL, и обычный unique пропустил бы
        # сколько угодно периодов группы «Без отдела» за один месяц.
        # Частичные индексы есть только в Postgres, поэтому строятся лишь там
        # (ddl_if — как у уникального логина в models/employees.py); объявлены
        # здесь, чтобы autogenerate не предлагал их удалить: до этапа 4 они жили
        # ТОЛЬКО в миграции 2ebe9fa53315, и каждая автогенерация норовила снести.
        # Проверяются они тоже только на Postgres — tests/test_pg_integration.py.
        Index(
            "uq_period_dept_year_month",
            "department_id", "year", "month",
            unique=True,
            postgresql_where=text("department_id IS NOT NULL"),
        ).ddl_if(dialect="postgresql"),
        Index(
            "uq_period_null_dept_year_month",
            "year", "month",
            unique=True,
            postgresql_where=text("department_id IS NULL"),
        ).ddl_if(dialect="postgresql"),
        CheckConstraint("month >= 1 AND month <= 12", name="ck_period_month_range"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="ck_period_year_range"),
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'closed')", name="ck_period_status"
        ),
    )
