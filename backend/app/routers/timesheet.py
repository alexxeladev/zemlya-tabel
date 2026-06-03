from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.companies import Company
from app.models.employees import Employee
from app.schemas.timesheet import (
    TimesheetBatchInput,
    TimesheetBatchResponse,
    TimesheetCellInput,
    TimesheetEntryRead,
    TimesheetMonthResponse,
)
from app.services.timesheet import get_month_entries, upsert_cell, upsert_cells_batch, visible_employees_for_actor

router = APIRouter()


def _check_cell_access(actor: Employee, target_employee_id: int, db: Session) -> Employee:
    """Validate actor can edit target employee's timesheet. Returns target Employee."""
    target = db.get(Employee, target_employee_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if actor.role in ("admin", "accountant"):
        return target

    if actor.role == "manager":
        if target.department_id != actor.department_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return target

    # employee — only self
    if actor.id != target_employee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return target


def _check_company_exists(db: Session, company_id: int) -> None:
    if not db.get(Company, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


@router.get("/{year}/{month}", response_model=TimesheetMonthResponse)
def get_month(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month")

    employees = visible_employees_for_actor(db, actor, department_id)
    companies = db.query(Company).filter(Company.is_active == True).all()  # noqa: E712
    entries = get_month_entries(db, employees, year, month)

    return TimesheetMonthResponse(
        year=year,
        month=month,
        employees=employees,
        companies=companies,
        entries=entries,
    )


@router.put("/cell", response_model=Optional[TimesheetEntryRead])
def save_cell(
    payload: TimesheetCellInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _check_cell_access(actor, payload.employee_id, db)
    _check_company_exists(db, payload.company_id)
    result = upsert_cell(
        db, actor,
        payload.employee_id, payload.work_date, payload.company_id, payload.hours,
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
    results = upsert_cells_batch(db, actor, cells)
    return TimesheetBatchResponse(entries=results)
