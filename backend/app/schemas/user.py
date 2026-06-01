from __future__ import annotations

import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.users import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    role: UserRole
    department_id: Optional[int] = None
    employee_id: Optional[int] = None
    is_active: bool = True


class UserCreate(UserBase):
    password: str


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    must_change_password: bool
    last_login_at: Optional[datetime.datetime] = None


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    department_id: Optional[int] = None
    employee_id: Optional[int] = None
    is_active: Optional[bool] = None
