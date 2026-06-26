from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.companies import Company
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.loan_deductions import LoanDeduction
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.payout import (
    AdjustmentCreate,
    AdjustmentRead,
    LoanOverrideInput,
)
from app.schemas.payroll import (
    PayrollSummaryRead,
)
from app.schemas.payroll_statement import (
    DistributionOverrideInput,
    PayrollStatementRead,
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
from app.models.company_shares import CompanyShareOverride
from app.services.payroll_statement import (
    build_payroll_statement,
    build_payroll_summary,
)
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


def _require_finance_role(actor: Employee) -> None:
    """Премии/KPI/удержания/займ видят и правят только admin/accountant/manager."""
    if actor.role not in ("admin", "accountant", "manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")


def _load_adjustments(
    db: Session, employees: list[Employee], year: int, month: int
) -> list[AdjustmentRead]:
    emp_ids = [e.id for e in employees]
    if not emp_ids:
        return []
    rows = (
        db.query(EmployeeAdjustment)
        .filter(
            EmployeeAdjustment.employee_id.in_(emp_ids),
            EmployeeAdjustment.year == year,
            EmployeeAdjustment.month == month,
        )
        .order_by(EmployeeAdjustment.created_at)
        .all()
    )
    return [
        AdjustmentRead(
            id=r.id,
            employee_id=r.employee_id,
            year=r.year,
            month=r.month,
            kind=r.kind,
            amount=r.amount,
            reason=r.reason,
            created_by_id=r.created_by_id,
            created_at=str(r.created_at) if r.created_at else None,
        )
        for r in rows
    ]


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
# Единый расчёт ЗП (табель + ведомость) живёт в app.services.payroll_statement —
# здесь только тонкая обёртка, чтобы не дублировать формулы.

def _build_payroll_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
) -> PayrollSummaryRead:
    return build_payroll_summary(db, employees, entries, year, month)


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


# ── Payroll statement: сводная ведомость + распределение по % (задача 3.11b) ───

