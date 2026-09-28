"""
Условия труда рабочего места: изменения по полю (ADR-001, task_terms_per_field).

Единственное место правила «какие условия действовали в день D».

* `terms_on(position, day)` — условия дня: по каждому полю последнее изменение с
  `effective_from <= day`. Поле, изменённое с D, действует с D и до следующего
  изменения ЭТОГО ЖЕ поля — никаких правил распространения, это просто свойство
  хранения.
* `month_segments(position, year, month)` — месяц, разрезанный на отрезки с
  неизменными условиями. Расчёт считает каждый отрезок прежней формулой и
  складывает (`services.payroll.calculate_position_payroll`); месяц без
  изменений — один отрезок.
* `TermsView` — позиция, у которой поля условий подменены значениями дня: её
  получает чистая функция расчёта, ничего не зная об истории.

**Как заводятся изменения.** Карточка присылает ЯВНЫЙ список изменений
(`record_term_changes`): группа полей + дата, с которой они действуют. Бэкенд
ничего не диффит и не угадывает — записывает ровно присланное, поэтому «то же
значение, но с сентября» вносится как обычное изменение.

Второй путь — прежний, через ЗЕРКАЛО: код меняет поля позиции, а слушатель
`before_flush` превращает расхождение зеркала с последним значением поля в
изменение с датой `set_effective_from` (не задана — 1-е число следующего
месяца). Им пока ходят форма вахты (её переводит отдельная задача) и создание
рабочего места. У этого пути есть известный дефект: значение, совпадающее с
зеркалом, изменением не считается, и правку задним числом им не внести —
ровно поэтому карточка переведена на явный список.

Дата изменения в месяце, который закрыт или на проверке у бухгалтера,
отклоняется (`ClosedPeriodError`): корректировка закрытого периода — только
через переоткрытие.

Модуль чистый по отношению к расчёту: БД нужна только записи (и разрешению
графика, которого нет в зеркале, — по идентификатору из сессии позиции).
"""
from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass, field as _dc_field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from app.models.position_term_changes import PositionTermChange, encode_term_value
from app.models.position_terms import TERM_FIELDS, TERMS_BEGINNING
from app.models.positions import EmployeePosition

#: Поля, от которых зависит ОБЩИЙ расчёт (не вахта). Смена только официальной
#: зарплаты охранника месяц общего расчёта на отрезки не режет.
PAYROLL_FIELDS: tuple[str, ...] = tuple(
    f for f in TERM_FIELDS if f not in ("is_official", "official_salary")
)

#: Группы условий — то, чем их меняет человек. Поля внутри группы связаны и
#: порознь бессмысленны (тип оплаты без своей базы, коэффициент без вида
#: оплаты), поэтому изменение вносится ГРУППОЙ целиком: присланные значения
#: записываются все, включая погашенные чужие базы.
TERM_GROUPS: dict[str, tuple[str, ...]] = {
    "pay": ("pay_type", "rate", "shift_rate", "hour_rate"),
    "schedule": ("schedule_id",),
    "weekend": ("weekend_pay_type", "weekend_coefficient", "weekend_fixed_rate"),
    "holiday": ("holiday_pay_type", "holiday_coefficient", "holiday_fixed_rate"),
    "overtime": ("overtime_coefficient",),
    "official": ("is_official", "official_salary"),
}

#: Подписи групп — на бэке, чтобы фронт не заводил вторую копию.
TERM_GROUP_LABELS: dict[str, str] = {
    "pay": "Тип оплаты и ставка",
    "schedule": "График",
    "weekend": "Оплата вне графика",
    "holiday": "Оплата праздничных",
    "overtime": "Коэффициент переработки",
    "official": "Официальное трудоустройство",
}

#: поле → группа, в которую оно входит
GROUP_OF_FIELD: dict[str, str] = {
    f: group for group, fields in TERM_GROUPS.items() for f in fields
}

assert set(GROUP_OF_FIELD) == set(TERM_FIELDS), "группы условий разошлись с TERM_FIELDS"

_EFFECTIVE_ATTR = "_terms_effective_from"


# ── Даты ──────────────────────────────────────────────────────────────────────

