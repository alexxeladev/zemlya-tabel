from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CompanyBase(BaseModel):
    code: str
    name: str
    inn: Optional[str] = None
    # Короткое название для узких колонок; пусто — выводится из name.
    short_name: Optional[str] = None


class CompanyCreate(CompanyBase):
    pass


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    # Порядок перечисления юрлиц — общий для всех экранов и выгрузок.
    sort_order: int = 0
    # Короткое имя, уже разрешённое бэком (short_name → name без правовой формы
    # → код): фронту не нужно повторять эту логику.
    display_name: str = ""


class CompanyUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    inn: Optional[str] = None
    short_name: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class CompanyOrderUpdate(BaseModel):
    """Полный порядок юрлиц: id в нужной последовательности."""
    company_ids: list[int]
