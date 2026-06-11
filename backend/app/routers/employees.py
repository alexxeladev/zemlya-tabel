import secrets
import string
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.core.security import hash_password
from app.database import get_db
from app.models.employees import Employee
from app.schemas.employee import (
    DismissalRequest,
    EmployeeAccessGrant,
    EmployeeAccessUpdate,
    EmployeeCreate,
    EmployeeRead,
    EmployeeUpdate,
)

router = APIRouter()

_admin_only = require_role("admin")


def _to_dict(emp: Employee) -> dict:
    return {
        "id": emp.id,
        "tab_number": emp.tab_number,
        "full_name": emp.full_name,
        "position": emp.position,
        "department_id": emp.department_id,
        "schedule_id": emp.schedule_id,
        "default_company_id": emp.default_company_id,
        "rate": str(emp.rate) if emp.rate is not None else None,
        "is_active": emp.is_active,
        "email": emp.email,
        "role": emp.role,
    }


def _gen_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


# ── List / Get ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[EmployeeRead])
def list_employees(
    department_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        emp = db.query(Employee).filter(Employee.id == current_user.id).all()
        return emp

    q = db.query(Employee)

    if current_user.role == "manager":
        if current_user.department_id is None:
            return []
        q = q.filter(Employee.department_id == current_user.department_id)
    elif department_id is not None:
        q = q.filter(Employee.department_id == department_id)

    if is_active is not None:
        q = q.filter(Employee.is_active == is_active)

    if search:
        pattern = f"%{search}%"
        q = q.filter(
            Employee.full_name.ilike(pattern) | Employee.tab_number.ilike(pattern)
        )

    return q.all()


@router.get("/{emp_id}", response_model=EmployeeRead)
def get_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if current_user.role == "employee":
        if current_user.id != emp_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if current_user.role == "manager":
        if emp.department_id != current_user.department_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    return emp


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = Employee(
        tab_number=payload.tab_number,
        full_name=payload.full_name,
        position=payload.position,
        department_id=payload.department_id,
        schedule_id=payload.schedule_id,
        default_company_id=payload.default_company_id,
        rate=payload.rate,
        is_active=payload.is_active,
        hire_date=payload.hire_date,
        dismissal_date=payload.dismissal_date,
    )

    if payload.access:
        if db.query(Employee).filter(Employee.email == payload.access.email).first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
        emp.email = payload.access.email
        emp.hashed_password = hash_password(payload.access.initial_password)
        emp.role = payload.access.role
        emp.must_change_password = True

    db.add(emp)
    db.flush()
    log_action(db, actor, "employee", emp.id, "create", after=_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


# ── Update ─────────────────────────────────────────────────────────────────────

# Правка 3.9-1: manager может только просматривать сотрудников. Любое изменение —
# только admin (откат правки 3.8, где manager редактировал свой отдел).


@router.patch("/{emp_id}", response_model=EmployeeRead)
def update_employee(
    emp_id: int,
    payload: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    data = payload.model_dump(exclude_unset=True)

    before = _to_dict(emp)
    for field, value in data.items():
        setattr(emp, field, value)
    db.flush()
    log_action(db, actor, "employee", emp.id, "update", before=before, after=_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


# ── Soft delete ────────────────────────────────────────────────────────────────

@router.delete("/{emp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.is_system_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя удалить системного администратора")
    before = _to_dict(emp)
    emp.is_active = False
    db.flush()
    log_action(db, actor, "employee", emp.id, "delete", before=before)
    db.commit()


# ── Dismiss / Rehire ──────────────────────────────────────────────────────────

@router.post("/{emp_id}/dismiss", response_model=EmployeeRead)
def dismiss_employee(
    emp_id: int,
    payload: DismissalRequest,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.is_system_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя уволить системного администратора")
    if not emp.is_active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Сотрудник уже уволен")

    before = _to_dict(emp)
    emp.is_active = False
    emp.dismissal_date = payload.dismissal_date
    db.flush()
    log_action(db, actor, "employee", emp.id, "employee_dismissed",
               before=before, after={"dismissal_date": str(payload.dismissal_date)})
    db.commit()
    db.refresh(emp)
    return emp


@router.post("/{emp_id}/rehire", response_model=EmployeeRead)
def rehire_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.is_active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Сотрудник уже активен")

    before = _to_dict(emp)
    emp.is_active = True
    emp.dismissal_date = None
    db.flush()
    log_action(db, actor, "employee", emp.id, "employee_rehired",
               before=before, after={"is_active": True})
    db.commit()
    db.refresh(emp)
    return emp


# ── Access management ──────────────────────────────────────────────────────────

@router.post("/{emp_id}/access", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def grant_access(
    emp_id: int,
    payload: EmployeeAccessGrant,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.email is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Employee already has system access")
    if db.query(Employee).filter(Employee.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    emp.email = payload.email
    emp.hashed_password = hash_password(payload.initial_password)
    emp.role = payload.role
    emp.must_change_password = True
    db.flush()
    log_action(db, actor, "employee", emp.id, "access_granted", after={"email": emp.email, "role": emp.role})
    db.commit()
    db.refresh(emp)
    return emp


@router.patch("/{emp_id}/access", response_model=EmployeeRead)
def update_access_role(
    emp_id: int,
    payload: EmployeeAccessUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.email is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Employee has no system access")
    if emp.is_system_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя сменить роль системного администратора")

    before_role = emp.role
    emp.role = payload.role
    db.flush()
    log_action(db, actor, "employee", emp.id, "role_changed",
               before={"role": before_role}, after={"role": emp.role})
    db.commit()
    db.refresh(emp)
    return emp


@router.post("/{emp_id}/reset-password")
def reset_password(
    emp_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.email is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Employee has no system access")

    temp_password = _gen_temp_password()
    emp.hashed_password = hash_password(temp_password)
    emp.must_change_password = True
    db.flush()
    log_action(db, actor, "employee", emp.id, "reset_password")
    db.commit()
    return {"temp_password": temp_password}


@router.delete("/{emp_id}/access", status_code=status.HTTP_204_NO_CONTENT)
def revoke_access(
    emp_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.is_system_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя отобрать доступ у системного администратора")
    if emp.email is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Employee has no system access")

    before = {"email": emp.email, "role": emp.role}
    emp.email = None
    emp.hashed_password = None
    emp.role = None
    emp.must_change_password = False
    db.flush()
    log_action(db, actor, "employee", emp.id, "access_revoked", before=before)
    db.commit()
