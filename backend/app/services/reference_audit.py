"""Журнал изменений справочных данных: кто, когда, какое поле, было → стало.

Единственное место, где решается, ЧТО попадает в журнал и КАК выглядит запись.

## Почему через события сессии, а не вызовами из роутеров

Записывать в каждом обработчике «а теперь залогируй оклад» — значит забыть это
в первом же новом месте, и журнал станет врать умолчанием. Поэтому запись висит
на `before_flush`/`after_flush` сессии SQLAlchemy: логируется всё, что реально
уходит в базу, независимо от того, каким кодом это записали.

Отсюда же — **бесплатное решение главной ловушки задачи**. Старый плоский API
(`emp.rate`, `emp.department_id`) — это compat-аксессоры, которые пишут в
ОСНОВНУЮ ПОЗИЦИЮ (`app/models/employees.py`). В сессии «грязным» оказывается
именно `EmployeePosition`, а не `Employee`, поэтому в журнал сама собой попадает
настоящая затронутая позиция. Никакого разбора «а это точно основная?» —
логируется то, что действительно меняется. У совместителя видно, какое из его
рабочих мест поехало.

## Почему запись не замедляет сохранение

- **Ни одного запроса на строку.** Расхождения берутся из истории атрибутов
  (`attributes.get_history`) — это уже загруженное состояние объекта. Имена для
  ссылок (отдел, компания, график) ищутся сперва в identity map сессии, и лишь
  недостающие добираются ОДНИМ запросом на справочник на флаш. Ходить в базу за
  именем на каждую строку нельзя: на массовом переносе это сотни запросов.
- **Один INSERT на флаш** (`executemany` по Core-таблице), а не ORM-объект на
  каждую строку.
- **Пишутся только реальные расхождения**: сохранение формы без правок не даёт
  ни одной записи, потому что в истории атрибутов ничего нет.

## Чего здесь нет

Часы, отсутствия, ночные смены, премии и статусы периодов — операционные данные,
их много и они меняются постоянно. Они остаются в старом `audit_log`
(`app/core/audit.py`), который эта задача не трогает.

**Миграции в журнал не попадают** и не должны: Alembic правит данные Core-ом,
мимо ORM-сессии, событий там нет. Это осознанное ограничение — в диагностике
миграции отличаются тем, что их следов в журнале как раз НЕТ.
"""

from __future__ import annotations

import datetime
import json
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, attributes

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.reference_changes import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_UPDATE,
    SOURCE_SYSTEM,
    SOURCE_UI,
    ReferenceChange,
)
from app.models.schedules import Schedule

# ── Что под аудитом ───────────────────────────────────────────────────────────
# Список полей ЯВНЫЙ, а не «все колонки»: иначе в журнал полезут updated_at,
# служебные счётчики и прочий шум, и читать его станет невозможно. Новое
# справочное поле надо дописать сюда — иначе его правки не видно.

# Поля-ссылки: в журнал пишем имя, а не голый id.
_REFERENCE_FIELDS: dict[str, Any] = {
    "department_id": Department,
    "company_id": Company,
    "head_company_id": Company,
    "schedule_id": Schedule,
    "default_company_id": Company,
}

