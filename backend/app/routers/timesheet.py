from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.companies import Company
from app.models.employees import Employee
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.payroll import (
    CompanyBreakdownRead,
    EmployeePayrollRead,
    PayrollSummaryRead,
)
from app.schemas.timesheet import (
    AutofillPreview,
    AutofillRequest,
    TimesheetBatchInput,
    TimesheetBatchResponse,
    TimesheetCellInput,
    TimesheetEntryRead,
    TimesheetMonthResponse,
)
from app.schemas.timesheet_period import (
    AuditLogRead,
    StatusChangeReason,
    TasksResponse,
    TimesheetPeriodRead,
)
from app.services.payroll import calculate_employee_payroll
from app.services.timesheet import (
    apply_autofill,
    build_autofill_preview,
    compute_extra_companies_by_employee,
    get_month_entries,
    upsert_cell,
    upsert_cells_batch,
    visible_employees_for_actor,
)
from app.services.timesheet_periods import (
    PeriodLockedException,
    close_period,
    get_or_create_period,
    list_review_tasks,
    make_period_read,
    reopen_period,
    return_to_draft,
    submit_for_review,
)

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _check_cell_access(actor: Employee, target_employee_id: int, db: Session) -> Employee:
    target = db.get(Employee, target_employee_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if actor.role in ("admin", "accountant"):
        return target
    if actor.role == "manager":
        if target.department_id != actor.department_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return target
    if actor.id != target_employee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return target


def _check_company_exists(db: Session, company_id: int) -> None:
    if not db.get(Company, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


def _get_period_or_404(db: Session, period_id: int) -> TimesheetPeriod:
    period = db.get(TimesheetPeriod, period_id)
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Period not found")
    return period


def _build_periods_for_response(
    db: Session,
    employees: list[Employee],
    year: int,
    month: int,
    actor: Employee,
) -> list[TimesheetPeriodRead]:
    """Create/fetch periods for all unique department_ids visible in this response."""
    dept_ids: set[int | None] = {e.department_id for e in employees}
    periods = []
    for dept_id in dept_ids:
        period = get_or_create_period(db, dept_id, year, month)
        periods.append(make_period_read(period, actor))
    db.commit()  # commit any newly created periods
    return periods


# ── Payroll helper ────────────────────────────────────────────────────────────

def _build_payroll_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
) -> PayrollSummaryRead:
    from decimal import Decimal
    from app.models.production_calendars import ProductionCalendar

    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    calendar_data = cal.data if cal else None

    companies = db.query(Company).filter(Company.is_active == True).all()  # noqa: E712
    companies_by_id = {c.id: (c.code, c.name) for c in companies}

    entries_by_employee: dict[int, list] = {}
    for e in entries:
        entries_by_employee.setdefault(e.employee_id, []).append(e)

    payroll_items: list[EmployeePayrollRead] = []
    for emp in employees:
        emp_entries = entries_by_employee.get(emp.id, [])
        p = calculate_employee_payroll(emp, emp_entries, calendar_data, year, month, companies_by_id)

        breakdown = [
            CompanyBreakdownRead(
                company_id=bd.company_id,
                company_code=bd.company_code,
                company_name=bd.company_name,
                hours=bd.hours,
                base_amount=bd.base_amount,
                overtime_amount=bd.overtime_amount,
                holiday_amount=bd.holiday_amount,
                total=bd.total,
            )
            for bd in p.breakdown_by_company
        ]
        payroll_items.append(EmployeePayrollRead(
            employee_id=p.employee_id,
            employee_name=p.employee_name,
            rate=p.rate,
            schedule_name=p.schedule_name,
            total_hours=p.total_hours,
            norm_hours=p.norm_hours,
            delta_hours=p.delta_hours,
            overtime_hours=p.overtime_hours,
            holiday_hours=p.holiday_hours,
            hourly_rate=p.hourly_rate,
            base_amount=p.base_amount,
            overtime_amount=p.overtime_amount,
            holiday_amount=p.holiday_amount,
            total_amount=p.total_amount,
            breakdown_by_company=breakdown,
            is_calculable=p.is_calculable,
            reason_if_not_calculable=p.reason_if_not_calculable,
        ))

    zero = Decimal("0")
    return PayrollSummaryRead(
        year=year,
        month=month,
        employees=payroll_items,
        total_employees=len(payroll_items),
        total_hours=sum((p.total_hours for p in payroll_items), zero),
        total_base_amount=sum((p.base_amount for p in payroll_items), zero),
        total_overtime_amount=sum((p.overtime_amount for p in payroll_items), zero),
        total_holiday_amount=sum((p.holiday_amount for p in payroll_items), zero),
        grand_total=sum((p.total_amount for p in payroll_items), zero),
    )


# ── Tasks inbox (Bug 3) ───────────────────────────────────────────────────────

@router.get("/tasks", response_model=TasksResponse)
def get_review_tasks(
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Inbox для accountant/admin: периоды на проверке + недавно закрытые."""
    if actor.role not in ("admin", "accountant"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    pending, closed = list_review_tasks(db)
    return TasksResponse(pending_review=pending, recently_closed=closed)


# ── GET month ─────────────────────────────────────────────────────────────────

@router.get("/{year}/{month}/payroll", response_model=PayrollSummaryRead)
def get_payroll(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    if actor.role not in ("admin", "accountant", "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    # Manager видит только свой отдел — запрос финансов чужого отдела запрещён
    if actor.role == "manager" and department_id is not None and department_id != actor.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    return _build_payroll_summary(db, employees, entries, year, month)


@router.get("/{year}/{month}", response_model=TimesheetMonthResponse)
def get_month(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    include_payroll: bool = Query(default=False),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )

    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    companies = db.query(Company).filter(Company.is_active == True).all()  # noqa: E712
    entries = get_month_entries(db, employees, year, month)
    periods = _build_periods_for_response(db, employees, year, month, actor)
    extra_companies = compute_extra_companies_by_employee(employees, entries)

    payroll = None
    if include_payroll and actor.role in ("admin", "accountant", "manager"):
        payroll = _build_payroll_summary(db, employees, entries, year, month)

    return TimesheetMonthResponse(
        year=year,
        month=month,
        employees=employees,
        companies=companies,
        entries=entries,
        periods=periods,
        extra_companies_by_employee=extra_companies,
        payroll=payroll,
    )


# ── Cell mutations ────────────────────────────────────────────────────────────

@router.put("/cell", response_model=Optional[TimesheetEntryRead])
def save_cell(
    payload: TimesheetCellInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _check_cell_access(actor, payload.employee_id, db)
    _check_company_exists(db, payload.company_id)
    try:
        result = upsert_cell(
            db, actor,
            payload.employee_id, payload.work_date, payload.company_id, payload.hours,
        )
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    return result


@router.post("/cells/batch", response_model=TimesheetBatchResponse)
def save_cells_batch(
    payload: TimesheetBatchInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    for cell in payload.entries:
        _check_cell_access(actor, cell.employee_id, db)
        _check_company_exists(db, cell.company_id)

    cells = [(c.employee_id, c.work_date, c.company_id, c.hours) for c in payload.entries]
    try:
        results = upsert_cells_batch(db, actor, cells)
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    return TimesheetBatchResponse(entries=results)


# ── Period workflow ───────────────────────────────────────────────────────────

@router.post("/periods/{period_id}/submit", response_model=TimesheetPeriodRead)
def submit_period(
    period_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = submit_for_review(db, period, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


@router.post("/periods/{period_id}/return", response_model=TimesheetPeriodRead)
def return_period(
    period_id: int,
    payload: StatusChangeReason,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = return_to_draft(db, period, actor, payload.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


@router.post("/periods/{period_id}/close", response_model=TimesheetPeriodRead)
def close_period_endpoint(
    period_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = close_period(db, period, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


@router.post("/periods/{period_id}/reopen", response_model=TimesheetPeriodRead)
def reopen_period_endpoint(
    period_id: int,
    payload: StatusChangeReason,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = reopen_period(db, period, actor, payload.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


# ── Autofill ─────────────────────────────────────────────────────────────────

@router.post("/autofill/preview", response_model=AutofillPreview)
def autofill_preview(
    payload: AutofillRequest,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    if actor.role not in ("admin", "accountant", "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    if actor.role == "manager" and payload.department_id is not None and payload.department_id != actor.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager может автозаполнить только свой отдел")
    try:
        return build_autofill_preview(db, actor, payload.year, payload.month, payload.department_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/autofill/apply")
def autofill_apply(
    payload: AutofillRequest,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    if actor.role not in ("admin", "accountant", "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    if actor.role == "manager" and payload.department_id is not None and payload.department_id != actor.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager может автозаполнить только свой отдел")
    try:
        preview = build_autofill_preview(db, actor, payload.year, payload.month, payload.department_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    try:
        count = apply_autofill(db, actor, preview)
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )

    log_action(
        db, actor, "timesheet", None, "timesheet_autofilled",
        after={"entries_created": count, "employees_count": preview.employees_processed},
    )
    db.commit()
    return {"entries_created": count, "employees_count": preview.employees_processed}


# ── Period history ────────────────────────────────────────────────────────────

@router.get("/periods/{period_id}/history", response_model=list[AuditLogRead])
def get_period_history(
    period_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _get_period_or_404(db, period_id)
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "timesheet_period",
            AuditLog.entity_id == period_id,
        )
        .order_by(AuditLog.created_at)
        .all()
    )
    result = []
    for log in logs:
        actor_emp = db.get(Employee, log.actor_id)
        result.append(
            AuditLogRead(
                id=log.id,
                actor_id=log.actor_id,
                actor_name=actor_emp.full_name if actor_emp else None,
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                action=log.action,
                before=log.before,
                after=log.after,
                reason=log.reason,
                created_at=str(log.created_at),
            )
        )
    return result


# ── Excel export ──────────────────────────────────────────────────────────────

@router.get("/{year}/{month}/export/excel")
def export_excel(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Экспорт табеля в Excel формата Т-13."""
    if actor.role not in ("admin", "accountant", "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён")
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    if actor.role == "manager" and department_id is not None:
        if actor.department_id != department_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён")

    from app.services.timesheet_export import generate_t13_excel
    excel_bytes = generate_t13_excel(db, actor, year, month, department_id)

    log_action(
        db, actor, "timesheet", None, "timesheet_exported_excel",
        after={"year": year, "month": month, "department_id": department_id},
    )
    db.commit()

    filename = f"timesheet_T13_{year}_{month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
