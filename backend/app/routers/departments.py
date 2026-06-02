from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.departments import Department
from app.models.users import User, UserRole
from app.schemas.department import DepartmentCreate, DepartmentRead, DepartmentUpdate

router = APIRouter()

_admin_only = require_role("admin")
_readers = require_role("admin", "accountant", "manager")


def _to_dict(obj: Department) -> dict:
    return {"id": obj.id, "name": obj.name, "code": obj.code, "is_active": obj.is_active}


@router.get("", response_model=list[DepartmentRead])
def list_departments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.employee:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return db.query(Department).all()


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    if db.query(Department).filter(Department.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    dept = Department(name=payload.name, code=payload.code, is_active=True)
    db.add(dept)
    db.flush()
    log_action(db, actor, "department", dept.id, "create", after=_to_dict(dept))
    db.commit()
    db.refresh(dept)
    return dept


@router.get("/{dept_id}", response_model=DepartmentRead)
def get_department(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.employee:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return dept


@router.patch("/{dept_id}", response_model=DepartmentRead)
def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    before = _to_dict(dept)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(dept, field, value)
    db.flush()
    log_action(db, actor, "department", dept.id, "update", before=before, after=_to_dict(dept))
    db.commit()
    db.refresh(dept)
    return dept


@router.delete("/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    dept_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    active_employees = [e for e in dept.employees if e.is_active]
    if active_employees:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Нельзя удалить: на этом отделе {len(active_employees)} сотрудников",
        )
    before = _to_dict(dept)
    dept.is_active = False
    db.flush()
    log_action(db, actor, "department", dept.id, "delete", before=before)
    db.commit()
