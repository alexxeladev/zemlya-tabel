from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee
    from app.models.positions import EmployeePosition


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(5), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Основная компания задаётся у ПОЗИЦИИ (task_positions ч.A), не у человека.
    positions: Mapped[list[EmployeePosition]] = relationship(
        "EmployeePosition", back_populates="company", viewonly=True
    )
    employees: Mapped[list[Employee]] = relationship(
        "Employee",
        secondary="employee_positions",
        primaryjoin="Company.id == EmployeePosition.company_id",
        secondaryjoin="EmployeePosition.employee_id == Employee.id",
        viewonly=True,
    )