@router.get("/{year}/{month}/statement", response_model=PayrollStatementRead)
def get_payroll_statement(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    if actor.role == "manager" and department_id is not None and department_id != actor.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    return build_payroll_statement(db, employees, entries, year, month)


@router.put("/distribution", status_code=status.HTTP_200_OK)
def set_distribution_override(
    payload: DistributionOverrideInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Переопределить распределение по компаниям на конкретный месяц (правка в
    ведомости). Заменяет весь набор процентов сотрудника за этот период."""
    _require_finance_role(actor)
    _check_cell_access(actor, payload.employee_id, db)
    if not (1 <= payload.month <= 12) or not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month")
    for s in payload.shares:
        _check_company_exists(db, s.company_id)
        if s.percent < 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Процент не может быть отрицательным")

    # Полностью заменяем набор за период.
    db.query(CompanyShareOverride).filter(
        CompanyShareOverride.employee_id == payload.employee_id,
        CompanyShareOverride.year == payload.year,
        CompanyShareOverride.month == payload.month,
    ).delete(synchronize_session=False)
    for s in payload.shares:
        if s.percent <= 0:
            continue
        db.add(CompanyShareOverride(
            employee_id=payload.employee_id,
            company_id=s.company_id,
            year=payload.year,
            month=payload.month,
            percent=s.percent,
            created_by_id=actor.id,
        ))
    log_action(
        db, actor, "company_share_override", payload.employee_id, "set",
        after={"year": payload.year, "month": payload.month,
               "shares": {s.company_id: str(s.percent) for s in payload.shares}},
    )
    db.commit()
    return {"employee_id": payload.employee_id, "year": payload.year, "month": payload.month}


@router.delete(
    "/distribution/{employee_id}/{year}/{month}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_distribution_override(
    employee_id: int,
    year: int,
    month: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Убрать переопределение — вернуть проценты по умолчанию из карточки."""
    _require_finance_role(actor)
    _check_cell_access(actor, employee_id, db)
    deleted = db.query(CompanyShareOverride).filter(
        CompanyShareOverride.employee_id == employee_id,
        CompanyShareOverride.year == year,
        CompanyShareOverride.month == month,
    ).delete(synchronize_session=False)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Переопределение не найдено")
    log_action(db, actor, "company_share_override", employee_id, "delete",
               before={"year": year, "month": month})
    db.commit()


@router.get("/{year}/{month}/statement/export/excel")
def export_statement_excel(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Выгрузка сводной ведомости «Расчёт ЗП» в Excel (задача 3.11b п.3)."""
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    if actor.role == "manager" and department_id is not None and department_id != actor.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    statement = build_payroll_statement(db, employees, entries, year, month)

    from app.services.payroll_statement_export import generate_statement_excel
    excel_bytes = generate_statement_excel(statement)

    log_action(
        db, actor, "payroll_statement", None, "statement_exported_excel",
        after={"year": year, "month": month, "department_id": department_id},
    )
    db.commit()

    filename = f"vedomost_{year}_{month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    adjustments: list[AdjustmentRead] = []
    if actor.role in ("admin", "accountant", "manager"):
        adjustments = _load_adjustments(db, employees, year, month)
        if include_payroll:
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
        adjustments=adjustments,
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


# ── Adjustments: премии / KPI / аванс (задача 3.11a) ───────────────────────────

@router.get("/{year}/{month}/adjustments", response_model=list[AdjustmentRead])
def list_adjustments(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    if actor.role == "manager" and department_id is not None and department_id != actor.department_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    return _load_adjustments(db, employees, year, month)


@router.post("/adjustments", response_model=AdjustmentRead, status_code=status.HTTP_201_CREATED)
def create_adjustment(
    payload: AdjustmentCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    # _check_cell_access проверяет видимость сотрудника по роли (manager — свой отдел)
    _check_cell_access(actor, payload.employee_id, db)
    if not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year")

    adj = EmployeeAdjustment(
        employee_id=payload.employee_id,
        year=payload.year,
        month=payload.month,
        kind=payload.kind,
        amount=payload.amount,
        reason=payload.reason,
        created_by_id=actor.id,
    )
    db.add(adj)
    db.flush()
    log_action(
        db, actor, "employee_adjustment", adj.id, "create",
        after={"employee_id": adj.employee_id, "year": adj.year, "month": adj.month,
               "kind": adj.kind, "amount": str(adj.amount), "reason": adj.reason},
    )
    db.commit()
    db.refresh(adj)
    return AdjustmentRead(
        id=adj.id, employee_id=adj.employee_id, year=adj.year, month=adj.month,
        kind=adj.kind, amount=adj.amount, reason=adj.reason,
        created_by_id=adj.created_by_id, created_at=str(adj.created_at) if adj.created_at else None,
    )


@router.delete("/adjustments/{adjustment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_adjustment(
    adjustment_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    adj = db.get(EmployeeAdjustment, adjustment_id)
    if not adj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
    _check_cell_access(actor, adj.employee_id, db)
    log_action(
        db, actor, "employee_adjustment", adj.id, "delete",
        before={"employee_id": adj.employee_id, "year": adj.year, "month": adj.month,
                "kind": adj.kind, "amount": str(adj.amount)},
    )
    db.delete(adj)
    db.commit()


# ── Loan: ручная правка удержания за месяц (задача 3.11a) ───────────────────────

@router.post("/loan-override", status_code=status.HTTP_200_OK)
def set_loan_override(
    payload: LoanOverrideInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Скорректировать сумму удержания по займу за конкретный месяц."""
    _require_finance_role(actor)
    target = _check_cell_access(actor, payload.employee_id, db)
    if target.loan_amount is None or target.loan_term_months is None or target.loan_start_date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="У сотрудника не настроен займ",
        )
    if not (1 <= payload.month <= 12) or not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month")

    from app.services.payout import load_loan_overrides, loan_month_state

    # Плановая доля на этот месяц (справочно) — без учёта самой правки этого месяца.
    overrides = load_loan_overrides(db, [target.id]).get(target.id, {})
    overrides.pop((payload.year, payload.month), None)
    state = loan_month_state(
        target.loan_amount, target.loan_term_months, target.loan_start_date,
        payload.year, payload.month, overrides,
    )
    planned = state.planned if state else payload.actual_amount

    existing = (
        db.query(LoanDeduction)
        .filter(
            LoanDeduction.employee_id == payload.employee_id,
            LoanDeduction.year == payload.year,
            LoanDeduction.month == payload.month,
        )
        .first()
    )
    if existing:
        before = {"actual_amount": str(existing.actual_amount)}
        existing.actual_amount = payload.actual_amount
        existing.planned_amount = planned
        db.flush()
        log_action(db, actor, "loan_deduction", existing.id, "update",
                   before=before, after={"actual_amount": str(payload.actual_amount)})
        row = existing
    else:
        row = LoanDeduction(
            employee_id=payload.employee_id,
            year=payload.year,
            month=payload.month,
            planned_amount=planned,
            actual_amount=payload.actual_amount,
            created_by_id=actor.id,
        )
        db.add(row)
        db.flush()
        log_action(db, actor, "loan_deduction", row.id, "create",
                   after={"year": payload.year, "month": payload.month,
                          "actual_amount": str(payload.actual_amount)})
    db.commit()
    return {"employee_id": payload.employee_id, "year": payload.year,
            "month": payload.month, "planned_amount": str(planned),
            "actual_amount": str(payload.actual_amount)}


@router.delete("/loan-override/{employee_id}/{year}/{month}", status_code=status.HTTP_204_NO_CONTENT)
def delete_loan_override(
    employee_id: int,
    year: int,
    month: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Убрать ручную правку — вернуть плановое удержание за месяц."""
    _require_finance_role(actor)
    _check_cell_access(actor, employee_id, db)
    row = (
        db.query(LoanDeduction)
        .filter(
            LoanDeduction.employee_id == employee_id,
            LoanDeduction.year == year,
            LoanDeduction.month == month,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правка не найдена")
    log_action(db, actor, "loan_deduction", row.id, "delete",
               before={"actual_amount": str(row.actual_amount)})
    db.delete(row)
    db.commit()
