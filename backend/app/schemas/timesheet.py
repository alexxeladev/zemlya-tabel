from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.company import CompanyRead
from app.schemas.employee import EmployeeRead


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


class TimesheetBatchInput(BaseModel):
    entries: list[TimesheetCellInput]


class TimesheetBatchResponse(BaseModel):
    entries: list[TimesheetEntryRead | None]