AUDITED_FIELDS: dict[type, tuple[str, ...]] = {
    Employee: (
        "full_name",
        "tab_number",
        # Легаси-колонка «должность» на сотруднике. Живёт ОТДЕЛЬНО от
        # `EmployeePosition.title` и показывается в дереве оргструктуры и в
        # списке сотрудников — именно из-за неё «должность не та». Под аудитом.
        "position",
        "email",
        "role",
        "hire_date",
        "dismissal_date",
        "is_active",
        "is_system_admin",
    ),
    EmployeePosition: (
        "title",
        "pay_type",
        "rate",
        "shift_rate",
        "hour_rate",
        "schedule_id",
        "department_id",
        "company_id",
        "weekend_pay_type",
        "weekend_coefficient",
        "weekend_fixed_rate",
        "holiday_pay_type",
        "holiday_coefficient",
        "holiday_fixed_rate",
        "overtime_coefficient",
        "has_night_shifts",
        "is_primary",
        "is_active",
    ),
    Department: (
        "name",
        "code",
        "head_company_id",
        "night_shift_fund",
        "uses_quantity_distribution",
        "quantity_metric_name",
        "quantity_part1_name",
        "quantity_part2_name",
        "is_active",
    ),
    Company: ("name", "code", "short_name", "inn", "sort_order", "is_active"),
    Schedule: (
        "name",
        "schedule_type",
        "hours_per_shift",
        "work_weekdays",
        "cycle_start_date",
        "cycle_work_days",
        "cycle_off_days",
        "description",
        "is_active",
    ),
}

ENTITY_TYPES: dict[type, str] = {
    Employee: "employee",
    EmployeePosition: "employee_position",
    Department: "department",
    Company: "company",
    Schedule: "schedule",
}

# Назначение ответственных (менеджеры и табельщики отдела) — связь many-to-many,
# своей модели у неё нет: изменения видны как история коллекции на отделе.
MANAGERS_ENTITY = "department_managers"

# Наборы процентов распределения по юрлицам. Своего событийного аудита у них
# быть НЕ МОЖЕТ: оба эндпойнта переписывают набор целиком — Core-DELETE всех
# строк и вставка новых, — а Core-DELETE идёт мимо ORM и её событий. Ловя только
# вставки, журнал показывал бы «добавлено 50%» и молчал бы о снятом — то есть
# врал бы. Поэтому набор пишется ОДНОЙ записью «было → стало» явным вызовом
# `record_change` из обработчика.
EMPLOYEE_SHARES_ENTITY = "employee_shares"
DEPARTMENT_SHARES_ENTITY = "department_shares"

ENTITY_LABELS: dict[str, str] = {
    "employee": "Сотрудник",
    "employee_position": "Рабочее место",
    "department": "Отдел",
    "company": "Юрлицо",
    "schedule": "График работы",
    EMPLOYEE_SHARES_ENTITY: "Распределение в карточке",
    DEPARTMENT_SHARES_ENTITY: "Распределение отдела",
    MANAGERS_ENTITY: "Ответственные отдела",
}

# Подписи полей для экрана: «rate» ничего не говорит бухгалтеру.
FIELD_LABELS: dict[str, str] = {
    "full_name": "ФИО",
    "tab_number": "Табельный номер",
    "position": "Должность (карточка)",
    "email": "Email (доступ)",
    "role": "Роль",
    "hire_date": "Дата приёма",
    "dismissal_date": "Дата увольнения",
    "is_active": "Активен",
    "is_system_admin": "Системный администратор",
    "title": "Должность",
    "pay_type": "Тип оплаты",
    "rate": "Оклад",
    "shift_rate": "Ставка за смену",
    "hour_rate": "Ставка за час",
    "schedule_id": "График",
    "department_id": "Отдел",
    "company_id": "Юрлицо",
    "weekend_pay_type": "Выходные: тип оплаты",
    "weekend_coefficient": "Коэффициент выходных",
    "weekend_fixed_rate": "Выходные: фикс. ставка",
    "holiday_pay_type": "Праздничные: тип оплаты",
    "holiday_coefficient": "Коэффициент праздничных",
    "holiday_fixed_rate": "Праздничные: фикс. ставка",
    "overtime_coefficient": "Коэффициент переработки",
    "has_night_shifts": "Ночные смены",
    "is_primary": "Основная позиция",
    "name": "Название",
    "code": "Код",
    "head_company_id": "Головная компания",
    "night_shift_fund": "Фонд ночных смен",
    "uses_quantity_distribution": "Распределение по показателю",
    "quantity_metric_name": "Название показателя",
    "quantity_part1_name": "Показатель: часть 1",
    "quantity_part2_name": "Показатель: часть 2",
    "short_name": "Короткое название",
    "inn": "ИНН",
    "sort_order": "Порядок в списке",
    "schedule_type": "Тип графика",
    "hours_per_shift": "Часов в смене",
    "work_weekdays": "Рабочие дни недели",
    "cycle_start_date": "Начало цикла",
    "cycle_work_days": "Рабочих дней цикла",
    "cycle_off_days": "Выходных дней цикла",
    "description": "Описание",
    "percent": "Процент",
    "shares": "Распределение по юрлицам",
    "managers": "Ответственные",
}


