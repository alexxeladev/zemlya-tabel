from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.absence import AbsenceRead
from app.schemas.company import CompanyRead
from app.schemas.employee import EmployeeRead
from app.schemas.payout import AdjustmentRead
from app.schemas.payroll import PayrollSummaryRead
from app.schemas.position import EmployeePositionRead
from app.schemas.timesheet_period import TimesheetPeriodRead


class TimesheetEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: int
    # Рабочее место, на которое отработаны часы (task_positions ч.A). NULL —
    # строка заведена до появления позиций, относится к основной.
    position_id: int | None = None
    work_date: date
    company_id: int
    hours: int


class TimesheetCellInput(BaseModel):
    employee_id: int
    # Не задано — часы уходят на ОСНОВНУЮ позицию (выбор рабочего места в
    # табеле появится в части B).
    position_id: int | None = None
    work_date: date
    company_id: int
    hours: int = Field(ge=0, le=24)


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
    # Рабочие места, ВИДИМЫЕ актору в этом отделе (task_positions ч.B): табель
    # строит по строке на позицию. У совместителя с работой в двух отделах в
    # табеле отдела видна только его позиция; менеджеру — только его отделы.
    positions_by_employee: dict[int, list[EmployeePositionRead]] = {}
    # Коды отсутствий (ОТ/ДО/Б/Н) — видны всем, включая employee (свои дни)
    absences: list[AbsenceRead] = []
    payroll: PayrollSummaryRead | None = None
    adjustments: list[AdjustmentRead] = []


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
