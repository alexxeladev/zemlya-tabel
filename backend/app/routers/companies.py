from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.companies import Company
from app.models.employees import Employee
from app.schemas.company import (
    CompanyCreate,
    CompanyOrderUpdate,
    CompanyRead,
    CompanyUpdate,
)
from app.services.company_order import company_order_by

router = APIRouter()

_admin_only = require_role("admin")


def _to_dict(obj: Company) -> dict:
    return {
        "id": obj.id, "code": obj.code, "name": obj.name, "inn": obj.inn,
        "short_name": obj.short_name, "sort_order": obj.sort_order,
        "is_active": obj.is_active,
    }


def _renumber(db: Session) -> None:
    """Пересобрать sort_order в плотный 1..N, сохранив текущий порядок.

    Ручной ввод числа в форме допускает дубли («поставить 2» рядом с уже
    существующей двойкой). Порядок от этого не ломается — второй ключ
    сортировки id, — но список выглядит странно, и следующая правка попадёт
    не туда, куда админ целился. Поэтому после любой правки порядка значения
    нормализуются.
    """
    for i, company in enumerate(
        db.query(Company).order_by(*company_order_by()).all(), start=1
    ):
        company.sort_order = i


@router.get("", response_model=list[CompanyRead])
def list_companies(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return db.query(Company).order_by(*company_order_by()).all()


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    if db.query(Company).filter(Company.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    # Новая компания встаёт в конец списка — молча вклиниваться в настроенный
    # порядок она не должна.
    last = db.query(func.max(Company.sort_order)).scalar() or 0
    company = Company(
        code=payload.code, name=payload.name, inn=payload.inn,
        short_name=payload.short_name, sort_order=last + 1, is_active=True,
    )
    db.add(company)
    db.flush()
    log_action(db, actor, "company", company.id, "create", after=_to_dict(company))
    db.commit()
    db.refresh(company)
    return company


@router.put("/order", response_model=list[CompanyRead])
def reorder_companies(
    payload: CompanyOrderUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Задать порядок перечисления юрлиц целиком (стрелки ↑/↓ в «Оргструктуре»).

    Принимается ПОЛНЫЙ список id в нужном порядке: частичная перестановка
    оставила бы дыры и совпадающие sort_order, а порядок должен быть один на
    всю систему. Компании, которых нет в списке, уезжают в конец, сохраняя
    относительный порядок, — так неактивная компания не пропадает молча.
    """
    companies = db.query(Company).order_by(*company_order_by()).all()
    by_id = {c.id: c for c in companies}
    unknown = [cid for cid in payload.company_ids if cid not in by_id]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Компания не найдена: {unknown[0]}",
        )
    ordered = [by_id[cid] for cid in dict.fromkeys(payload.company_ids)]
    rest = [c for c in companies if c.id not in set(payload.company_ids)]
    before = [_to_dict(c) for c in companies]
    for i, company in enumerate(ordered + rest, start=1):
        company.sort_order = i
    db.flush()
    log_action(
        db, actor, "company", 0, "reorder",
        before={"order": before},
        after={"order": [_to_dict(c) for c in ordered + rest]},
    )
    db.commit()
    return db.query(Company).order_by(*company_order_by()).all()


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
    fields = payload.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(company, field, value)
    db.flush()
    if "sort_order" in fields:
        _renumber(db)
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
