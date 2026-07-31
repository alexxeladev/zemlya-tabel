from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class OrgEmployeeRead(BaseModel):
    """Сотрудник в дереве — только для показа и перехода в карточку."""
    id: int
    full_name: str
    tab_number: Optional[str] = None
    position: Optional[str] = None
    role: Optional[str] = None
    is_active: bool


class OrgDepartmentRead(BaseModel):
    id: int
    name: str
    code: str
    is_active: bool
    head_company_id: Optional[int] = None
    # Менеджеры отдела: чем руководят, а не где числятся (task_org_structure ч.2)
    managers: list[OrgEmployeeRead]
    # employee_count отдаётся отдельно, чтобы свёрнутый узел показывал количество,
    # не разворачивая список сотрудников.
    employee_count: int
    employees: list[OrgEmployeeRead]


class OrgCompanyRead(BaseModel):
    id: int
    code: str
    name: str
    inn: Optional[str] = None
    is_active: bool
    departments: list[OrgDepartmentRead]


class OrgTreeRead(BaseModel):
    """Дерево Компания → Отдел → Сотрудники.

    Головная компания отдела — только группировка. Сотрудник при этом
    по-прежнему может работать на несколько юрлиц: расчёт ЗП смотрит на часы
    и проценты, а не на положение отдела в дереве.
    """
    companies: list[OrgCompanyRead]
    # Отделы без головной компании и сотрудники без отдела — иначе они пропали бы
    # из дерева вместе с возможностью их починить.
    departments_without_company: list[OrgDepartmentRead]
    employees_without_department: list[OrgEmployeeRead]
