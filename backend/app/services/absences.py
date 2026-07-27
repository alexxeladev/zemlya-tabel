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
from datetime import date

from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.employee_absences import (
    ABSENCE_CODES,
    PAID_ABSENCE_KINDS,
    EmployeeAbsence,
)
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry

__all__ = [
    "ABSENCE_CODES",
    "PAID_ABSENCE_KINDS",
    "absence_code",
    "absence_days_by_kind",
    "get_month_absences",
    "set_absence",
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
