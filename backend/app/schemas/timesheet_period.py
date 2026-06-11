from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PeriodStatus = Literal["draft", "pending_review", "closed"]


class TimesheetPeriodRead(BaseModel):
    id: int
    department_id: int | None
    department_name: str | None
    year: int
    month: int
    status: PeriodStatus
    submitted_at: datetime | None
    submitted_by_name: str | None
    reviewed_at: datetime | None
    reviewed_by_name: str | None
    closed_at: datetime | None
    closed_by_name: str | None
    can_edit: bool
    can_submit: bool
    can_close: bool
    can_return: bool
    can_reopen: bool


class StatusChangeReason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PeriodTaskRead(BaseModel):
    """Строка inbox-страницы «Задачи» для бухгалтера."""
    period_id: int
    department_id: int | None
    department_name: str
    year: int
    month: int
    status: PeriodStatus
    submitted_by_name: str | None
    submitted_at: datetime | None
    closed_by_name: str | None
    closed_at: datetime | None
    total_hours: int


class TasksResponse(BaseModel):
    pending_review: list[PeriodTaskRead]
    recently_closed: list[PeriodTaskRead]


class AuditLogRead(BaseModel):
    id: int
    actor_id: int
    actor_name: str | None
    entity_type: str
    entity_id: int | None
    action: str
    before: Any
    after: Any
    reason: str | None
    created_at: str
