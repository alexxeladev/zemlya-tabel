import secrets
import string
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import require_role
from app.core.security import hash_password
from app.database import get_db
from app.models.users import User, UserRole
from app.schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter()

_admin_only = require_role("admin")


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "department_id": user.department_id,
        "employee_id": user.employee_id,
        "is_active": user.is_active,
    }


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        department_id=payload.department_id,
        employee_id=payload.employee_id,
        is_active=payload.is_active,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    log_action(db, actor, "user", user.id, "create", after=_user_to_dict(user))
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=list[UserRead])
def list_users(
    role: Optional[UserRole] = Query(default=None),
    department_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(_admin_only),
):
    q = db.query(User)
    if role is not None:
        q = q.filter(User.role == role)
    if department_id is not None:
        q = q.filter(User.department_id == department_id)
    if is_active is not None:
        q = q.filter(User.is_active == is_active)
    return q.all()


@router.get("/{user_id}", response_model=UserRead)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_admin_only),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    before = _user_to_dict(user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.flush()
    log_action(db, actor, "user", user.id, "update", before=before, after=_user_to_dict(user))
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(12))
    user.hashed_password = hash_password(temp_password)
    user.must_change_password = True
    db.flush()
    log_action(db, actor, "user", user.id, "reset_password")
    db.commit()
    return {"temp_password": temp_password}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    before = _user_to_dict(user)
    user.is_active = False
    db.flush()
    log_action(db, actor, "user", user.id, "delete", before=before)
    db.commit()