def default_effective_from(today: date | None = None) -> date:
    """Дата начала изменения по умолчанию — 1-е число следующего месяца
    (решение заказчика: оклад обычно меняют с месяца, и случайной оплаты
    половины месяца по разным ставкам не будет)."""
    today = today or date.today()
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, _calendar.monthrange(year, month)[1])


def set_effective_from(position: EmployeePosition, day: date | None) -> None:
    """Запомнить, с какой даты действует предстоящее изменение условий позиции.

    Читает слушатель при flush — это ПУТЬ ЧЕРЕЗ ЗЕРКАЛО (вахта, создание
    рабочего места). Карточка сотрудника ходит через `record_term_changes`.
    """
    setattr(position, _EFFECTIVE_ATTR, day)


def pending_effective_from(position: EmployeePosition) -> date:
    return getattr(position, _EFFECTIVE_ATTR, None) or default_effective_from()


# ── Чтение условий ────────────────────────────────────────────────────────────

def _changes(position) -> list[PositionTermChange]:
    """Изменения условий позиции по возрастанию даты (порядок — у связи)."""
    changes = getattr(position, "term_changes", None)
    return list(changes or [])


def _schedule_of(position, schedule_id: int | None):
    """Объект графика по идентификатору из истории.

    Обычный случай — график не менялся или изменение последнее: он уже лежит на
    позиции зеркалом, и запроса не будет вовсе. Историю другого графика
    достаём через сессию позиции: `get` бьёт в identity map, поэтому на месяц
    расчёта приходится не больше запроса на КАЖДЫЙ РАЗНЫЙ график, а не на строку.
    """
    if schedule_id is None:
        return None
    if getattr(position, "schedule_id", None) == schedule_id:
        return getattr(position, "schedule", None)
    session = object_session(position)
    if session is None:
        return None
    from app.models.schedules import Schedule

    with session.no_autoflush:
        return session.get(Schedule, schedule_id)


class TermsAtDate:
    """Условия, действовавшие в конкретный день.

    Значение поля — из последнего его изменения не позже дня; поля, по которым
    изменений нет вовсе, читаются с позиции (её колонки — зеркало последнего
    значения). `schedule` отдаётся объектом: его ждёт расчёт.
    """

    __slots__ = ("_position", "_values", "day")

    def __init__(self, position, values: dict[str, Any], day: date) -> None:
        self._position = position
        self._values = values
        self.day = day

    def __getattr__(self, name):
        if name in TERM_FIELDS:
            values = self._values
            if name in values:
                return values[name]
            return getattr(self._position, name, None)
        if name == "schedule":
            return _schedule_of(self._position, self.schedule_id)
        raise AttributeError(name)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<TermsAtDate {self.day} {self._values}>"


def values_on(position, day: date) -> dict[str, Any]:
    """Значения полей условий, действовавшие в день (только заданные историей)."""
    values: dict[str, Any] = {}
    for c in _changes(position):  # по возрастанию даты
        if c.effective_from <= day:
            values[c.field] = c.decoded
        else:
            break
    return values


def terms_on(position, day: date):
    """Условия позиции, действовавшие в день `day`.

    Истории нет (позиция вставлена мимо ORM, объект собран в тестах) — сама
    позиция: её поля и есть единственные условия.
    """
    if position is None:
        return None
    values = values_on(position, day)
    if not values:
        return position
    return TermsAtDate(position, values, day)


def latest_terms(position):
    """Условия по последнему изменению каждого поля — то же, что зеркало."""
    changes = _changes(position)
    if not changes:
        return position
    return terms_on(position, max(c.effective_from for c in changes))


def _same(a, b, fields) -> bool:
    return all(getattr(a, f, None) == getattr(b, f, None) for f in fields)


def month_segments(
    position, year: int, month: int, fields: tuple[str, ...] = PAYROLL_FIELDS,
) -> list[tuple[date, date, object]]:
    """Отрезки месяца с неизменными условиями: [(первый день, последний, условия)].

    Границы — даты изменений НУЖНЫХ полей внутри месяца. Изменение, записанное
    тем же значением (законное: бухгалтер осознанно указала дату), месяц не
    режет — отрезки склеиваются сравнением значений.
    """
    first, last = month_bounds(year, month)
    if position is None:
        return [(first, last, None)]
    changes = _changes(position)
    if not changes:
        return [(first, last, position)]

    days = sorted({
        c.effective_from for c in changes
        if first < c.effective_from <= last and c.field in fields
    })
    segments: list[tuple[date, date, object]] = []
    cur_start = first
    cur_terms = terms_on(position, first)
    for day in days:
        terms = terms_on(position, day)
        if _same(terms, cur_terms, fields):
            cur_terms = terms
            continue
        segments.append((cur_start, day - timedelta(days=1), cur_terms))
        cur_start, cur_terms = day, terms
    segments.append((cur_start, last, cur_terms))
    return segments


