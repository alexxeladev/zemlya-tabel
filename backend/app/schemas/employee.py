from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator, model_validator

from app.schemas.company import CompanyRead
from app.schemas.department import DepartmentRead
from app.schemas.schedule import ScheduleRead

EmployeeRoleType = Literal["admin", "manager", "accountant", "employee"]


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
    rate: Optional[Decimal] = None
    is_active: bool = True
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None


class EmployeeCreate(EmployeeBase):
    access: Optional[EmployeeAccessCreate] = None


class EmployeeRead(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    has_access: bool = False
    email: Optional[str] = None
    role: Optional[str] = None
    must_change_password: bool = False
    last_login_at: Optional[datetime.datetime] = None
    is_system_admin: bool = False

    department: Optional[DepartmentRead] = None
    schedule: Optional[ScheduleRead] = None
    default_company: Optional[CompanyRead] = None

    @model_validator(mode="after")
    def _compute_has_access(self) -> "EmployeeRead":
        self.has_access = self.email is not None
        return self


class EmployeeUpdate(BaseModel):
    tab_number: Optional[str] = None
    full_name: Optional[str] = None
    position: Optional[str] = None
    department_id: Optional[int] = None
    schedule_id: Optional[int] = None
    default_company_id: Optional[int] = None
    rate: Optional[Decimal] = None
    is_active: Optional[bool] = None
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None


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
