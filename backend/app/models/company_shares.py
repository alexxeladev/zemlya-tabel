from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.employees import Employee


class EmployeeCompanyShare(Base):
    """
    Управленческое распределение затрат на сотрудника между юрлицами по умолчанию
    (задача 3.11b п.1). Набор «компания → процент» в карточке сотрудника. Сумма
    процентов должна давать ~100%. НЕ связано с часами в табеле.
    """

    __tablename__ = "employee_company_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    employee: Mapped[Employee] = relationship("Employee", foreign_keys=[employee_id])
    company: Mapped[Company] = relationship("Company", foreign_keys=[company_id])

    __table_args__ = (
        UniqueConstraint("employee_id", "company_id", name="uq_emp_company_share"),
    )


class CompanyShareOverride(Base):
    """
    Помесячное переопределение распределения по компаниям (гибрид как у займа,
    задача 3.11b п.1). Строки за (employee, year, month) существуют ТОЛЬКО когда
    бухгалтер правил проценты на конкретный период в ведомости. Если за месяц нет
    ни одной строки — берётся набор по умолчанию из карточки.
    """

    __tablename__ = "company_share_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    employee: Mapped[Employee] = relationship("Employee", foreign_keys=[employee_id])
    company: Mapped[Company] = relationship("Company", foreign_keys=[company_id])
    created_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[created_by_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "company_id", "year", "month",
            name="uq_company_share_override_period",
        ),
    )
