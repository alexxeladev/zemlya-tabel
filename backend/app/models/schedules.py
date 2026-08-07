from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base

if TYPE_CHECKING:
    from app.models.employees import Employee
    from app.models.positions import EmployeePosition


class _JSONB(TypeDecorator):
    """JSONB on PostgreSQL, JSON elsewhere (SQLite for tests)."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Schedule(Base):
    """
    График работы. Два вида (task_shift_schedules), см. `app.services.work_schedule`:

      - `schedule_type="weekday"` — по дням недели (`work_weekdays`): 5/2, 6/1, вс–чт;
      - `schedule_type="cyclic"`  — скользящий цикл от `cycle_start_date`
        (`cycle_work_days` смен подряд + `cycle_off_days` выходных): 2/2, 3/3.

    Значения `standard`/`shift` — legacy, мигрированы в weekday/cyclic;
    на входе всё равно нормализуются (`normalize_schedule_type`).
    """

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    hours_per_shift: Mapped[int] = mapped_column(nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), default="weekday", nullable=False, server_default="weekday")
    # weekday: рабочие дни недели, 0=Пн … 6=Вс. NULL → выводится из имени «N/M».
    work_weekdays: Mapped[Any | None] = mapped_column(_JSONB, nullable=True)
    # cyclic: анкер фазы + паттерн цикла. Смена 1 и смена 2 — разные анкеры.
    cycle_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cycle_work_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle_off_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    # График назначается ПОЗИЦИИ (task_positions ч.A): у совместителя на разных
    # рабочих местах разные графики.
    positions: Mapped[list[EmployeePosition]] = relationship(
        "EmployeePosition", back_populates="schedule", viewonly=True
    )
    employees: Mapped[list[Employee]] = relationship(
        "Employee",
        secondary="employee_positions",
        primaryjoin="Schedule.id == EmployeePosition.schedule_id",
        secondaryjoin="EmployeePosition.employee_id == Employee.id",
        viewonly=True,
    )
