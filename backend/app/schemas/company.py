from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CompanyBase(BaseModel):
    code: str
    name: str
    inn: Optional[str] = None


class CompanyCreate(CompanyBase):
    pass


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class CompanyUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    inn: Optional[str] = None
    is_active: Optional[bool] = None
