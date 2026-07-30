from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.payroll_statement import CompanyShareInput


class DepartmentBase(BaseModel):
    name: str
    code: str
    # Головная компания — группировка отдела в дереве оргструктуры.
    # На расчёт ЗП (часы и проценты по юрлицам) НЕ влияет.
    head_company_id: Optional[int] = None


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentRead(DepartmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    head_company_id: Optional[int] = None
    is_active: Optional[bool] = None


class DepartmentManagerRead(BaseModel):
    """Менеджер отдела — краткая карточка для дерева оргструктуры."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    position: Optional[str] = None
    email: Optional[str] = None


class DepartmentManagersRead(BaseModel):
    department_id: int
    managers: list[DepartmentManagerRead]


class DepartmentManagersUpdate(BaseModel):
    """Полный набор менеджеров отдела: что прислали, то и будет (пусто — снять всех)."""
    employee_ids: list[int]


class DepartmentSharesRead(BaseModel):
    """Дефолт распределения по юрлицам на уровне отдела (task_distribution_v2 ч.3)."""
    department_id: int
    shares: list[CompanyShareInput]
    percent_sum: Decimal


class DepartmentSharesUpdate(BaseModel):
    shares: list[CompanyShareInput]
