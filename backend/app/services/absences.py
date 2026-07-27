"""
Отсутствия сотрудников: коды ОТ / ДО / Б / Н в табеле (задача «Отсутствия», ч.1).

Инвариант: один день = либо часы работы, либо один код отсутствия. Обе мутации
сходятся сюда и в `services.timesheet.upsert_cell`, поэтому «и часы, и код»
в одном дне возникнуть не может.

Деньги здесь НЕ считаются — оплата отпуска/больничного живёт в
`services.payroll` (там же норма и оклад), чтобы формула была в одном месте.
"""
from __future__ import annotations

import calendar as _cal
from collections.abc import Iterable
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import log_action
from app.models.employee_absences import (
    ABSENCE_CODES,
    PAID_ABSENCE_KINDS,
    AbsenceKind,
    EmployeeAbsence,
)
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.services.calendar import is_holiday

__all__ = [
    "ABSENCE_CODES",
    "PAID_ABSENCE_KINDS",
    "absence_code",
    "absence_days_by_kind",
    "get_month_absences",
    "is_payable_absence_day",
    "load_year_sick_dates",
    "over_limit_sick_dates",
    "set_absence",
    "sick_days_used_before_month",
    "sick_limit_days",
    "split_sick_dates_by_limit",
]


def absence_code(kind: str) -> str:
    """Буквенный код для табеля/Excel: vacation → «ОТ» и т.д."""
    return ABSENCE_CODES.get(kind, kind)


def get_month_absences(
    db: Session,
    employees: list[Employee],
    year: int,
    month: int,
) -> list[EmployeeAbsence]:
    if not employees:
        return []
    days_in_month = _cal.monthrange(year, month)[1]
    emp_ids = [e.id for e in employees]
    return (
        db.query(EmployeeAbsence)
        .filter(
            EmployeeAbsence.employee_id.in_(emp_ids),
            EmployeeAbsence.work_date >= date(year, month, 1),
            EmployeeAbsence.work_date <= date(year, month, days_in_month),
        )
        .all()
    )


def absence_days_by_kind(absences: list[EmployeeAbsence]) -> dict[str, int]:
    """{kind: сколько дней} — по одной записи на день, поэтому просто счётчик."""
    result: dict[str, int] = {kind: 0 for kind in ABSENCE_CODES}
    for a in absences:
        result[a.kind] = result.get(a.kind, 0) + 1
    return result


# ── Годовой лимит больничного (часть 2) ───────────────────────────────────────
#
# Лимит календарный: первые N рабочих дней больничного в году оплачиваются
# 100%, дальше — за свой счёт. Считаем ХРОНОЛОГИЧЕСКИ по дате: правка задним
# числом в ранний месяц автоматически съедает лимит раньше, и поздние месяцы
# пересчитываются сами — отдельного «состояния лимита» в БД не храним.
#
# Лимит расходуют только оплачиваемые дни (рабочие по производственному
# календарю): день Б на выходном отмечается, но денег не даёт и лимит не жжёт —
# это следует из правила части 1 «платим только за рабочие дни».


def sick_limit_days() -> int:
    """Годовой лимит оплачиваемых дней больничного (настройка, дефолт 10)."""
    return settings.SICK_LEAVE_LIMIT_DAYS


def is_payable_absence_day(work_date: date, calendar_data: dict | None) -> bool:
    """Оплачиваемый ли это день отсутствия — т.е. рабочий по календарю."""
    if calendar_data is None:
        return True
    return not is_holiday(calendar_data, work_date.month, work_date.day)


def split_sick_dates_by_limit(
    sick_dates: Iterable[date],
    calendar_data: dict | None,
    limit: int | None = None,
    used_before: int = 0,
) -> tuple[list[date], list[date]]:
    """
    Делит дни больничного на оплачиваемые и сверхлимитные — хронологически.

    `used_before` — сколько оплачиваемых дней года уже израсходовано ДО этого
    набора (для помесячного расчёта). Возвращает (оплачиваемые, сверх лимита);
    неоплачиваемые «нерабочие» дни не попадают ни в один список — они не дают
    денег и лимит не расходуют.
    """
    if limit is None:
        limit = sick_limit_days()
    remaining = max(0, limit - max(0, used_before))

    paid: list[date] = []
    over: list[date] = []
    for d in sorted(sick_dates):
        if not is_payable_absence_day(d, calendar_data):
            continue
        if remaining > 0:
            paid.append(d)
            remaining -= 1
        else:
            over.append(d)
    return paid, over


