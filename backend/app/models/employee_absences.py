from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee


class AbsenceKind(str, enum.Enum):
    """Виды отсутствий (буквенные коды формы Т-13)."""

    vacation = "vacation"   # ОТ — отпуск оплачиваемый
    unpaid = "unpaid"       # ДО — отпуск за свой счёт (не оплачивается)
    sick = "sick"           # Б  — больничный
    absent = "absent"       # Н  — неявка / прогул (не оплачивается)


# Код в табеле/Excel ← вид отсутствия. Один источник для API, UI-легенды и Т-13.
ABSENCE_CODES: dict[str, str] = {
    AbsenceKind.vacation.value: "ОТ",
    AbsenceKind.unpaid.value: "ДО",
    AbsenceKind.sick.value: "Б",
    AbsenceKind.absent.value: "Н",
}

# Оплачиваемые виды: считаются отдельными начислениями (отпускные / больничные),
# ДО и Н — только отметка в табеле, денег не дают.
PAID_ABSENCE_KINDS: frozenset[str] = frozenset(
    {AbsenceKind.vacation.value, AbsenceKind.sick.value}
)


class EmployeeAbsence(Base):
    """
    Отсутствие сотрудника в конкретный день (задача «Отсутствия», часть 1).

    Один день = либо часы работы (возможно по нескольким компаниям), либо один
    код отсутствия — не одновременно. Взаимоисключение обеспечивает сервис
    `app.services.absences`: при постановке кода часы этого дня удаляются, при
    вводе часов снимается код.

    Отсутствие НЕ привязано к юрлицу: оно на весь день сотрудника целиком.
    """

    __tablename__ = "employee_absences"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

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
        UniqueConstraint("employee_id", "work_date", name="uq_absence_employee_date"),
        CheckConstraint(
            "kind IN ('vacation', 'unpaid', 'sick', 'absent')",
            name="ck_absence_kind",
        ),
        Index("ix_absence_employee_date", "employee_id", "work_date"),
    )
