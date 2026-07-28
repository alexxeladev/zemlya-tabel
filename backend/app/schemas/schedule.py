from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.services.work_schedule import (
    SCHEDULE_TYPE_CYCLIC,
    SCHEDULE_TYPE_WEEKDAY,
    normalize_schedule_type,
)


def validate_cycle_fields(
    schedule_type: str,
    cycle_start_date: date | None,
    cycle_work_days: int | None,
    cycle_off_days: int | None,
) -> None:
    """Общая проверка полей цикла — для создания и для правки (после слияния)."""
    if normalize_schedule_type(schedule_type) != SCHEDULE_TYPE_CYCLIC:
        return
    if cycle_start_date is None:
        raise ValueError("Для сменного графика нужна дата начала цикла")
    if not cycle_work_days or cycle_work_days < 1:
        raise ValueError("Смен подряд должно быть не меньше 1")
    if cycle_off_days is None or cycle_off_days < 1:
        raise ValueError("Выходных подряд должно быть не меньше 1")


class ScheduleBase(BaseModel):
    """
    График работы (task_shift_schedules).

    `schedule_type`:
      - `weekday` — по дням недели, рабочие дни в `work_weekdays` (0=Пн … 6=Вс);
        пусто → выводятся из имени «N/M» (5/2 → Пн–Пт, 6/1 → Пн–Сб).
      - `cyclic` — скользящий цикл: `cycle_start_date` (анкер фазы) +
        `cycle_work_days`/`cycle_off_days` (2/2, 3/3). Смена 1 и смена 2 —
        два графика с разными стартовыми датами.

    Legacy-значения `standard`/`shift` принимаются и нормализуются.
    """

    name: str
    hours_per_shift: int
    schedule_type: str = SCHEDULE_TYPE_WEEKDAY
    work_weekdays: Optional[list[int]] = None
    cycle_start_date: Optional[date] = None
    cycle_work_days: Optional[int] = None
    cycle_off_days: Optional[int] = None
    description: Optional[str] = None

    @field_validator("schedule_type")
    @classmethod
    def _normalize_type(cls, v: str) -> str:
        return normalize_schedule_type(v)

    @field_validator("work_weekdays")
    @classmethod
    def _check_weekdays(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        days = sorted({int(x) for x in v})
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("Дни недели задаются числами 0 (Пн) … 6 (Вс)")
        return days or None


class ScheduleCreate(ScheduleBase):
    @model_validator(mode="after")
    def _check_cycle(self):
        # Только на входе: ScheduleRead такой проверки не делает, иначе
        # неполный legacy-график из БД нельзя было бы даже прочитать (его
        # починит админ через редактор).
        validate_cycle_fields(
            self.schedule_type,
            self.cycle_start_date,
            self.cycle_work_days,
            self.cycle_off_days,
        )
        return self


class ScheduleRead(ScheduleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    hours_per_shift: Optional[int] = None
    schedule_type: Optional[str] = None
    work_weekdays: Optional[list[int]] = None
    cycle_start_date: Optional[date] = None
    cycle_work_days: Optional[int] = None
    cycle_off_days: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("schedule_type")
    @classmethod
    def _normalize_type(cls, v: str | None) -> str | None:
        return None if v is None else normalize_schedule_type(v)


# ── Превью рабочих дней месяца ────────────────────────────────────────────────

class SchedulePreviewRequest(BaseModel):
    """Превью строится по ещё не сохранённому графику — форма редактора."""

    year: int
    month: int
    hours_per_shift: int = 8
    schedule_type: str = SCHEDULE_TYPE_WEEKDAY
    name: Optional[str] = None
    work_weekdays: Optional[list[int]] = None
    cycle_start_date: Optional[date] = None
    cycle_work_days: Optional[int] = None
    cycle_off_days: Optional[int] = None

    @field_validator("schedule_type")
    @classmethod
    def _normalize_type(cls, v: str) -> str:
        return normalize_schedule_type(v)


class SchedulePreviewDay(BaseModel):
    day: int
    work_date: date
    weekday: int
    is_work_day: bool
    hours: int
    is_holiday: bool
    is_short_day: bool


class SchedulePreview(BaseModel):
    year: int
    month: int
    days: list[SchedulePreviewDay]
    work_days: int
    norm_hours: int
    has_calendar: bool
    issue: Optional[str] = None
