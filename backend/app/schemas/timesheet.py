from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.absence import AbsenceRead
from app.schemas.quantity import (
    DepartmentQuantitiesRead,
    QuantityDistributionRow,
)
from app.schemas.company import CompanyRead
from app.schemas.employee import EmployeeRead
from app.schemas.night_shift import NightFundRead, NightShiftRead
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
    # Ночные смены (task_night_shifts_rework): отметки выходов в ночь и
    # состояние фонда отделов — из него считаются ставка и лимит числа смен.
    night_shifts: list[NightShiftRead] = []
    night_funds: list[NightFundRead] = []
    # Заявки на подбор отделов с флагом «распределение по заявкам»
    # (заявки у HR, АРМ у ИТ): им делится зарплата отдела вместо каскада.
    # Приходят вместе с табелем, чтобы блок ввода не требовал второго запроса;
    # отделов без флага здесь нет, и у нефинансовых ролей список пуст.
    quantities: list[DepartmentQuantitiesRead] = []
    # Суммы распределения по юрлицам для строк таких отделов — считаются на бэке
    # теми же числами, что ведомость (пусто, если расчёт не запрашивали).
    quantity_distribution: list[QuantityDistributionRow] = []
    payroll: PayrollSummaryRead | None = None
    adjustments: list[AdjustmentRead] = []
    # Личные отметки «строку проверил» (task_pilot_ux ч.3): id рабочих мест,
    # отмеченных ИМЕННО ЭТИМ пользователем в этом месяце. Едут вместе с
    # табелем одним запросом — по строке их было бы 70. Чужие сюда не
    # попадают никогда: выборка сужена по актору.
    checked_positions: list[int] = []


class RowCheckInput(BaseModel):
    """Одна отметка «проверено»: рабочее место + месяц. value=false — снять."""

    position_id: int
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    value: bool


class RowCheckRead(BaseModel):
    position_id: int
    year: int
    month: int
    checked: bool


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
