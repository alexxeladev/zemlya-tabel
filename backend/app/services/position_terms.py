"""
Версии условий труда рабочего места (task_stage3_historicity, часть 1).

Единственное место правила «какие условия действовали в день D».

* `terms_on(position, day)` — версия, действовавшая в день: последняя с
  `effective_from <= day`. День начала версии ВХОДИТ в неё, последний день
  прежней версии — день перед началом новой.
* `month_segments(position, year, month)` — месяц, разрезанный на отрезки с
  неизменными условиями. Расчёт считает каждый отрезок прежней формулой и
  складывает (`services.payroll.calculate_position_payroll`); месяц с одной
  версией — один отрезок, результат тот же, что до этапа 3.
* `TermsView` — позиция, у которой поля условий подменены полями версии: её
  получает чистая функция расчёта, ничего не зная о версиях.

**Как заводятся версии.** Никто не создаёт строки `position_terms` руками:
код меняет поля позиции, как и раньше (формы, compat-аксессоры сотрудника,
импорт, вахта), а слушатель `before_flush` превращает изменение в версию с
датой начала действия. Дату задаёт вызывающий (`set_effective_from`, из поля
формы); не задана — **1-е число следующего месяца** (решение заказчика).
Изменение поля с даты D действует с D и дальше — до первой более поздней
версии, где это поле задано ИНАЧЕ (явно запланированное изменение не
затирается). Поля позиции после этого равны последней версии — её зеркало.

Дата версии в месяце, который закрыт или на проверке у бухгалтера, отклоняется
(`ClosedPeriodError`): корректировка закрытого периода — только через
переоткрытие.

Модуль чистый по отношению к расчёту: БД нужна только слушателю.
"""
from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.position_terms import TERM_FIELDS, TERMS_BEGINNING, PositionTerms
from app.models.positions import EmployeePosition

