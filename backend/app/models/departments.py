from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.department_managers import department_managers

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.employees import Employee
    from app.models.positions import EmployeePosition


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    # Головная компания отдела (task_org_structure ч.1) — ЯРЛЫК ДЛЯ НАВИГАЦИИ:
    # в какой компании отдел числится в дереве оргструктуры. НЕ ограничивает,
    # на какие юрлица работают сотрудники: часы и распределение процентов
    # остаются мультикомпанийными и на это поле не смотрят.
    head_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Сотрудники отдела — через ПОЗИЦИИ (task_positions ч.A): в отделе числится
    # не человек, а его рабочее место, и у совместителя они в разных отделах.
    # viewonly — состав отдела меняется правкой позиции, не этой коллекцией.
    positions: Mapped[list[EmployeePosition]] = relationship(
        "EmployeePosition", back_populates="department", viewonly=True
    )
    employees: Mapped[list[Employee]] = relationship(
        "Employee",
        secondary="employee_positions",
        primaryjoin="Department.id == EmployeePosition.department_id",
        secondaryjoin="EmployeePosition.employee_id == Employee.id",
        viewonly=True,
    )
    head_company: Mapped[Company | None] = relationship("Company")

    # Менеджеры отдела (task_org_structure ч.2). Отдельно от `employees`:
    # руководить отделом можно, не числясь в нём.
    managers: Mapped[list[Employee]] = relationship(
        "Employee",
        secondary=department_managers,
        back_populates="managed_departments",
    )
