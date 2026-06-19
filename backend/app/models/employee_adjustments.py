from __future__ import annotations

import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee


class AdjustmentKind(str, enum.Enum):
    premium = "premium"          # премия (+ к выплате)
    kpi = "kpi"                  # KPI (+ к выплате)
    advance = "advance"          # аванс (− из выплаты, разовое удержание)


class EmployeeAdjustment(Base):
    """
    Денежная корректировка выплаты сотрудника за конкретный период.
    Премия и KPI прибавляются, аванс — удержание. У каждой записи обязательное
    обоснование (reason). За месяц у сотрудника может быть несколько записей —
    они суммируются по типу.
    """

    __tablename__ = "employee_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(server_default=func.now())

    employee: Mapped[Employee] = relationship("Employee", foreign_keys=[employee_id])
    created_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[created_by_id]
    )

    __table_args__ = (
        Index("ix_adjustment_emp_year_month", "employee_id", "year", "month"),
    )
