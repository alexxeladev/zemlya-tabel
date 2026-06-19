from __future__ import annotations

import datetime
import enum
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.departments import Department
    from app.models.schedules import Schedule
    from app.models.timesheet_entries import TimesheetEntry


class EmployeeRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    accountant = "accountant"
    employee = "employee"


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Personal info
    tab_number: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Structure (all nullable)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id"), nullable=True)
    default_company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)

    # Finance (nullable)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Оплата выходных/праздничных часов (правка 3.9-3) — per-employee.
    # weekend_pay_type: "coefficient" → coefficient × часовая ставка;
    #                   "fixed_rate"  → фиксированная ставка за час.
    weekend_pay_type: Mapped[str] = mapped_column(
        String(20), default="coefficient", server_default="coefficient", nullable=False
    )
    weekend_coefficient: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), default=Decimal("1.5"), server_default="1.5", nullable=True
    )
    weekend_fixed_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Займ (задача 3.11a): сумма, срок в месяцах, дата начала погашения.
    # Гасится равными долями (сумма/срок) автоматически; бухгалтер может
    # скорректировать удержание за конкретный месяц (LoanDeduction). Остаток
    # считается на лету = сумма − фактически удержанное (app.services.payout).
    loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loan_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loan_start_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hire_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    dismissal_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    # Auth fields (nullable — only if employee has system access)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Relationships
    department: Mapped[Optional[Department]] = relationship("Department", back_populates="employees")
    schedule: Mapped[Optional[Schedule]] = relationship("Schedule", back_populates="employees")
    default_company: Mapped[Optional[Company]] = relationship("Company", back_populates="employees")
    timesheet_entries: Mapped[list[TimesheetEntry]] = relationship(
        "TimesheetEntry", back_populates="employee", cascade="all, delete-orphan"
    )
