from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.departments import Department
    from app.models.schedules import Schedule
    from app.models.users import User


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    tab_number: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), nullable=False)
    default_company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    hire_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    dismissal_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    department: Mapped[Department] = relationship("Department", back_populates="employees")
    schedule: Mapped[Schedule] = relationship("Schedule", back_populates="employees")
    default_company: Mapped[Company] = relationship("Company", back_populates="employees")
    user: Mapped[User | None] = relationship("User", back_populates="employee")
