from __future__ import annotations

import calendar as _cal
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry


def visible_employees_for_actor(
    db: Session,
    actor: Employee,
    department_id: int | None = None,
    year: int | None = None,
    month: int | None = None,
) -> list[Employee]:
    q = db.query(Employee).filter(Employee.is_system_admin == False)  # noqa: E712

    if year is not None and month is not None:
        period_start = date(year, month, 1)
        q = q.filter(
            or_(
                Employee.is_active == True,  # noqa: E712
                Employee.dismissal_date >= period_start,
            )
        )
    else:
        q = q.filter(Employee.is_active == True)  # noqa: E712

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
    days_in_month = _cal.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_in_month)
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


def compute_extra_companies_by_employee(
    employees: list[Employee],
    entries: list[TimesheetEntry],
) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for emp in employees:
        emp_company_ids = {e.company_id for e in entries if e.employee_id == emp.id}
        if emp.default_company_id is not None:
            emp_company_ids.discard(emp.default_company_id)
        result[emp.id] = sorted(emp_company_ids)
    return result


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

    # Взаимоисключение часы/код отсутствия: часы в дне снимают код (день стал
    # рабочим). Обратное направление — в services.absences.set_absence.
    if hours != Decimal("0"):
        from app.services.absences import delete_absence_for_day

        delete_absence_for_day(db, actor, employee_id, work_date)

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
    from app.services.timesheet_periods import (
        PeriodLockedException,
        can_edit_cells,
        get_or_create_period,
    )

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


def build_autofill_preview(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    department_id: int | None = None,
):
    """
    Compute what would be filled by schedule for visible employees.
    Returns AutofillPreview without modifying timesheet_entries.
    """
    from app.models.production_calendars import ProductionCalendar
    from app.schemas.timesheet import AutofillPreview, AutofillSkippedEmployee, TimesheetCellInput
    from app.services.timesheet_periods import can_edit_cells, get_or_create_period
    from app.services.work_schedule import (
        planned_work_dates,
        schedule_issue,
        shift_hours_for_date,
    )

    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    if cal is None:
        raise ValueError(f"Загрузите производственный календарь {year}")

    calendar_data = cal.data
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)

    days_in_month = _cal.monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month)

    emp_ids = [e.id for e in employees]
    existing_entries = (
        db.query(TimesheetEntry)
        .filter(
            TimesheetEntry.employee_id.in_(emp_ids) if emp_ids else False,
            TimesheetEntry.work_date >= period_start,
            TimesheetEntry.work_date <= period_end,
        )
        .all()
    ) if emp_ids else []

    existing_keys: set[tuple[int, date, int]] = {
        (e.employee_id, e.work_date, e.company_id) for e in existing_entries
    }

    entries_to_create: list[TimesheetCellInput] = []
    cells_skipped = 0
    employees_processed = 0
    employees_skipped: list[AutofillSkippedEmployee] = []

    has_draft = False

    for emp in employees:
        period = get_or_create_period(db, emp.department_id, year, month)
        if not can_edit_cells(period):
            employees_skipped.append(AutofillSkippedEmployee(
                employee_id=emp.id,
                employee_name=emp.full_name,
                reason="Период не открыт для редактирования",
            ))
            continue

        has_draft = True

        if emp.schedule_id is None:
            employees_skipped.append(AutofillSkippedEmployee(
                employee_id=emp.id,
                employee_name=emp.full_name,
                reason="Не назначен график работы",
            ))
            continue

        schedule = emp.schedule
        if schedule is None:
            employees_skipped.append(AutofillSkippedEmployee(
                employee_id=emp.id,
                employee_name=emp.full_name,
                reason="Не назначен график работы",
            ))
            continue

        # Сменный график без анкера/паттерна цикла посчитать нельзя.
        issue = schedule_issue(schedule)
        if issue is not None:
            employees_skipped.append(AutofillSkippedEmployee(
                employee_id=emp.id,
                employee_name=emp.full_name,
                reason=issue,
            ))
            continue

        if emp.default_company_id is None:
            employees_skipped.append(AutofillSkippedEmployee(
                employee_id=emp.id,
                employee_name=emp.full_name,
                reason="Не указана основная компания",
            ))
            continue

        employees_processed += 1

        # Плановые дни графика (task_shift_schedules): weekday — рабочие дни
        # недели графика по производственному календарю, cyclic — смены по
        # циклу от стартовой даты (календарь на цикл не влияет).
        for work_date in planned_work_dates(schedule, year, month, calendar_data):
            hours = int(shift_hours_for_date(schedule, work_date, calendar_data))
            key = (emp.id, work_date, emp.default_company_id)
            if key in existing_keys:
                cells_skipped += 1
                continue

            entries_to_create.append(TimesheetCellInput(
                employee_id=emp.id,
                work_date=work_date,
                company_id=emp.default_company_id,
                hours=hours,
            ))

    db.commit()  # commit any lazily-created periods

    if not has_draft and employees:
        raise ValueError("Нет периодов в статусе draft для автозаполнения")

    return AutofillPreview(
        year=year,
        month=month,
        entries_to_create=entries_to_create,
        cells_skipped=cells_skipped,
        employees_processed=employees_processed,
        employees_skipped=employees_skipped,
    )


def apply_autofill(
    db: Session,
    actor: Employee,
    preview,
) -> int:
    """Apply preview entries to DB via upsert_cells_batch. Returns count created."""
    if not preview.entries_to_create:
        return 0

    cells = [
        (e.employee_id, e.work_date, e.company_id, e.hours)
        for e in preview.entries_to_create
    ]
    results = upsert_cells_batch(db, actor, cells)
    return sum(1 for r in results if r is not None)
