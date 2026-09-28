"""
Условия рабочего места для карточки: что действует сегодня, что запланировано,
история по полю (ADR-001, task_terms_per_field).

Карточка показывает условия ГРУППАМИ и только для чтения: изменение вносится
кнопкой «Изменить», то есть явным списком изменений с датой
(`position_terms.record_term_changes`). Здесь собирается то, что для этого нужно
показать, и разбирается то, что присылает диалог:

* `terms_state(db, position)` — по каждой группе значения, действующие СЕГОДНЯ,
  и запланированное дальше («с 01.12.2026 будет 100 000»), плюс плоская история
  по полю. Именно отсутствие «запланированного» на экране и сделало дефект
  27.09.2026 невидимым: карточка показывала будущее как настоящее.
* `apply_terms_input(db, position, changes)` — присланные группы превращаются в
  изменения полей. Здесь и гасятся взаимоисключающие поля: тип оплаты без своей
  базы и коэффициент без вида оплаты бессмысленны, поэтому группа записывается
  целиком.

Значения на входе и на выходе — в том же виде, в каком лежат в базе
(`encode_term_value`): деньги строкой, чтобы `Decimal` не ездил через float.
"""
from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.position_term_changes import encode_term_value
from app.models.position_terms import TERM_FIELDS, TERMS_BEGINNING
from app.models.positions import PAY_TYPE_BASE_FIELD, EmployeePosition
from app.models.schedules import Schedule
from app.schemas.position import (
    PositionTermsStateRead,
    TermChangeRead,
    TermGroupStateRead,
    TermPlannedRead,
    TermsChangeInput,
)
from app.services.position_terms import (
    GROUP_OF_FIELD,
    TERM_GROUP_LABELS,
    TERM_GROUPS,
    TermChange,
    TermsValueError,
    default_effective_from,
    effective_label,
    record_term_changes,
    terms_on,
)

#: Поле «вид оплаты» → поля значений, зависящие от него. Гасится то, что не
#: соответствует выбранному виду: у коэффициента не должно остаться фикс-ставки.
_BY_KIND: dict[str, dict[str, str]] = {
    "weekend_pay_type": {
        "coefficient": "weekend_coefficient", "fixed_rate": "weekend_fixed_rate",
    },
    "holiday_pay_type": {
        "coefficient": "holiday_coefficient", "fixed_rate": "holiday_fixed_rate",
    },
}


def _field_label(field: str) -> str:
    from app.services.reference_audit import FIELD_LABELS

    return FIELD_LABELS.get(field, field)