# ── Старое значение обязано быть доступно в момент записи ─────────────────────

def _force_old_value_loading() -> None:
    """Заставить SQLAlchemy держать прежнее значение аудируемых полей.

    По умолчанию присвоение в НЕЗАГРУЖЕННЫЙ атрибут (после commit объекты
    истекают) старое значение не поднимает, и в истории остаётся только «стало».
    Журнал с пустым «было» бесполезен ровно там, где он нужен, — поэтому на
    каждое аудируемое поле вешается пустой слушатель `set` с
    `active_history=True`: он не делает ничего, но включает подгрузку прежнего
    значения.

    Цена — тот самый SELECT, который всё равно случился бы при первом чтении
    поля; в обработчиках объект к моменту правки уже прочитан, поэтому в
    обычном пути дополнительных запросов не появляется.
    """

    def _keep(target, value, oldvalue, initiator):  # noqa: ANN001 — сигнатура события
        return value

    for model, fields in AUDITED_FIELDS.items():
        for field in fields:
            event.listen(
                getattr(model, field), "set", _keep, active_history=True, retval=True
            )


_force_old_value_loading()


# ── Контекст: кто пишет и каким путём ─────────────────────────────────────────
# Живёт в `Session.info`, а не в contextvars: сессия и так одна на запрос, а
# contextvar, выставленный внутри синхронного обработчика FastAPI (он идёт в
# threadpool), до следующего вызова не доживает — на этом легко обжечься.

_ACTOR_ID = "audit_actor_id"
_ACTOR_NAME = "audit_actor_name"
_SOURCE = "audit_source"
_OPERATION = "audit_operation_id"


def set_audit_actor(db: Session, actor: Employee | None, source: str = SOURCE_UI) -> None:
    """Запомнить на сессии, кто и каким путём вносит изменения."""
    db.info[_ACTOR_ID] = actor.id if actor is not None else None
    db.info[_ACTOR_NAME] = actor.full_name if actor is not None else None
    db.info.setdefault(_SOURCE, source)


@contextmanager
def audit_operation(db: Session, source: str, operation_id: str | None = None):
    """Пометить блок как массовую операцию: общий источник и общий id.

    Все записи внутри получают один `operation_id`, поэтому «что сделал этот
    перенос» открывается одним списком. Прежние значения восстанавливаются —
    вложенность и повторный вызов в одном запросе безопасны.
    """
    prev_source = db.info.get(_SOURCE)
    prev_op = db.info.get(_OPERATION)
    db.info[_SOURCE] = source
    db.info[_OPERATION] = operation_id or str(uuid.uuid4())
    try:
        yield db.info[_OPERATION]
    finally:
        db.info[_SOURCE] = prev_source
        db.info[_OPERATION] = prev_op


def current_operation_id(db: Session) -> str | None:
    return db.info.get(_OPERATION)


# ── Представление значений ────────────────────────────────────────────────────

