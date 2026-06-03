from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.companies import Company
from app.models.employees import Employee
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.timesheet import (
    AutofillPreview,
    AutofillRequest,
    TimesheetBatchInput,
    TimesheetBatchResponse,
    TimesheetCellInput,
    TimesheetEntryRead,
    TimesheetMonthResponse,
)
from app.schemas.timesheet_period import AuditLogRead, StatusChangeReason, TimesheetPeriodRead
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


# ── GET month ─────────────────────────────────────────────────────────────────

@router.get("/{year}/{month}", response_model=TimesheetMonthResponse)
def get_month(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
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

    return TimesheetMonthResponse(
        year=year,
        month=month,
        employees=employees,
        companies=companies,
        entries=entries,
        periods=periods,
        extra_companies_by_employee=extra_companies,
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
