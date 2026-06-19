from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee


class LoanDeduction(Base):
    """
    Помесячное удержание по займу сотрудника. Запись существует только когда
    бухгалтер вручную скорректировал сумму удержания за месяц (override):
    тогда actual_amount — фактически удержанное, planned_amount — плановая доля
    на момент правки (справочно). Для месяцев без записи удержание считается
    плановой долей на лету (см. app.services.payout).
    """

    __tablename__ = "loan_deductions"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    employee: Mapped[Employee] = relationship("Employee", foreign_keys=[employee_id])
    created_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[created_by_id]
    )

    __table_args__ = (
        UniqueConstraint("employee_id", "year", "month", name="uq_loan_deduction_emp_period"),
    )