#: Поля, от которых зависит ОБЩИЙ расчёт (не вахта). Смена только официальной
#: зарплаты охранника месяц общего расчёта на отрезки не режет.
PAYROLL_FIELDS: tuple[str, ...] = tuple(
    f for f in TERM_FIELDS if f not in ("is_official", "official_salary")
)

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

    Читает слушатель при flush. None — дата по умолчанию.
    """
    setattr(position, _EFFECTIVE_ATTR, day)


def pending_effective_from(position: EmployeePosition) -> date:
    return getattr(position, _EFFECTIVE_ATTR, None) or default_effective_from()


# ── Чтение версий ─────────────────────────────────────────────────────────────

def _versions(position) -> list:
    versions = getattr(position, "terms_versions", None)
    return list(versions or [])


def terms_on(position, day: date):
    """Условия позиции, действовавшие в день `day`.

    Версий нет (объект собран в тестах, позиция вставлена мимо ORM) — сама
    позиция: её поля и есть единственные условия.
    """
    if position is None:
        return None
    current = None
    for v in _versions(position):  # отсортированы по effective_from
        if v.effective_from <= day:
            current = v
        else:
            break
    return current if current is not None else position


def latest_terms(position):
    versions = _versions(position)
    return versions[-1] if versions else position


def _same(a, b, fields) -> bool:
    return all(getattr(a, f, None) == getattr(b, f, None) for f in fields)


def month_segments(
    position, year: int, month: int, fields: tuple[str, ...] = PAYROLL_FIELDS,
) -> list[tuple[date, date, object]]:
    """Отрезки месяца с неизменными условиями: [(первый день, последний, версия)].

    Соседние версии, не отличающиеся по `fields`, склеиваются: смена, скажем,
    официальной зарплаты охранника общий расчёт на части не режет.
    """
    first, last = month_bounds(year, month)
    if position is None:
        return [(first, last, None)]
    versions = _versions(position)
    if not versions:
        return [(first, last, position)]

    segments: list[tuple[date, date, object]] = []
    start_terms = terms_on(position, first)
    cur_start, cur_terms = first, start_terms
    for v in versions:
        if v.effective_from <= first or v.effective_from > last:
            continue
        if _same(v, cur_terms, fields):
            cur_terms = v
            continue
        segments.append((cur_start, v.effective_from - timedelta(days=1), cur_terms))
        cur_start, cur_terms = v.effective_from, v
    segments.append((cur_start, last, cur_terms))
    return segments


class TermsView:
    """Позиция, у которой условия взяты из версии.

    Чистая функция расчёта читает `position.rate`, `position.schedule` и т. п. —
    здесь эти поля отдаёт версия, всё остальное (id, основная ли, должность,
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
    """Версии, действовавшие в месяце, — для снимка закрытого периода."""
    out = []
    for start, end, terms in month_segments(position, year, month, TERM_FIELDS):
        out.append({
            "version_id": getattr(terms, "id", None) if isinstance(terms, PositionTerms) else None,
            "from": start.isoformat(),
            "to": end.isoformat(),
            **{f: _json(getattr(terms, f, None)) for f in TERM_FIELDS},
        })
    return out


def _json(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


# ── Запись версий: слушатель сессии ───────────────────────────────────────────

class ClosedPeriodError(Exception):
    """Правка задевает месяц, который закрыт или на проверке у бухгалтера.

    Роутеры и общий обработчик переводят её в 409."""


def _mirror_value(position: EmployeePosition, field: str):
    if field == "schedule_id":
        # Связь могли поменять объектом, а не id: FK синхронизируется только
        # внутри flush, и до него `schedule_id` остался бы прежним.
        state = position.__dict__
        if "schedule" in state:
            sched = state["schedule"]
            return sched.id if sched is not None else None
    return getattr(position, field)


def _terms_from_position(position: EmployeePosition, day: date) -> PositionTerms:
    return PositionTerms(
        effective_from=day,
        **{f: _mirror_value(position, f) for f in TERM_FIELDS},
    )


def _copy_terms(src, day: date) -> PositionTerms:
    return PositionTerms(effective_from=day, **{f: getattr(src, f) for f in TERM_FIELDS})


def _stamp(session: Session, version: PositionTerms) -> None:
    from app.services.reference_audit import _ACTOR_ID, _ACTOR_NAME

    version.created_by_id = session.info.get(_ACTOR_ID)
    version.created_by_name = session.info.get(_ACTOR_NAME)


def _check_date_open(session: Session, position: EmployeePosition, day: date) -> None:
    """Дата начала версии не может попасть в закрытый месяц или месяц на
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


def apply_terms_changes(session: Session, position: EmployeePosition) -> None:
    """Перенести изменения полей позиции в историю версий."""
    versions = list(position.terms_versions)
    if not versions:
        # Новая позиция (или вставленная мимо ORM и впервые правится) — первая
        # версия действует с начала времён: до неё у позиции условий не было.
        v = _terms_from_position(position, TERMS_BEGINNING)
        _stamp(session, v)
        position.terms_versions.append(v)
        return

    latest = versions[-1]
    changed = [f for f in TERM_FIELDS if _mirror_value(position, f) != getattr(latest, f)]
    if not changed:
        return

    if position.id in session.info.get(_FRESH, ()):
        # Позиция создана в ЭТОЙ ЖЕ транзакции: создание и настройка — одно
        # действие (flush → задать график и оклад → commit), истории у неё ещё
        # нет. Дописываем первую версию, а не заводим новую «со следующего
        # месяца» — иначе новый сотрудник получал бы оклад только через месяц.
        for v in versions:
            for f in changed:
                setattr(v, f, _mirror_value(position, f))
        return

    day = pending_effective_from(position)
    _check_date_open(session, position, day)
    base = terms_on(position, day)
    if base is position:  # раньше первой версии — от первой
        base = versions[0]
    # Значение поля, действовавшее в день D ДО правки: по нему узнаём, какие
    # более поздние версии поле явно не меняли.
    before = {f: getattr(base, f) for f in changed}
    target = next((v for v in versions if v.effective_from == day), None)
    if target is None:
        target = _copy_terms(base, day)
        _stamp(session, target)
        position.terms_versions.append(target)
        versions.append(target)
    for f in changed:
        target_value = _mirror_value(position, f)
        setattr(target, f, target_value)
        # Изменение действует с D и дальше — пока поле не менялось явно. Более
        # поздние версии с прежним значением получают новое (иначе повышение
        # «с 15-го» откатилось бы на следующей версии, заведённой ради другого
        # поля), а на первой версии, где поле задано иначе, распространение
        # останавливается: запланированное позже изменение не затирается.
        for v in sorted(versions, key=lambda x: x.effective_from):
            if v.effective_from <= day:
                continue
            if getattr(v, f) != before[f]:
                break
            setattr(v, f, target_value)
    # Порядок коллекции — по дате (новая версия могла встать в середину).
    position.terms_versions.sort(key=lambda v: v.effective_from)
    # Зеркало — ПОСЛЕДНЯЯ версия. Если правили прошлое, а позже есть ещё
    # версии, по остальным полям зеркало уже равно последней.
    last = position.terms_versions[-1]
    for f in TERM_FIELDS:
        value = getattr(last, f)
        if f == "schedule_id":
            if position.schedule_id != value:
                position.schedule_id = value
        elif getattr(position, f) != value:
            setattr(position, f, value)
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
    """Подпись даты начала версии для интерфейса и снимка."""
    return "с начала" if day <= TERMS_BEGINNING else f"с {day:%d.%m.%Y}"


__all__ = [
    "ClosedPeriodError",
    "DatedSchedule",
    "PAYROLL_FIELDS",
    "TermsView",
    "apply_terms_changes",
    "default_effective_from",
    "effective_label",
    "latest_terms",
    "month_bounds",
    "month_segments",
    "schedule_on",
    "set_effective_from",
    "terms_on",
    "terms_snapshot",
    "view_on",
]

