from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee
    from app.models.positions import EmployeePosition


class RowCheck(Base):
    """
    ЛИЧНАЯ отметка «строку проверил» (task_pilot_ux ч.3).

    Табельщик идёт по 70 строкам и теряет место. Отметка — его собственная
    закладка: чужие её не видят и снять не могут (выдача всегда фильтруется по
    `user_id` актора), поэтому это НЕ статус проверки табеля и в workflow
    периода она не участвует.

    Ключ — (пользователь, год, месяц, ПОЗИЦИЯ). Позиция, а не сотрудник:
    строка табеля = рабочее место (task_positions ч.B), у совместителя их
    несколько, в разных отделах и с разными часами — проверяются они порознь.

    Год и месяц входят в ключ, поэтому в новом месяце отметок просто нет:
    ничего никуда не переносится и сбрасывать отдельной задачей нечего.
    """

    __tablename__ = "row_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("employee_positions.id"), index=True, nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    user: Mapped[Employee] = relationship("Employee", foreign_keys=[user_id])
    position: Mapped[EmployeePosition] = relationship("EmployeePosition")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "position_id", "year", "month", name="uq_row_check_user_pos_month"
        ),
        # Выдача табеля берёт отметки одним запросом: «мои за этот месяц».
        Index("ix_row_check_user_month", "user_id", "year", "month"),
    )
