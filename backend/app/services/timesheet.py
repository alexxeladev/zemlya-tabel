from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.companies import Company
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry


def visible_employees_for_actor(
    db: Session,
    actor: Employee,
    department_id: int | None = None,
) -> list[Employee]:
    q = db.query(Employee).filter(Employee.is_active == True)  # noqa: E712

    if actor.role == "employee":
        return q.filter(Employee.id == actor.id).all()

    if actor.role == "manager":
        if actor.department_id is None:
            return []
        return q.filter(Employee.department_id == actor.department_id).all()

    # admin / accountant
    if department_id is not None:
        q = q.filter(Employee.department_id == department_id)
    return q.all()


def get_month_entries(
    db: Session,
    employees: list[Employee],
    year: int,
    month: int,
) -> list[TimesheetEntry]:
    if not employees:
        return []
    import calendar as _cal
    from datetime import date as _date
    days_in_month = _cal.monthrange(year, month)[1]
    start = _date(year, month, 1)
    end = _date(year, month, days_in_month)
    emp_ids = [e.id for e in employees]
    return (
        db.query(TimesheetEntry)
        .filter(
            TimesheetEntry.employee_id.in_(emp_ids),
            TimesheetEntry.work_date >= start,
            TimesheetEntry.work_date <= end,
        )
        .all()
    )


def _upsert_cell_no_commit(
    db: Session,
    actor: Employee,
    employee_id: int,
    work_date: date,
    company_id: int,
    hours: Decimal,
) -> TimesheetEntry | None:
    """Core upsert logic — flush only, no commit. Caller owns the transaction."""
    existing = (
        db.query(TimesheetEntry)
        .filter(
            and_(
                TimesheetEntry.employee_id == employee_id,
                TimesheetEntry.work_date == work_date,
                TimesheetEntry.company_id == company_id,
            )
        )
        .first()
    )

    if hours == Decimal("0"):
        if existing:
            log_action(
                db, actor, "timesheet_entry", existing.id, "delete",
                before={"hours": str(existing.hours)},
            )
            db.delete(existing)
            db.flush()
        return None

    if existing:
        before_hours = str(existing.hours)
        existing.hours = hours
        db.flush()
        log_action(
            db, actor, "timesheet_entry", existing.id, "update",
            before={"hours": before_hours},
            after={"hours": str(hours)},
        )
        db.flush()
        return existing
    else:
        entry = TimesheetEntry(
            employee_id=employee_id,
            work_date=work_date,
            company_id=company_id,
            hours=hours,
        )
        db.add(entry)
        db.flush()
        log_action(
            db, actor, "timesheet_entry", entry.id, "create",
            after={"hours": str(hours)},
        )
        db.flush()
        return entry


def _check_period_lock(db: Session, employee_id: int, work_date: date) -> None:
    """Raises PeriodLockedException if the period for this employee+date is not draft."""
    from app.services.timesheet_periods import PeriodLockedException, get_or_create_period, can_edit_cells

    emp = db.get(Employee, employee_id)
    if emp is None:
        return  # employee not found — let the FK check handle it
    period = get_or_create_period(db, emp.department_id, work_date.year, work_date.month)
    if not can_edit_cells(period):
        raise PeriodLockedException(period.status)


def upsert_cell(
    db: Session,
    actor: Employee,
    employee_id: int,
    work_date: date,
    company_id: int,
    hours: Decimal,
) -> TimesheetEntry | None:
    _check_period_lock(db, employee_id, work_date)
    result = _upsert_cell_no_commit(db, actor, employee_id, work_date, company_id, hours)
    db.commit()
    if result is not None:
        db.refresh(result)
    return result


def upsert_cells_batch(
    db: Session,
    actor: Employee,
    cells: list[tuple[int, date, int, Decimal]],
) -> list[TimesheetEntry | None]:
    """Transactional batch upsert — single commit for all cells."""
    # Check period lock for all cells first
    for employee_id, work_date, _company_id, _hours in cells:
        _check_period_lock(db, employee_id, work_date)

    results = []
    for employee_id, work_date, company_id, hours in cells:
        result = _upsert_cell_no_commit(db, actor, employee_id, work_date, company_id, hours)
        results.append(result)
    db.commit()
    for r in results:
        if r is not None:
            db.refresh(r)
    return results