def load_year_sick_dates(
    db: Session, emp_ids: list[int], year: int
) -> dict[int, list[date]]:
    """{employee_id: [даты больничного за календарный год]} — все записи Б.

    Берём все существующие отметки года независимо от статуса периода: факт
    болезни есть, а черновик/закрытый — это про workflow табеля, не про лимит.
    """
    result: dict[int, list[date]] = {}
    if not emp_ids:
        return result
    rows = (
        db.query(EmployeeAbsence)
        .filter(
            EmployeeAbsence.employee_id.in_(emp_ids),
            EmployeeAbsence.kind == AbsenceKind.sick.value,
            EmployeeAbsence.work_date >= date(year, 1, 1),
            EmployeeAbsence.work_date <= date(year, 12, 31),
        )
        .all()
    )
    for r in rows:
        result.setdefault(r.employee_id, []).append(r.work_date)
    return result


def sick_days_used_before_month(
    db: Session,
    emp_ids: list[int],
    year: int,
    month: int,
    calendar_data: dict | None,
) -> dict[int, int]:
    """{employee_id: оплачиваемых дней Б в этом году ДО указанного месяца}."""
    used: dict[int, int] = {}
    for emp_id, dates in load_year_sick_dates(db, emp_ids, year).items():
        used[emp_id] = sum(
            1
            for d in dates
            if d.month < month and is_payable_absence_day(d, calendar_data)
        )
    return used


def over_limit_sick_dates(
    db: Session, emp_ids: list[int], year: int, calendar_data: dict | None
) -> dict[int, set[date]]:
    """{employee_id: даты Б сверх годового лимита} — для пометки в табеле."""
    result: dict[int, set[date]] = {}
    for emp_id, dates in load_year_sick_dates(db, emp_ids, year).items():
        _, over = split_sick_dates_by_limit(dates, calendar_data)
        if over:
            result[emp_id] = set(over)
    return result


def delete_absence_for_day(
    db: Session, actor: Employee, employee_id: int, work_date: date
) -> bool:
    """Снять код отсутствия с дня (без commit). True — если что-то удалили."""
    existing = (
        db.query(EmployeeAbsence)
        .filter(
            EmployeeAbsence.employee_id == employee_id,
            EmployeeAbsence.work_date == work_date,
        )
        .first()
    )
    if existing is None:
        return False
    log_action(
        db, actor, "employee_absence", existing.id, "delete",
        before={"employee_id": employee_id, "work_date": str(work_date),
                "kind": existing.kind},
    )
    db.delete(existing)
    db.flush()
    return True


def set_absence(
    db: Session,
    actor: Employee,
    employee_id: int,
    work_date: date,
    kind: str | None,
) -> EmployeeAbsence | None:
    """
    Поставить/сменить/снять код отсутствия на день.

    kind=None — снять отметку. Постановка кода удаляет часы этого дня по всем
    компаниям (взаимоисключение), удаление пишется в audit log — тихой потери
    данных не происходит.

    Период должен быть в статусе draft — как и для часов.
    """
    from app.services.timesheet import _check_period_lock

    _check_period_lock(db, employee_id, work_date)

    if kind is None:
        deleted = delete_absence_for_day(db, actor, employee_id, work_date)
        if deleted:
            db.commit()
        return None

    if kind not in ABSENCE_CODES:
        raise ValueError(f"Неизвестный вид отсутствия: {kind}")

    # Взаимоисключение: в дне с кодом отсутствия часов быть не может.
    hours_entries = (
        db.query(TimesheetEntry)
        .filter(
            TimesheetEntry.employee_id == employee_id,
            TimesheetEntry.work_date == work_date,
        )
        .all()
    )
    for entry in hours_entries:
        log_action(
            db, actor, "timesheet_entry", entry.id, "delete",
            before={"hours": str(entry.hours), "company_id": entry.company_id},
            reason=f"Проставлен код отсутствия {absence_code(kind)}",
        )
        db.delete(entry)
    if hours_entries:
        db.flush()

    existing = (
        db.query(EmployeeAbsence)
        .filter(
            EmployeeAbsence.employee_id == employee_id,
            EmployeeAbsence.work_date == work_date,
        )
        .first()
    )
    if existing is not None:
        if existing.kind == kind:
            db.commit()
            db.refresh(existing)
            return existing
        before_kind = existing.kind
        existing.kind = kind
        db.flush()
        log_action(
            db, actor, "employee_absence", existing.id, "update",
            before={"kind": before_kind}, after={"kind": kind},
        )
        db.commit()
        db.refresh(existing)
        return existing

    absence = EmployeeAbsence(
        employee_id=employee_id,
        work_date=work_date,
        kind=kind,
        created_by_id=actor.id,
    )
    db.add(absence)
    db.flush()
    log_action(
        db, actor, "employee_absence", absence.id, "create",
        after={"employee_id": employee_id, "work_date": str(work_date), "kind": kind},
    )
    db.commit()
    db.refresh(absence)
    return absence
