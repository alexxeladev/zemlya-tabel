"""Журнал изменений справочных данных — чтение (task_audit_log).

Только admin: журнал показывает оклады, роли и доступы, и обзор «кто что менял»
по всей компании — это не то, что должен видеть руководитель отдела.

Пишет журнал не этот роутер, а события сессии в `app/services/reference_audit.py`.
Здесь только выдача с фильтрами и постраничностью.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import require_role
from app.database import get_db
from app.models.employees import Employee
from app.models.reference_changes import SOURCE_LABELS, SOURCES, ReferenceChange
from app.schemas.reference_change import (
    AuditFilterOption,
    AuditFiltersRead,
    ReferenceChangePage,
    ReferenceChangeRead,
)
from app.services.reference_audit import ENTITY_LABELS, FIELD_LABELS

router = APIRouter()

_admin_only = require_role("admin")

# Потолок страницы. Журнал растёт быстро, и «отдай всё» — верный способ
# положить экран через полгода эксплуатации.
MAX_LIMIT = 200


def _to_read(row: ReferenceChange) -> ReferenceChangeRead:
    return ReferenceChangeRead(
        id=row.id,
        created_at=row.created_at,
        actor_id=row.actor_id,
        actor_name=row.actor_name,
        source=row.source,
        source_label=SOURCE_LABELS.get(row.source, row.source),
        operation_id=row.operation_id,
        entity_type=row.entity_type,
        entity_type_label=ENTITY_LABELS.get(row.entity_type, row.entity_type),
        entity_id=row.entity_id,
        entity_label=row.entity_label,
        employee_id=row.employee_id,
        action=row.action,
        field=row.field,
        field_label=FIELD_LABELS.get(row.field) if row.field else None,
        old_value=row.old_value,
        new_value=row.new_value,
    )


def _apply_filters(
    stmt,
    *,
    employee_id: int | None,
    entity_type: str | None,
    entity_id: int | None,
    actor_id: int | None,
    source: str | None,
    operation_id: str | None,
    date_from: datetime.date | None,
    date_to: datetime.date | None,
):
    if employee_id is not None:
        stmt = stmt.where(ReferenceChange.employee_id == employee_id)
    if entity_type:
        stmt = stmt.where(ReferenceChange.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(ReferenceChange.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(ReferenceChange.actor_id == actor_id)
    if source:
        stmt = stmt.where(ReferenceChange.source == source)
    if operation_id:
        stmt = stmt.where(ReferenceChange.operation_id == operation_id)
    if date_from is not None:
        stmt = stmt.where(ReferenceChange.created_at >= _start_of(date_from))
    if date_to is not None:
        # Конец периода ВКЛЮЧИТЕЛЬНО: пользователь, выбравший «по 5 сентября»,
        # ожидает увидеть правки пятого числа, а не пустой экран.
        end = _start_of(date_to) + datetime.timedelta(days=1)
        stmt = stmt.where(ReferenceChange.created_at < end)
    return stmt


def _start_of(day: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time.min)


@router.get("", response_model=ReferenceChangePage)
def list_changes(
    employee_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_id: int | None = None,
    source: str | None = None,
    operation_id: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Лента журнала с фильтрами и постраничностью (новые сверху)."""
    if source is not None and source not in SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестный источник изменения: {source}",
        )

    filters = dict(
        employee_id=employee_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        source=source,
        operation_id=operation_id,
        date_from=date_from,
        date_to=date_to,
    )

    total = db.execute(
        _apply_filters(select(func.count(ReferenceChange.id)), **filters)
    ).scalar_one()

    # Вторичная сортировка по id: у массовой операции все записи пишутся одним
    # INSERT-ом и делят created_at до микросекунды, а страницы без устойчивого
    # порядка начали бы дублировать и терять строки.
    rows = db.execute(
        _apply_filters(select(ReferenceChange), **filters)
        .order_by(ReferenceChange.created_at.desc(), ReferenceChange.id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    return ReferenceChangePage(
        items=[_to_read(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/filters", response_model=AuditFiltersRead)
def audit_filters(
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Значения для выпадашек. Авторы берутся ИЗ САМОГО ЖУРНАЛА, а не из списка
    сотрудников: в фильтре нужны те, кто реально что-то менял."""
    actor_rows = db.execute(
        select(ReferenceChange.actor_id, ReferenceChange.actor_name)
        .where(ReferenceChange.actor_id.is_not(None))
        .distinct()
    ).all()
    seen: dict[int, str] = {}
    for actor_id, actor_name in actor_rows:
        seen.setdefault(actor_id, actor_name or f"#{actor_id}")

    return AuditFiltersRead(
        entity_types=[
            AuditFilterOption(value=k, label=v) for k, v in ENTITY_LABELS.items()
        ],
        sources=[
            AuditFilterOption(value=s, label=SOURCE_LABELS.get(s, s)) for s in SOURCES
        ],
        actors=[
            AuditFilterOption(value=str(k), label=v)
            for k, v in sorted(seen.items(), key=lambda kv: kv[1])
        ],
    )
