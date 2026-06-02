from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.companies import Company
from app.models.employees import Employee
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUpdate

router = APIRouter()

_admin_only = require_role("admin")


def _to_dict(obj: Company) -> dict:
    return {"id": obj.id, "code": obj.code, "name": obj.name, "inn": obj.inn, "is_active": obj.is_active}


@router.get("", response_model=list[CompanyRead])
def list_companies(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return db.query(Company).all()


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    if db.query(Company).filter(Company.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    company = Company(code=payload.code, name=payload.name, inn=payload.inn, is_active=True)
    db.add(company)
    db.flush()
    log_action(db, actor, "company", company.id, "create", after=_to_dict(company))
    db.commit()
    db.refresh(company)
    return company


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


@router.patch("/{company_id}", response_model=CompanyRead)
def update_company(
    company_id: int,
    payload: CompanyUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    before = _to_dict(company)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    db.flush()
    log_action(db, actor, "company", company.id, "update", before=before, after=_to_dict(company))
    db.commit()
    db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    active_employees = [e for e in company.employees if e.is_active]
    if active_employees:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Нельзя удалить: у этой компании {len(active_employees)} сотрудников",
        )
    before = _to_dict(company)
    company.is_active = False
    db.flush()
    log_action(db, actor, "company", company.id, "delete", before=before)
    db.commit()
