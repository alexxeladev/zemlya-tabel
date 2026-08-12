from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator


from app.schemas.company import CompanyRead
from app.schemas.department import DepartmentRead
from app.schemas.position import EmployeePositionRead
from app.schemas.schedule import ScheduleRead

EmployeeRoleType = Literal["admin", "manager", "accountant", "employee"]
WeekendPayType = Literal["coefficient", "fixed_rate"]
# Тип оплаты позиции: оклад / смены × ставка / часы × ставка за час
PayType = Literal["salary", "per_shift", "hourly"]


class EmployeeAccessCreate(BaseModel):
    """Credentials block when creating an employee with system access."""
    email: EmailStr
    role: EmployeeRoleType
    initial_password: str

    @field_validator("initial_password")
    @classmethod
    def _pwd_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class EmployeeBase(BaseModel):
    tab_number: Optional[str] = None
    full_name: str
    position: Optional[str] = None
    department_id: Optional[int] = None
    schedule_id: Optional[int] = None
    default_company_id: Optional[int] = None
    # Тип оплаты и его база — взаимоисключающие (task_positions ч.A):
    #   "salary"    — месячный оклад `rate`;
    #   "per_shift" — ставка за смену `shift_rate`;
    #   "hourly"    — ставка за час `hour_rate`.
    pay_type: PayType = "salary"
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    hour_rate: Optional[Decimal] = None
    weekend_pay_type: WeekendPayType = "coefficient"
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    # Праздничные — отдельная от выходных настройка (дефолт коэффициента 2.0)
    holiday_pay_type: WeekendPayType = "coefficient"
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None
    overtime_coefficient: Optional[Decimal] = None
    loan_amount: Optional[Decimal] = None
    loan_term_months: Optional[int] = None
    loan_start_date: Optional[datetime.date] = None
    is_active: bool = True
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None


class EmployeeCreate(EmployeeBase):
    access: Optional[EmployeeAccessCreate] = None


class EmployeeRead(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    has_access: bool = False
    status: Literal["active", "dismissed"] = "active"
    email: Optional[str] = None
    role: Optional[str] = None
    must_change_password: bool = False
    last_login_at: Optional[datetime.datetime] = None
    is_system_admin: bool = False

    department: Optional[DepartmentRead] = None
    schedule: Optional[ScheduleRead] = None
    default_company: Optional[CompanyRead] = None

    # Рабочие места (task_positions ч.B). Плоские поля выше — это ОСНОВНАЯ
    # позиция через compat-аксессоры; здесь виден весь список, включая
    # совместительство. У сотрудника без совместительства ровно один элемент.
    positions: list[EmployeePositionRead] = []

    # Чем менеджер РУКОВОДИТ (task_org_structure ч.2) — не путать с `department`,
    # где он числится. Фронт по этому полю строит селектор отделов менеджера.
    managed_department_ids: list[int] = []

    @model_validator(mode="after")
    def _compute_fields(self) -> "EmployeeRead":
        self.has_access = self.email is not None
        self.status = "dismissed" if not self.is_active else "active"
        return self


class DismissalRequest(BaseModel):
    dismissal_date: datetime.date


class EmployeeUpdate(BaseModel):
    tab_number: Optional[str] = None
    full_name: Optional[str] = None
    position: Optional[str] = None
    department_id: Optional[int] = None
    schedule_id: Optional[int] = None
    default_company_id: Optional[int] = None
    pay_type: Optional[PayType] = None
    rate: Optional[Decimal] = None
    shift_rate: Optional[Decimal] = None
    hour_rate: Optional[Decimal] = None
    weekend_pay_type: Optional[WeekendPayType] = None
    weekend_coefficient: Optional[Decimal] = None
    weekend_fixed_rate: Optional[Decimal] = None
    holiday_pay_type: Optional[WeekendPayType] = None
    holiday_coefficient: Optional[Decimal] = None
    holiday_fixed_rate: Optional[Decimal] = None
    overtime_coefficient: Optional[Decimal] = None
    loan_amount: Optional[Decimal] = None
    loan_term_months: Optional[int] = None
    loan_start_date: Optional[datetime.date] = None
    is_active: Optional[bool] = None
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None
    is_system_admin: Optional[bool] = None


class EmployeeAccessGrant(BaseModel):
    """Grant system access to an employee."""
    email: EmailStr
    role: EmployeeRoleType
    initial_password: str

    @field_validator("initial_password")
    @classmethod
    def _pwd_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class EmployeeAccessUpdate(BaseModel):
    """Update role only."""
    role: EmployeeRoleType
