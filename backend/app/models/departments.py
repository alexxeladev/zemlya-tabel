from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.employees import Employee


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

    employees: Mapped[list[Employee]] = relationship("Employee", back_populates="department")
    head_company: Mapped[Company | None] = relationship("Company")
