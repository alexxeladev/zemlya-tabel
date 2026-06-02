from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class ScheduleBase(BaseModel):
    name: str
    hours_per_shift: int
    description: Optional[str] = None


class ScheduleCreate(ScheduleBase):
    pass


class ScheduleRead(ScheduleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    hours_per_shift: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
