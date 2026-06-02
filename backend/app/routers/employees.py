from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.employees import Employee
from app.models.users import User, UserRole
from app.schemas.employee import EmployeeCreate, EmployeeRead, EmployeeUpdate

router = APIRouter()

_admin_only = require_role("admin")


def _to_dict(obj: Employee) -> dict:
    return {
        "id": obj.id,
        "tab_number": obj.tab_number,
        "full_name": obj.full_name,
        "position": obj.position,
        "department_id": obj.department_id,
        "schedule_id": obj.schedule_id,
        "default_company_id": obj.default_company_id,
        "rate": str(obj.rate),
        "is_active": obj.is_active,
    }


@router.get("", response_model=list[EmployeeRead])
def list_employees(
    department_id: Optional[int] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.employee:
        emp = db.query(Employee).filter(
            Employee.id == current_user.employee_id
        ).all()
        return emp

    q = db.query(Employee)

    if current_user.role == UserRole.manager:
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


@router.post("", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    emp = Employee(**payload.model_dump())
    db.add(emp)
    db.flush()
    log_action(db, actor, "employee", emp.id, "create", after=_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/{emp_id}", response_model=EmployeeRead)
def get_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if current_user.role == UserRole.employee:
        if current_user.employee_id != emp_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if current_user.role == UserRole.manager:
        if emp.department_id != current_user.department_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    return emp


@router.patch("/{emp_id}", response_model=EmployeeRead)
def update_employee(
    emp_id: int,
    payload: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    before = _to_dict(emp)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)
    db.flush()
    log_action(db, actor, "employee", emp.id, "update", before=before, after=_to_dict(emp))
    db.commit()
    db.refresh(emp)
    return emp


@router.delete("/{emp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    emp_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    before = _to_dict(emp)
    emp.is_active = False
    db.flush()
    log_action(db, actor, "employee", emp.id, "delete", before=before)
    db.commit()
