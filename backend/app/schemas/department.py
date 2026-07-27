from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.payroll_statement import CompanyShareInput


class DepartmentBase(BaseModel):
    name: str
    code: str


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentRead(DepartmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    is_active: Optional[bool] = None


class DepartmentSharesRead(BaseModel):
    """Дефолт распределения по юрлицам на уровне отдела (task_distribution_v2 ч.3)."""
    department_id: int
    shares: list[CompanyShareInput]
    percent_sum: Decimal


class DepartmentSharesUpdate(BaseModel):
    shares: list[CompanyShareInput]
