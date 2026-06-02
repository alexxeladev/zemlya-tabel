from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.company import CompanyRead
from app.schemas.department import DepartmentRead
from app.schemas.schedule import ScheduleRead


class EmployeeBase(BaseModel):
    tab_number: Optional[str] = None
    full_name: str
    position: Optional[str] = None
    department_id: int
    schedule_id: int
    default_company_id: int
    rate: Decimal
    is_active: bool = True
    hire_date: Optional[datetime.date] = None
    dismissal_date: Optional[datetime.date] = None


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeRead(EmployeeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    department: Optional[DepartmentRead] = None
    schedule: Optional[ScheduleRead] = None
    default_company: Optional[CompanyRead] = None


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
