from __future__ import annotations

import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ReferenceChangeRead(BaseModel):
    """Одна строка журнала: одно изменённое поле.

    Подписи (`entity_type_label`, `field_label`, `source_label`) собирает бэк:
    словарь полей и так живёт в `app/services/reference_audit.py`, и второй его
    копией на фронте они бы разъехались.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime.datetime

    actor_id: Optional[int] = None
    actor_name: Optional[str] = None

    source: str
    source_label: str

    # Общий id массовой операции: по нему открывается «что сделал этот перенос».
    operation_id: Optional[str] = None

    entity_type: str
    entity_type_label: str
    entity_id: Optional[int] = None
    entity_label: Optional[str] = None

    # Сотрудник, к которому относится запись (у позиции — её владелец).
    employee_id: Optional[int] = None

    action: str
    field: Optional[str] = None
    field_label: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class ReferenceChangePage(BaseModel):
    """Страница журнала. `total` — сколько записей под фильтром всего."""

    items: list[ReferenceChangeRead]
    total: int
    limit: int
    offset: int


class AuditFilterOption(BaseModel):
    value: str
    label: str


class AuditFiltersRead(BaseModel):
    """Справочники для выпадашек экрана: типы сущностей, источники, авторы."""

    entity_types: list[AuditFilterOption]
    sources: list[AuditFilterOption]
    actors: list[AuditFilterOption]
