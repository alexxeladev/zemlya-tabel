from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.employees import Employee


class TimesheetEntry(Base):
    __tablename__ = "timesheet_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    hours: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    employee: Mapped[Employee] = relationship("Employee", back_populates="timesheet_entries")
    company: Mapped[Company] = relationship("Company")

    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", "company_id", name="uq_timesheet_employee_date_company"),
        CheckConstraint("hours >= 0 AND hours <= 24", name="ck_timesheet_hours_range"),
        Index("ix_timesheet_employee_date", "employee_id", "work_date"),
    )