def _plain(value: Any) -> str | None:
    """Значение поля строкой — так, как его прочитает человек."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, Decimal):
        # 1.50 и 1.5 — одно и то же число; хвост нулей в журнале только мешает.
        normalized = value.normalize()
        return format(normalized, "f")
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _lookup_label(session: Session, model: type, pk: Any) -> str | None:
    """Имя справочной строки — ТОЛЬКО из identity map, без запроса в базу.

    Запись аудита не имеет права стоить лишнего SELECT-а: на массовом переносе
    это сотни запросов в самой горячей точке. Объект почти всегда уже загружен
    тем же обработчиком; не загружен — обойдёмся идентификатором.
    """
    if pk is None:
        return None
    try:
        obj = session.identity_map.get(inspect(model).identity_key_from_primary_key([pk]))
        if obj is None:
            return None
        # Чтение ДОЛЖНО быть внутри try: объект из identity map может оказаться
        # истёкшим или уже удалённым, и обращение к полю бросит ObjectDeletedError
        # прямо посреди сохранения. Журнал не имеет права ронять запись данных —
        # без имени он останется читаемым, без сохранения пользователь останется
        # без работы.
        return getattr(obj, "name", None) or getattr(obj, "full_name", None)
    except Exception:  # noqa: BLE001 — на любой неожиданности просто нет имени
        return None


# Ссылки, чьё имя не нашлось в сессии, помечаются здесь и добираются ОДНИМ
# запросом на справочник уже после флаша (см. `_resolve_pending_refs`).
_UNRESOLVED = "audit_unresolved_refs"


def _ref_text(pk: Any) -> str:
    return f"#{pk}"


def _render(session: Session, field: str, value: Any, row: dict, slot: str) -> str | None:
    """Значение для журнала: у ссылок — имя, иначе как есть.

    Имя ищется только в identity map (без запроса). Не нашли — пишем `#42` и
    запоминаем ссылку: недостающие имена доберутся одним запросом на справочник
    после флаша, а не по запросу на строку.
    """
    model = _REFERENCE_FIELDS.get(field)
    if model is not None and value is not None:
        label = _lookup_label(session, model, value)
        if label:
            return f"{label} ({_ref_text(value)})"
        session.info.setdefault(_UNRESOLVED, []).append((model, value, row, slot))
        return _ref_text(value)
    return _plain(value)


def _resolve_pending_refs(session: Session) -> None:
    """Добрать имена справочников, которых не было в сессии.

    ОДИН запрос на справочник на флаш, а не на строку: массовый перенос 200
    позиций добирает название компании одним SELECT-ом, а не двумя сотнями.
    Не нашли и в базе (строку успели удалить) — в журнале остаётся `#42`,
    и это честнее выдуманного имени.
    """
    pending = session.info.pop(_UNRESOLVED, None)
    if not pending:
        return
    by_model: dict[type, set[Any]] = {}
    for model, pk, _row, _slot in pending:
        by_model.setdefault(model, set()).add(pk)

    names: dict[tuple[type, Any], str] = {}
    for model, pks in by_model.items():
        for pk, name in session.execute(
            select(model.id, model.name).where(model.id.in_(pks))
        ).all():
            names[(model, pk)] = name

    for model, pk, row, slot in pending:
        name = names.get((model, pk))
        if name:
            row[slot] = f"{name} ({_ref_text(pk)})"


def _entity_label(session: Session, obj: Any) -> str | None:
    """Человекочитаемое имя сущности — сохраняется в записи навсегда."""
    if isinstance(obj, Employee):
        return obj.full_name
    if isinstance(obj, EmployeePosition):
        # Рабочее место без имени сотрудника не опознать, а сотрудник тут почти
        # всегда уже загружен (его карточку и правят).
        who = _lookup_label(session, Employee, obj.employee_id)
        title = obj.title or "без должности"
        return f"{who} / {title}" if who else title
    return getattr(obj, "name", None)


def _employee_id_of(obj: Any) -> int | None:
    """К какому сотруднику относится запись (для истории в карточке)."""
    if isinstance(obj, Employee):
        return obj.id
    if isinstance(obj, EmployeePosition):
        return obj.employee_id
    return None


# ── Сбор изменений ────────────────────────────────────────────────────────────

_PENDING = "audit_pending_rows"


def _base_row(session: Session, obj: Any, action: str) -> dict:
    return {
        "actor_id": session.info.get(_ACTOR_ID),
        "actor_name": session.info.get(_ACTOR_NAME),
        "source": session.info.get(_SOURCE) or SOURCE_SYSTEM,
        "operation_id": session.info.get(_OPERATION),
        "entity_type": ENTITY_TYPES[type(obj)],
        "entity_label": _entity_label(session, obj),
        "employee_id": _employee_id_of(obj),
        "action": action,
        "field": None,
        "old_value": None,
        "new_value": None,
    }


def _changed_fields(obj: Any, fields: Iterable[str]) -> list[tuple[str, Any, Any]]:
    """Реальные расхождения было → стало по истории атрибутов.

    Только то, что действительно поменялось: присвоение того же значения
    истории не создаёт, поэтому сохранение формы без правок записей не даёт.
    """
    out: list[tuple[str, Any, Any]] = []
    for field in fields:
        history = attributes.get_history(obj, field)
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if old == new:
            continue
        out.append((field, old, new))
    return out


def _managers_rows(session: Session, dept: Department) -> list[dict]:
    """Назначение ответственных: кого добавили, кого сняли."""
    history = attributes.get_history(dept, "managers")
    if not history.has_changes():
        return []
    rows: list[dict] = []
    for emp in history.added:
        row = _base_row(session, dept, ACTION_UPDATE)
        row.update(
            entity_type=MANAGERS_ENTITY,
            entity_id=dept.id,
            field="managers",
            old_value=None,
            new_value=f"{emp.full_name} (#{emp.id})",
        )
        rows.append(row)
    for emp in history.deleted:
        row = _base_row(session, dept, ACTION_UPDATE)
        row.update(
            entity_type=MANAGERS_ENTITY,
            entity_id=dept.id,
            field="managers",
            old_value=f"{emp.full_name} (#{emp.id})",
            new_value=None,
        )
        rows.append(row)
    return rows


@event.listens_for(Session, "before_flush")
def _collect_reference_changes(session: Session, flush_context, instances) -> None:
    """Собрать расхождения ДО флаша, пока история атрибутов ещё жива."""
    pending: list[dict] = session.info.setdefault(_PENDING, [])

    for obj in session.new:
        if type(obj) not in ENTITY_TYPES:
            continue
        row = _base_row(session, obj, ACTION_CREATE)
        # id новой строки появится только после флаша — дозаполним там же.
        row["_obj"] = obj
        pending.append(row)

    for obj in session.dirty:
        if type(obj) not in ENTITY_TYPES:
            continue
        if not session.is_modified(obj, include_collections=False):
            # Объект попал в dirty, но ни одно поле не изменилось: правка формы
            # «сохранить без изменений» не должна плодить записи.
            if isinstance(obj, Department):
                pending.extend(_managers_rows(session, obj))
            continue
        for field, old, new in _changed_fields(obj, AUDITED_FIELDS[type(obj)]):
            row = _base_row(session, obj, ACTION_UPDATE)
            row["field"] = field
            row["old_value"] = _render(session, field, old, row, "old_value")
            row["new_value"] = _render(session, field, new, row, "new_value")
            row["_obj"] = obj
            pending.append(row)
        if isinstance(obj, Department):
            pending.extend(_managers_rows(session, obj))

    for obj in session.deleted:
        if type(obj) not in ENTITY_TYPES:
            continue
        row = _base_row(session, obj, ACTION_DELETE)
        row["entity_id"] = getattr(obj, "id", None)
        pending.append(row)


@event.listens_for(Session, "after_flush")
def _write_reference_changes(session: Session, flush_context) -> None:
    """Записать собранное ОДНИМ INSERT-ом, когда id новых строк уже присвоены."""
    pending: list[dict] = session.info.get(_PENDING) or []
    if not pending:
        session.info.pop(_UNRESOLVED, None)
        return
    session.info[_PENDING] = []
    # Имена справочников, которых не было в сессии: добираем ДО записи, чтобы в
    # журнале стояло «ООО «Комфорт» (#45)», а не голое «#45».
    _resolve_pending_refs(session)

    rows = []
    for row in pending:
        obj = row.pop("_obj", None)
        if obj is not None:
            row.setdefault("entity_id", None)
            if row.get("entity_id") is None:
                row["entity_id"] = getattr(obj, "id", None)
            if row.get("employee_id") is None:
                row["employee_id"] = _employee_id_of(obj)
        row.setdefault("entity_id", None)
        rows.append(row)

    # executemany по Core-таблице: ORM-объект на строку стоил бы дороже, а
    # читать эти записи всё равно только запросом с фильтрами.
    session.execute(ReferenceChange.__table__.insert(), rows)


def record_change(
    db: Session,
    *,
    entity_type: str,
    entity_id: int | None,
    entity_label: str | None,
    field: str,
    old_value: str | None,
    new_value: str | None,
    employee_id: int | None = None,
) -> bool:
    """Записать изменение ЯВНО, когда события сессии его не видят.

    Нужно ровно там, где данные переписываются мимо ORM — наборы процентов
    распределения (Core-DELETE всего набора + вставка нового). Не общий чёрный
    ход: всё, что меняется через ORM, обязано логироваться событиями, иначе мы
    вернёмся к «в каждом обработчике не забудь про журнал».

    Ничего не изменилось — записи нет (возвращает False): пересохранение того же
    набора не должно плодить строки.
    """
    if old_value == new_value:
        return False
    db.execute(
        ReferenceChange.__table__.insert(),
        [{
            "actor_id": db.info.get(_ACTOR_ID),
            "actor_name": db.info.get(_ACTOR_NAME),
            "source": db.info.get(_SOURCE) or SOURCE_SYSTEM,
            "operation_id": db.info.get(_OPERATION),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_label": entity_label,
            "employee_id": employee_id,
            "action": ACTION_UPDATE,
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
        }],
    )
    return True


def format_shares(rows: Iterable[tuple[str, Any]]) -> str:
    """Набор процентов одной читаемой строкой: «ЗМО 60% · Комфорт 40%».

    Пустой набор — это «распределение снято», и показать его надо словами:
    пустая ячейка в журнале читается как «данных нет», а не как «убрали».
    """
    parts = [f"{name} {_plain(percent)}%" for name, percent in rows]
    return " · ".join(parts) if parts else "не задано"


def format_share_rows(db: Session, pairs: Iterable[tuple[int, Any]]) -> str:
    """То же, но по парам «id юрлица → процент»: подставляет названия.

    ОДИН запрос на набор. Порядок юрлиц — настроенный в справочнике
    (task_vedomost_format ч.1), тот же, что на всех экранах: своя сортировка
    здесь разошлась бы с ними. Общий помощник для карточки и дефолта отдела —
    два формата одной и той же строки читались бы как разные данные.
    """
    pairs = list(pairs)
    if not pairs:
        return format_shares([])
    from app.services.company_order import company_display_name, company_sort_key

    companies = {
        c.id: c
        for c in db.query(Company).filter(Company.id.in_([cid for cid, _ in pairs])).all()
    }
    ordered = sorted(
        pairs,
        key=lambda p: company_sort_key(companies[p[0]]) if p[0] in companies else (10**9, p[0]),
    )
    return format_shares(
        [
            (company_display_name(companies[cid]) if cid in companies else f"#{cid}", pct)
            for cid, pct in ordered
        ]
    )


@event.listens_for(Session, "after_soft_rollback")
def _drop_pending_on_rollback(session: Session, previous_transaction) -> None:
    """Флаш не доехал — собранные расхождения выбросить.

    Иначе откатившаяся правка всплыла бы в журнале при следующем сохранении и
    журнал сообщил бы об изменении, которого в базе нет.
    """
    session.info[_PENDING] = []
    session.info.pop(_UNRESOLVED, None)