def group_change_values(group: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Значения ГРУППЫ по присланному диалогу — все поля группы разом.

    Взаимоисключающие поля гасятся здесь, а не «где-нибудь потом»: значение
    чужого вида оплаты, оставшееся в истории, молча увело бы расчёт не на ту
    базу (то же правило, что `PAY_TYPE_BASE_FIELD` в общем сохранении).
    """
    fields = TERM_GROUPS.get(group)
    if fields is None:
        raise TermsValueError(f"Неизвестная группа условий: {group}")

    if group == "pay":
        pay_type = payload.get("pay_type")
        if pay_type not in PAY_TYPE_BASE_FIELD:
            raise TermsValueError("Не выбран тип оплаты")
        base = PAY_TYPE_BASE_FIELD[pay_type]
        values: dict[str, Any] = {"pay_type": pay_type}
        for field in ("rate", "shift_rate", "hour_rate"):
            values[field] = payload.get(base) if field == base else None
        return values

    for kind_field, by_kind in _BY_KIND.items():
        if kind_field not in fields:
            continue
        kind = payload.get(kind_field)
        if kind not in by_kind:
            raise TermsValueError(f"Не выбран вид оплаты: {_field_label(kind_field)}")
        values = dict.fromkeys(fields)  # чужое значение вида оплаты гасится
        values[kind_field] = kind
        values[by_kind[kind]] = payload.get(by_kind[kind])
        return values

    if group == "official":
        is_official = payload.get("is_official")
        if is_official is None:
            raise TermsValueError("Не указано, официально ли устроен сотрудник")
        # Признак выключен — зарплаты нет: два состояния одного факта
        # разъехались бы (правило модели официальной зарплаты).
        return {
            "is_official": bool(is_official),
            "official_salary": payload.get("official_salary") if is_official else None,
        }

    return {field: payload.get(field) for field in fields}


def apply_terms_input(
    db: Session, position: EmployeePosition, changes: list[TermsChangeInput],
) -> None:
    """Присланные изменения групп → изменения полей условий."""
    record_term_changes(db, position, [
        TermChange(
            effective_from=change.effective_from,
            values=group_change_values(
                change.group, change.model_dump(exclude={"group", "effective_from"})
            ),
        )
        for change in changes
    ])


def _encoded(terms, field: str) -> Any:
    return encode_term_value(field, getattr(terms, field, None))


def _schedule_names(db: Session, ids: set[int]) -> dict[str, str]:
    """Названия графиков, встречающихся в условиях и истории — одним запросом.

    Историю могли завести на график, который позже сняли с учёта: в списке
    формы его нет, а в истории он обязан читаться названием, а не «#43».
    """
    if not ids:
        return {}
    rows = db.query(Schedule.id, Schedule.name).filter(Schedule.id.in_(ids)).all()
    return {str(sid): name for sid, name in rows}


def terms_state(
    db: Session, position: EmployeePosition, today: datetime.date | None = None,
) -> PositionTermsStateRead:
    """Условия рабочего места для карточки: сегодня, запланированное, история."""
    today = today or datetime.date.today()
    current_terms = terms_on(position, today)
    schedule_ids: set[int] = set()

    # ── история по полю: свежие сверху ──
    changes = sorted(
        position.term_changes,
        key=lambda c: (c.effective_from, c.field),
        reverse=True,
    )
    history: list[TermChangeRead] = []
    for change in changes:
        if change.field not in TERM_FIELDS:  # поле сняли с версионирования
            continue
        if change.field == "schedule_id" and change.value is not None:
            schedule_ids.add(int(change.value))
        group = GROUP_OF_FIELD[change.field]
        history.append(TermChangeRead(
            field=change.field,
            field_label=_field_label(change.field),
            group=group,
            group_label=TERM_GROUP_LABELS[group],
            effective_from=None if change.is_base else change.effective_from,
            effective_label=effective_label(change.effective_from),
            value=change.value,
            created_by_name=change.created_by_name,
            created_at=change.created_at,
        ))

    # ── группы: значения сегодня + запланированное дальше ──
    groups: list[TermGroupStateRead] = []
    for group, fields in TERM_GROUPS.items():
        current = {f: _encoded(current_terms, f) for f in fields}
        if current.get("schedule_id") is not None:
            schedule_ids.add(int(current["schedule_id"]))
        planned_by_date: dict[datetime.date, dict[str, Any]] = {}
        for change in position.term_changes:
            if change.field in fields and change.effective_from > today:
                planned_by_date.setdefault(change.effective_from, {})[change.field] = (
                    change.value
                )
        planned = [
            TermPlannedRead(
                effective_from=day,
                effective_label=effective_label(day),
                fields=[f for f in fields if f in values],
                values=values,
            )
            for day, values in sorted(planned_by_date.items())
        ]
        groups.append(TermGroupStateRead(
            group=group,
            label=TERM_GROUP_LABELS[group],
            fields=list(fields),
            current=current,
            planned=planned,
        ))

    return PositionTermsStateRead(
        position_id=position.id,
        today=today,
        default_effective_from=default_effective_from(today),
        beginning=TERMS_BEGINNING,
        groups=groups,
        changes=history,
        schedule_names=_schedule_names(db, schedule_ids),
    )