class TermsView:
    """Позиция, у которой условия взяты из истории.

    Чистая функция расчёта читает `position.rate`, `position.schedule` и т. п. —
    здесь эти поля отдают условия дня, всё остальное (id, основная ли, должность,
    отдел, компания) — сама позиция.
    """

    __slots__ = ("_position", "_terms")

    def __init__(self, position, terms) -> None:
        self._position = position
        self._terms = terms

    def __getattr__(self, name):
        if name in TERM_FIELDS or name == "schedule":
            return getattr(self._terms, name)
        return getattr(self._position, name)

    @property
    def terms(self):
        return self._terms


def view_on(position, terms):
    if position is None or terms is None or terms is position:
        return position
    return TermsView(position, terms)


def schedule_on(position, day: date):
    """График позиции, действовавший в день `day`."""
    terms = terms_on(position, day)
    return getattr(terms, "schedule", None) if terms is not None else None


class DatedSchedule:
    """График позиции, зависящий от даты: `on(day)` — график, действовавший в
    этот день. Его получают места, которым график нужен по датам вперемешку из
    разных месяцев (годовой лимит больничного)."""

    __slots__ = ("_position",)

    def __init__(self, position) -> None:
        self._position = position

    def on(self, day: date):
        return schedule_on(self._position, day)


def terms_snapshot(position, year: int, month: int) -> list[dict]:
    """Условия, действовавшие в месяце, — для снимка закрытого периода.

    Состав прежний; `version_id` остался ключом формата и всегда пуст: версий
    как снимков всех полей больше нет, а старые снимки читаются как есть.
    """
    out = []
    for start, end, terms in month_segments(position, year, month, TERM_FIELDS):
        out.append({
            "version_id": None,
            "from": start.isoformat(),
            "to": end.isoformat(),
            **{f: _json(getattr(terms, f, None)) for f in TERM_FIELDS},
        })
    return out


