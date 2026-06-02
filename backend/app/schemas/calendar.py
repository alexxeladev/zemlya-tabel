from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MonthData(BaseModel):
    month: int = Field(ge=1, le=12)
    days: str


class CalendarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    year: int
    months: list[MonthData]
    source: str
    loaded_at: datetime
    workdays_total: int
    short_days_total: int


class CalendarImportRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    months: list[MonthData] = Field(min_length=12, max_length=12)


class DayInfo(BaseModel):
    day: int
    type: Literal["work", "short", "holiday"]
    weekday: int


class MonthSummary(BaseModel):
    year: int
    month: int
    workdays: int
    short_days: int
    norm_hours_8h: int
    days: list[DayInfo]
