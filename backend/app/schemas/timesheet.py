from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.company import CompanyRead
from app.schemas.employee import EmployeeRead
from app.schemas.timesheet_period import TimesheetPeriodRead


class TimesheetEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: int
    work_date: date
    company_id: int
    hours: Decimal


class TimesheetCellInput(BaseModel):
    employee_id: int
    work_date: date
    company_id: int
    hours: Decimal = Field(ge=0, le=24)


class TimesheetMonthQuery(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    department_id: int | None = None


class TimesheetMonthResponse(BaseModel):
    year: int
    month: int
    employees: list[EmployeeRead]
    companies: list[CompanyRead]
    entries: list[TimesheetEntryRead]
    periods: list[TimesheetPeriodRead]
    extra_companies_by_employee: dict[int, list[int]] = {}


class TimesheetBatchInput(BaseModel):
    entries: list[TimesheetCellInput]


class TimesheetBatchResponse(BaseModel):
    entries: list[TimesheetEntryRead | None]


class AutofillSkippedEmployee(BaseModel):
    employee_id: int
    employee_name: str
    reason: str


class AutofillPreview(BaseModel):
    year: int
    month: int
    entries_to_create: list[TimesheetCellInput]
    cells_skipped: int
    employees_processed: int
    employees_skipped: list[AutofillSkippedEmployee]


class AutofillRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    department_id: int | None = None