def _json(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


# ── Запись изменений ──────────────────────────────────────────────────────────

class ClosedPeriodError(Exception):
    """Правка задевает месяц, который закрыт или на проверке у бухгалтера.

    Роутеры и общий обработчик переводят её в 409."""


class TermsValueError(Exception):
    """Присланные значения условий не годятся (неизвестное поле, пустая группа).

    Роутеры переводят её в 422."""


class TermsNotEditableError(Exception):
    """Условия попытались изменить общей правкой карточки, без даты.

    Свободно они не правятся: у изменения обязана быть дата, с которой оно
    действует, иначе правка уходит не в тот месяц — ровно так и потерялись
    коэффициенты на препроде 27.09.2026. Общий обработчик переводит в 422.
    """


@dataclass
class TermChange:
    """Одно изменение группы условий: значения полей и дата начала действия."""

    effective_from: date
    values: dict[str, Any] = _dc_field(default_factory=dict)


def _mirror_value(position: EmployeePosition, field: str):
    if field == "schedule_id":
        # Связь могли поменять объектом, а не id: FK синхронизируется только
        # внутри flush, и до него `schedule_id` остался бы прежним.
        state = position.__dict__
        if "schedule" in state:
            sched = state["schedule"]
            return sched.id if sched is not None else None
    return getattr(position, field)


def _stamp(session: Session, change: PositionTermChange) -> None:
    from app.services.reference_audit import _ACTOR_ID, _ACTOR_NAME

    change.created_by_id = session.info.get(_ACTOR_ID)
    change.created_by_name = session.info.get(_ACTOR_NAME)


def _check_date_open(session: Session, position: EmployeePosition, day: date) -> None:
    """Дата начала изменения не может попасть в закрытый месяц или месяц на
    проверке у бухгалтера (решение заказчика п.4 + правило п.6: период,
    отправленный бухгалтеру, не меняется под ним)."""
    from app.models.timesheet_periods import TimesheetPeriod
    from app.services.closed_periods import month_status

    if day <= TERMS_BEGINNING:
        # «С начала времён» у существующей позиции — переписать всю историю:
        # задевает каждый месяц, поэтому годится, только пока ни один месяц
        # отдела не закрыт и не на проверке.
        query = session.query(TimesheetPeriod).filter(TimesheetPeriod.status != "draft")
        query = (
            query.filter(TimesheetPeriod.department_id.is_(None))
            if position.department_id is None
            else query.filter(TimesheetPeriod.department_id == position.department_id)
        )
        if query.first() is not None:
            raise ClosedPeriodError(
                "Нельзя менять условия «с начала»: у отдела есть закрытые или "
                "проверяемые месяцы. Укажите дату начала в открытом месяце."
            )
        return

    status = month_status(session, position.department_id, day.year, day.month)
    if status is not None:
        label = "закрыт" if status == "closed" else "на проверке у бухгалтера"
        raise ClosedPeriodError(
            f"Нельзя менять условия с {day:%d.%m.%Y}: период {day:%m.%Y} {label}. "
            "Выберите дату в открытом месяце или переоткройте период."
        )


def _write_change(
    session: Session,
    position: EmployeePosition,
    field: str,
    day: date,
    value: Any,
    journal: bool = True,
) -> PositionTermChange:
    """Записать значение поля с этой даты: своя строка на (поле, дату).

    Повторное изменение того же поля той же датой перезаписывает строку —
    иначе на одну дату пришлось бы держать два значения одного поля.

    `journal=False` — только для базовых значений нового рабочего места: его
    появление журнал пишет одной записью о создании, и четырнадцать строк
    «оклад с начала» к ней ничего не добавили бы.
    """
    from app.services.reference_audit import queue_term_change

    # «Было» — то, что действовало в этот день ДО правки (истории нет — зеркало).
    before = values_on(position, day).get(field, _mirror_value(position, field))
    if journal:
        queue_term_change(
            session, position, field=field, since=effective_label(day),
            old_value=before, new_value=value,
        )
    for c in position.term_changes:
        if c.field == field and c.effective_from == day:
            c.decoded = value
            _stamp(session, c)
            return c
    change = PositionTermChange(
        field=field, effective_from=day, value=encode_term_value(field, value),
    )
    _stamp(session, change)
    position.term_changes.append(change)
    return change


def ensure_no_free_terms_edit(
    position: EmployeePosition | None, values: dict[str, Any],
) -> None:
    """Отклонить изменение условий в общей правке рабочего места.

    Проверяется РАСХОЖДЕНИЕ, а не присутствие поля в запросе: форма шлёт поля
    целиком, и отказ на неизменившемся значении сломал бы обычное сохранение
    (так же устроен запрет общего справочника для охранных позиций).
    """
    if position is None:
        return
    from app.services.reference_audit import FIELD_LABELS

    changed = [
        f for f in TERM_FIELDS
        if f in values and _mirror_value(position, f) != values[f]
    ]
    if not changed:
        return
    names = ", ".join(FIELD_LABELS.get(f, f) for f in changed)
    raise TermsNotEditableError(
        f"Условия труда ({names}) меняются в карточке кнопкой «Изменить»: "
        "у изменения обязательна дата, с которой оно действует."
    )


def latest_values(position) -> dict[str, Any]:
    """Значение каждого поля по последнему его изменению — это и есть зеркало."""
    values: dict[str, Any] = {}
    for c in _changes(position):  # по возрастанию даты
        values[c.field] = c.decoded
    return values


def sync_mirror(position: EmployeePosition) -> None:
    """Колонки позиции = последнее значение каждого поля (решение заказчика:
    зеркало остаётся последним, даже если правили прошлое)."""
    for field, value in latest_values(position).items():
        if _mirror_value(position, field) != value:
            setattr(position, field, value)


def record_term_changes(
    session: Session, position: EmployeePosition, changes: list[TermChange],
) -> list[PositionTermChange]:
    """Записать ЯВНЫЙ список изменений условий: ровно то, что прислали.

    Ничего не диффит: значение, совпадающее с действующим, — законное
    изменение (дату выбрал человек), и оно записывается. Разные группы можно
    менять разными датами в один заход — по вызову на группу.
    """
    written: list[PositionTermChange] = []
    for change in changes:
        unknown = [f for f in change.values if f not in TERM_FIELDS]
        if unknown:
            raise TermsValueError(f"Неизвестные условия: {', '.join(sorted(unknown))}")
        if not change.values:
            raise TermsValueError("Изменение без значений")
        _check_date_open(session, position, change.effective_from)
        for field, value in change.values.items():
            written.append(
                _write_change(session, position, field, change.effective_from, value)
            )
    position.term_changes.sort(key=lambda c: (c.effective_from, c.field))
    sync_mirror(position)
    setattr(position, _EFFECTIVE_ATTR, None)
    return written


def apply_terms_changes(session: Session, position: EmployeePosition) -> None:
    """Путь через ЗЕРКАЛО: перенести изменение полей позиции в историю.

    Им ходят создание рабочего места и форма вахты. Дефект пути известен и
    записан в `.claude/rules/historicity.md`: значение, совпадающее с
    зеркалом, изменением не считается. Карточка сотрудника пользуется
    `record_term_changes`, где этого нет.
    """
    changes = _changes(position)
    if not changes:
        # Истории нет (новая позиция или вставленная мимо ORM) — базовые
        # значения всех полей «с начала времён»: до них условий не было.
        for field in TERM_FIELDS:
            _write_change(
                session, position, field, TERMS_BEGINNING,
                _mirror_value(position, field), journal=False,
            )
        return

    latest = latest_values(position)
    changed = [f for f in TERM_FIELDS if _mirror_value(position, f) != latest.get(f)]
    if not changed:
        return

    if position.id in session.info.get(_FRESH, ()):
        # Позиция создана в ЭТОЙ ЖЕ транзакции: создание и настройка — одно
        # действие (flush → задать график и оклад → commit), истории у неё ещё
        # нет. Правим базовые строки, а не заводим изменение «со следующего
        # месяца» — иначе новый сотрудник получал бы оклад только через месяц.
        for c in position.term_changes:
            if c.field in changed:
                c.decoded = _mirror_value(position, c.field)
        return

    day = pending_effective_from(position)
    _check_date_open(session, position, day)
    for field in changed:
        _write_change(session, position, field, day, _mirror_value(position, field))
    position.term_changes.sort(key=lambda c: (c.effective_from, c.field))
    # Зеркало — последнее значение каждого поля: правка прошлого его не двигает.
    sync_mirror(position)
    setattr(position, _EFFECTIVE_ATTR, None)


_FRESH = "terms_fresh_positions"


@event.listens_for(Session, "after_flush")
def _remember_new_positions(session: Session, flush_context) -> None:
    """Запомнить позиции, вставленные в этой транзакции (см. `apply_terms_changes`)."""
    fresh = [o.id for o in session.new if isinstance(o, EmployeePosition)]
    if fresh:
        session.info.setdefault(_FRESH, set()).update(fresh)


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
@event.listens_for(Session, "after_soft_rollback")
def _forget_new_positions(session: Session, *_) -> None:
    session.info.pop(_FRESH, None)


@event.listens_for(Session, "before_flush")
def _positions_to_versions(session: Session, flush_context, instances) -> None:
    positions = [
        obj for obj in list(session.new) + list(session.dirty)
        if isinstance(obj, EmployeePosition) and obj not in session.deleted
    ]
    if not positions:
        return
    with session.no_autoflush:
        for position in positions:
            apply_terms_changes(session, position)


def effective_label(day: date) -> str:
    """Подпись даты начала изменения для интерфейса и снимка."""
    return "с начала" if day <= TERMS_BEGINNING else f"с {day:%d.%m.%Y}"


__all__ = [
    "ClosedPeriodError",
    "DatedSchedule",
    "GROUP_OF_FIELD",
    "PAYROLL_FIELDS",
    "TERM_GROUPS",
    "TERM_GROUP_LABELS",
    "TermChange",
    "TermsNotEditableError",
    "TermsValueError",
    "TermsView",
    "apply_terms_changes",
    "default_effective_from",
    "effective_label",
    "ensure_no_free_terms_edit",
    "latest_terms",
    "latest_values",
    "month_bounds",
    "month_segments",
    "record_term_changes",
    "schedule_on",
    "set_effective_from",
    "sync_mirror",
    "terms_on",
    "terms_snapshot",
    "values_on",
    "view_on",
]
