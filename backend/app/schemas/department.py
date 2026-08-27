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
    # Фонд ночных смен на месяц; не задан — дефолт модели (100 000).
    night_shift_fund: Optional[Decimal] = None
    # Распределять зарплату отдела по заявкам на подбор (task_hr_applications).
    uses_applications_distribution: Optional[bool] = None


class DepartmentRead(DepartmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    # Из фонда вычисляется ставка ночной смены и лимит их числа за месяц
    # (task_night_shifts_rework) — деньги, поэтому табельщику не отдаётся.
    night_shift_fund: Optional[Decimal] = None
    # Зарплата отдела делится по заявкам на подбор, а не по каскаду
    # (task_hr_applications). Не деньги, а правило — видно всем, кто видит отдел.
    uses_applications_distribution: bool = False


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    head_company_id: Optional[int] = None
    night_shift_fund: Optional[Decimal] = None
    uses_applications_distribution: Optional[bool] = None
    is_active: Optional[bool] = None


class DepartmentManagerRead(BaseModel):
    """Менеджер или табельщик отдела — краткая карточка для дерева оргструктуры.

    `role` нужна, чтобы отличить руководителя от табельщика: связь у них одна
    (`managed_departments`), а права разные (task_timekeeper_role).
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    position: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None


class DepartmentManagersRead(BaseModel):
    department_id: int
    managers: list[DepartmentManagerRead]


class DepartmentManagersUpdate(BaseModel):
    """Полный набор менеджеров и табельщиков отдела: что прислали, то и будет
    (пусто — снять всех)."""
    employee_ids: list[int]


class DepartmentSharesRead(BaseModel):
    """Дефолт распределения по юрлицам на уровне отдела (task_distribution_v2 ч.3)."""
    department_id: int
    shares: list[CompanyShareInput]
    percent_sum: Decimal


class DepartmentSharesUpdate(BaseModel):
    shares: list[CompanyShareInput]


# ── Перенос отдела в другую компанию (task_move_department) ───────────────────

class DepartmentMoveRequest(BaseModel):
    target_company_id: int


class DepartmentMoveMonth(BaseModel):
    year: int
    month: int


class DepartmentMovePreview(BaseModel):
    """Что будет затронуто переносом — показывается в диалоге до подтверждения."""
    department_id: int
    department_name: str
    source_company_id: Optional[int] = None
    source_company_name: Optional[str] = None
    target_company_id: int
    target_company_name: str
    employee_count: int
    position_count: int
    #: Рабочие места тех же людей в ДРУГИХ отделах — они не переносятся.
    untouched_position_count: int
    #: Закрытые месяцы отдела: их расклад по юрлицам будет зафиксирован как есть.
    closed_months: list[DepartmentMoveMonth]
    #: У скольких позиций задан явный %, не включающий целевую компанию.
    stale_share_position_count: int
    #: Дефолт распределения самого отдела не включает целевую компанию.
    department_shares_stale: bool
    #: Ячеек часов в незакрытых месяцах, которые сменят юрлицо на целевое.
    entries_to_reattribute: int


class DepartmentMoveResult(BaseModel):
    department_id: int
    target_company_id: int
    positions_moved: int
    employees_affected: int
    closed_months_frozen: int
    override_rows_written: int
    entries_reattributed: int
