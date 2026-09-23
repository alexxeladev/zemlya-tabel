from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.position_terms import TERMS_BEGINNING

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
    # Распределение задаётся на ПОЗИЦИЮ (task_positions ч.A): у совместителя
    # каждое рабочее место разносится по юрлицам по-своему.
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("employee_positions.id"), index=True, nullable=True
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    # С какого месяца действует набор (task_stage3_historicity): всегда 1-е
    # число — итог месяца один, делить месяц по процентам нечего (решение
    # заказчика). Набор = все строки позиции с одной датой; действует набор с
    # наибольшей датой не позже 1-го числа рассчитываемого месяца. Первый набор —
    # с начала времён (`TERMS_BEGINNING`). «Снять распределение» с месяца —
    # набор из нулевых долей: «задано» означает хотя бы одну долю > 0.
    effective_from: Mapped[datetime.date] = mapped_column(
        Date, nullable=False, default=TERMS_BEGINNING, server_default="1900-01-01"
    )

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    employee: Mapped[Employee] = relationship("Employee", foreign_keys=[employee_id])
    company: Mapped[Company] = relationship("Company", foreign_keys=[company_id])

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "position_id", "company_id", "effective_from",
            name="uq_emp_company_share",
        ),
    )


class DepartmentCompanyShare(Base):
    """
    Распределение затрат по юрлицам по умолчанию НА УРОВНЕ ОТДЕЛА
    (task_distribution_v2 ч.3). Наследуется сотрудниками отдела, у которых нет
    своего распределения (ни помесячного переопределения, ни процентов в карточке).
    Каскад: месячный % > карточка > отдел > авто по часам.
    """

    __tablename__ = "department_company_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    company: Mapped[Company] = relationship("Company", foreign_keys=[company_id])

    __table_args__ = (
        UniqueConstraint("department_id", "company_id", name="uq_dept_company_share"),
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
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("employee_positions.id"), index=True, nullable=True
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Шесть знаков после запятой, а не три, как у остальных наборов:
    #: перенос отдела в другую компанию (task_move_department) замораживает
    #: расклад закрытых месяцев через этот override, вычисляя проценты из уже
    #: посчитанных сумм. При трёх знаках обратный пересчёт расходился с
    #: исходными суммами на единицы рублей — то есть «заморозка» сама двигала бы
    #: историю. Ручной ввод в ведомости остаётся двухзначным.
    percent: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)

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
            "employee_id", "position_id", "company_id", "year", "month",
            name="uq_company_share_override_period",
        ),
    )
