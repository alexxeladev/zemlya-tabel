from __future__ import annotations

import datetime
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee
    from app.models.positions import EmployeePosition


class NightShift(Base):
    """
    Выход в ночную смену (task_night_shifts_rework).

    Ночная смена — ОТДЕЛЬНАЯ подработка, к графику сотрудника не привязанная:
    выйти в ночь можно в любой день, независимо от того, рабочий он по графику
    или выходной. С дневными часами она СОСУЩЕСТВУЕТ — в одном дне может быть и
    отработанная смена, и ночная отметка; взаимоисключения, как у часов и кодов
    отсутствия, здесь нет.

    Отметка привязана к ПОЗИЦИИ, а не только к человеку: у позиции лежит флаг
    «ночные смены» и отдел, а фонд (и, значит, ставка с лимитом) — свойство
    отдела. Часов у отметки нет — оплачивается сам факт смены по вычисленной
    ставке `фонд отдела / календарные дни месяца`.
    """

    __tablename__ = "night_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("employee_positions.id"), index=True, nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    employee: Mapped[Employee] = relationship("Employee", foreign_keys=[employee_id])
    position: Mapped[EmployeePosition] = relationship("EmployeePosition")
    created_by: Mapped[Employee | None] = relationship(
        "Employee", foreign_keys=[created_by_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "position_id", "work_date", name="uq_night_shift_position_date"
        ),
        # Лимит считается по ОТДЕЛУ за месяц, то есть выборкой по позициям и
        # диапазону дат — этот индекс её и обслуживает.
        Index("ix_night_shift_position_date", "position_id", "work_date"),
    )
