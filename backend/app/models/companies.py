from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, func
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
    # Короткое название для узких колонок («Комфорт-Эксплуатация» → «К-Эксплуат.»).
    # Пусто — короткое имя выводится из name (см. app/services/company_order.py).
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Порядок перечисления компаний — единый для всех представлений и выгрузок.
    # Сортировать только через app/services/company_order.py, не по id/name.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    @property
    def display_name(self) -> str:
        """Короткое название для колонок и подписей (см. services/company_order)."""
        from app.services.company_order import company_display_name

        return company_display_name(self)

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
