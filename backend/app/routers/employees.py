import secrets
import string
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.core.security import hash_password
from app.database import get_db
from app.models.company_shares import EmployeeCompanyShare
from app.models.employees import Employee
from app.models.positions import (
    PAY_TYPE_BASE_FIELD,
    EmployeePosition,
)
from app.schemas.employee import (
    DismissalRequest,
    EmployeeAccessGrant,
    EmployeeAccessUpdate,
    EmployeeCreate,
    EmployeeRead,
    EmployeeUpdate,
)
from app.schemas.employee_import import EmployeeImportResult
from app.schemas.payroll_statement import (
    CompanyShareInput,
    EmployeeSharesRead,
    EmployeeSharesUpdate,
)
from app.schemas.position import (
    EmployeePositionCreate,
    EmployeePositionRead,
    EmployeePositionUpdate,
)
from app.services.company_shares import (
    SharesValidationError,
    load_department_shares,
    validate_shares,
)
from app.services.employee_import import (
    ImportFileError,
    generate_import_template,
    import_valid_rows,
    parse_import_file,
)
from app.services.employees import build_employee
from app.services.finance_masking import mask_employee, mask_employees, mask_position
from app.services.org_access import (
    accessible_department_ids,
    can_access_department,
    can_see_finances,
    hides_finances,
    is_department_scoped,
)
from app.services.positions import (
    PositionError,
    apply_position_fields,
    create_position,
    delete_position,
    in_department,
    in_departments,
    set_primary,
)
from app.services.reference_audit import (
    EMPLOYEE_SHARES_ENTITY,
    format_share_rows,
    record_change,
)

router = APIRouter()

_admin_only = require_role("admin")

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Тип оплаты → поле, в котором лежит его база (см. app.models.positions).
_PAY_TYPE_BASE_FIELD = PAY_TYPE_BASE_FIELD


def _to_dict(emp: Employee) -> dict:
    return {
        "id": emp.id,
        "tab_number": emp.tab_number,
        "full_name": emp.full_name,
        "position": emp.position,
        "department_id": emp.department_id,
        "schedule_id": emp.schedule_id,
        "default_company_id": emp.default_company_id,
        "pay_type": emp.pay_type,
        "rate": str(emp.rate) if emp.rate is not None else None,
        "shift_rate": str(emp.shift_rate) if emp.shift_rate is not None else None,
        "hour_rate": str(emp.hour_rate) if emp.hour_rate is not None else None,
        "weekend_pay_type": emp.weekend_pay_type,
        "weekend_coefficient": str(emp.weekend_coefficient) if emp.weekend_coefficient is not None else None,
        "weekend_fixed_rate": str(emp.weekend_fixed_rate) if emp.weekend_fixed_rate is not None else None,
        "holiday_pay_type": emp.holiday_pay_type,
        "holiday_coefficient": str(emp.holiday_coefficient) if emp.holiday_coefficient is not None else None,
        "holiday_fixed_rate": str(emp.holiday_fixed_rate) if emp.holiday_fixed_rate is not None else None,
        "overtime_coefficient": str(emp.overtime_coefficient) if emp.overtime_coefficient is not None else None,
        "loan_amount": str(emp.loan_amount) if emp.loan_amount is not None else None,
        "loan_term_months": emp.loan_term_months,
        "loan_start_date": str(emp.loan_start_date) if emp.loan_start_date is not None else None,
        "is_active": emp.is_active,
        "email": emp.email,
        "role": emp.role,
    }


def _gen_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _drop_managed_departments_if_not_scoped(emp: Employee) -> list[int]:
    """Привязка к отделам имеет смысл только у manager и timekeeper
    (task_org_structure ч.2, task_timekeeper_role): первый отделом руководит,
    второй ведёт его табель. Возвращает id отделов, с которых сотрудник снят, —
    для audit log."""
    if is_department_scoped(emp) or not emp.managed_departments:
        return []
    dropped = emp.managed_department_ids
    emp.managed_departments = []
    return dropped


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

    if is_department_scoped(current_user):
        # Сотрудники всех отделов, которыми руководит менеджер (task_org_structure
        # ч.2) или которые ведёт табельщик (task_timekeeper_role)
        dept_ids = accessible_department_ids(current_user, department_id)
        if not dept_ids:
            return []
        q = q.filter(in_departments(dept_ids))
    elif department_id is not None:
        q = q.filter(in_department(department_id))

    if is_active is not None:
        q = q.filter(Employee.is_active == is_active)

    if search:
        pattern = f"%{search}%"
        q = q.filter(
            Employee.full_name.ilike(pattern) | Employee.tab_number.ilike(pattern)
        )

    rows = q.all()
    if hides_finances(current_user):
        # Табельщику список сотрудников отдела виден, оклады в нём — нет
        return mask_employees([EmployeeRead.model_validate(e) for e in rows])
    return rows


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

    if is_department_scoped(current_user):
        if not can_access_department(current_user, emp.department_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if hides_finances(current_user):
        return mask_employee(EmployeeRead.model_validate(emp))
    return emp


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = build_employee(payload)

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
    # Смена типа оплаты гасит поля чужих типов: у окладника не должно остаться
    # ставки за смену или за час, у посменного — оклада (иначе расчёт молча
    # возьмёт не ту базу).
    for pay_type, base_field in _PAY_TYPE_BASE_FIELD.items():
        if emp.pay_type != pay_type:
            setattr(emp, base_field, None)
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
    # Роль сменили с «руководителя» — снимаем его со всех отделов, иначе он
    # остаётся в списке менеджеров отдела, уже ничем не руководя.
    dropped = _drop_managed_departments_if_not_scoped(emp)
    db.flush()
    log_action(db, actor, "employee", emp.id, "role_changed",
               before={"role": before_role, "managed_department_ids": dropped},
               after={"role": emp.role})
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

    before = {
        "email": emp.email,
        "role": emp.role,
        "managed_department_ids": emp.managed_department_ids,
    }
    emp.email = None
    emp.hashed_password = None
    emp.role = None
    emp.must_change_password = False
    # Без доступа в систему руководить отделами не может — связь снимаем.
    _drop_managed_departments_if_not_scoped(emp)
    db.flush()
    log_action(db, actor, "employee", emp.id, "access_revoked", before=before)
    db.commit()


# ── Позиции (рабочие места) сотрудника — task_positions ч.B ────────────────────
#
# Совместитель = несколько позиций, у каждой свои должность, тип оплаты и база,
# график, отдел, компания и коэффициенты. Ровно одна помечена «основная».
# Читать может любой, кто видит карточку; править — только admin.

def _sorted_positions(emp: Employee) -> list[EmployeePosition]:
    """Порядок в карточке: основная → активные → отключённые."""
    return sorted(
        emp.positions,
        key=lambda p: (not p.is_primary, not p.is_active, p.sort_order, p.id),
    )


def _position_or_404(emp: Employee, position_id: int) -> EmployeePosition:
    for pos in emp.positions:
        if pos.id == position_id:
            return pos
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Позиция не найдена")


def _position_dict(pos: EmployeePosition) -> dict:
    """Снимок позиции для audit log."""
    return {
        "title": pos.title,
        "is_primary": pos.is_primary,
        "is_active": pos.is_active,
        "department_id": pos.department_id,
        "schedule_id": pos.schedule_id,
        "company_id": pos.company_id,
        "pay_type": pos.pay_type,
        "rate": str(pos.rate) if pos.rate is not None else None,
        "shift_rate": str(pos.shift_rate) if pos.shift_rate is not None else None,
        "hour_rate": str(pos.hour_rate) if pos.hour_rate is not None else None,
    }


def _employee_for_read(db: Session, emp_id: int, actor: Employee) -> Employee:
    """Карточка сотрудника с проверкой видимости (те же правила, что у GET)."""
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if actor.role == "employee" and actor.id != emp_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if is_department_scoped(actor) and not any(
        can_access_department(actor, pos.department_id) for pos in emp.positions
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return emp


def _employee_for_write(db: Session, emp_id: int) -> Employee:
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return emp


@router.get("/{emp_id}/positions", response_model=list[EmployeePositionRead])
def list_positions(
    emp_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Все рабочие места сотрудника (основная первой). Менеджеру отдаём полный
    список — в карточке он видит, где ещё числится его сотрудник; скрывать чужие
    отделы имеет смысл в табеле, где по ним вводят часы."""
    emp = _employee_for_read(db, emp_id, current_user)
    positions = _sorted_positions(emp)
    if hides_finances(current_user):
        # Табельщику — должность/график/отдел рабочего места, но без ставок
        return [
            mask_position(EmployeePositionRead.model_validate(p)) for p in positions
        ]
    return positions


@router.post(
    "/{emp_id}/positions",
    response_model=EmployeePositionRead,
    status_code=status.HTTP_201_CREATED,
)
def add_position(
    emp_id: int,
    payload: EmployeePositionCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = _employee_for_write(db, emp_id)
    position = create_position(emp, payload.model_dump())
    db.flush()
    log_action(db, actor, "employee_position", position.id, "create",
               after={"employee_id": emp.id, **_position_dict(position)})
    db.commit()
    db.refresh(position)
    return position


@router.patch("/{emp_id}/positions/{position_id}", response_model=EmployeePositionRead)
def update_position(
    emp_id: int,
    position_id: int,
    payload: EmployeePositionUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = _employee_for_write(db, emp_id)
    position = _position_or_404(emp, position_id)
    before = _position_dict(position)
    data = payload.model_dump(exclude_unset=True)
    # Деактивировать основную нельзя — иначе сотрудник останется без рабочего
    # места, а расчёт молча съедет на случайную позицию.
    if data.get("is_active") is False and position.is_primary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нельзя отключить основную позицию — сначала назначьте основной другую",
        )
    apply_position_fields(position, data)
    db.flush()
    log_action(db, actor, "employee_position", position.id, "update",
               before=before, after=_position_dict(position))
    db.commit()
    db.refresh(position)
    return position


@router.post(
    "/{emp_id}/positions/{position_id}/make-primary",
    response_model=list[EmployeePositionRead],
)
def make_position_primary(
    emp_id: int,
    position_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Переназначить основную позицию. Основная ровно одна: с прежней признак
    снимается. От неё зависят отпускные/больничные и займ — см. CLAUDE.md."""
    emp = _employee_for_write(db, emp_id)
    position = _position_or_404(emp, position_id)
    before = emp.primary_position.id if emp.primary_position else None
    try:
        set_primary(emp, position)
    except PositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.flush()
    log_action(db, actor, "employee_position", position.id, "make_primary",
               before={"primary_position_id": before},
               after={"primary_position_id": position.id})
    db.commit()
    db.refresh(emp)
    return _sorted_positions(emp)


@router.delete("/{emp_id}/positions/{position_id}")
def remove_position(
    emp_id: int,
    position_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Убрать рабочее место. С часами/начислениями позиция деактивируется, а не
    удаляется — иначе история табеля осталась бы без рабочего места."""
    emp = _employee_for_write(db, emp_id)
    position = _position_or_404(emp, position_id)
    before = _position_dict(position)
    try:
        result = delete_position(db, emp, position)
    except PositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    log_action(db, actor, "employee_position", position_id, result, before=before)
    db.commit()
    return {"result": result}


# ── Распределение по компаниям по умолчанию (задача 3.11b п.1) ──────────────────

def _shares_response(
    db: Session, emp: Employee, position: EmployeePosition | None = None
) -> EmployeeSharesRead:
    """Распределение РАБОЧЕГО МЕСТА + дефолт его отдела (наследуется, если своего
    нет — task_distribution_v2 ч.3, каскад).

    Без явной позиции берётся основная — так же, как было до совместительства.
    """
    position = position or emp.primary_position
    position_id = position.id if position else None
    rows = [
        r
        for r in db.query(EmployeeCompanyShare)
        .filter(EmployeeCompanyShare.employee_id == emp.id)
        .all()
        # Строки без позиции заведены до неё и относятся к основной.
        if r.position_id == position_id
        or (r.position_id is None and position is not None and position.is_primary)
    ]
    percent_sum = sum((r.percent for r in rows), Decimal("0"))
    dept_id = position.department_id if position else None
    dept_map = load_department_shares(db, [dept_id] if dept_id else [])
    dept_shares = dept_map.get(dept_id, {}) if dept_id else {}
    return EmployeeSharesRead(
        employee_id=emp.id,
        position_id=position_id,
        shares=[CompanyShareInput(company_id=r.company_id, percent=r.percent) for r in rows],
        percent_sum=percent_sum,
        department_id=dept_id,
        department_name=position.department.name if position and position.department else None,
        department_shares=[
            CompanyShareInput(company_id=cid, percent=pct)
            for cid, pct in sorted(dept_shares.items())
        ],
        inherits_department=percent_sum <= 0 and bool(dept_shares),
    )


# ── Импорт сотрудников из Excel (task_employee_import) ─────────────────────────

@router.get("/import/template")
def download_import_template(
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Шаблон .xlsx для заполнения: колонки, строка-пример и лист «Справочники»."""
    content = generate_import_template(db)
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="shablon_sotrudnikov.xlsx"'},
    )


@router.post("/import", response_model=EmployeeImportResult)
def import_employees(
    file: UploadFile = File(...),
    confirm: bool = Query(default=False),
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Разобрать заполненный файл: превью со статусами строк, а по `confirm=true`
    — создать сотрудников по валидным строкам (ошибочные пропускаются).

    Один эндпоинт на оба шага: подтверждение приходит тем же файлом, поэтому
    на сервере нечему протухнуть — разбор и валидация повторяются перед записью,
    а не берутся на веру из превью.
    """
    content = file.file.read()
    try:
        result = parse_import_file(db, content)
    except ImportFileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if confirm:
        result = import_valid_rows(db, actor, result)
    return result


@router.get("/{emp_id}/company-shares", response_model=EmployeeSharesRead)
def get_company_shares(
    emp_id: int,
    position_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Проценты распределения РАБОЧЕГО МЕСТА; без `position_id` — основного."""
    # Распределение по юрлицам — деньги: табельщику 403 (task_timekeeper_role)
    if not can_see_finances(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if is_department_scoped(current_user) and not can_access_department(
        current_user, emp.department_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")

    return _shares_response(db, emp, emp.position_by_id(position_id))


def _current_shares(db: Session, emp_id: int, position_id: int | None) -> list:
    """Текущий набор процентов рабочего места — снимок «до» для журнала."""
    rows = db.query(EmployeeCompanyShare).filter(
        EmployeeCompanyShare.employee_id == emp_id,
        or_(
            EmployeeCompanyShare.position_id == position_id,
            EmployeeCompanyShare.position_id.is_(None),
        ),
    ).all()
    return [(r.company_id, r.percent) for r in rows]


@router.put("/{emp_id}/company-shares", response_model=EmployeeSharesRead)
def set_company_shares(
    emp_id: int,
    payload: EmployeeSharesUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Задать проценты распределения по умолчанию (в карточке). Сумма должна быть
    близка к 100% (допускаем 99–101 из-за округления)."""
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    try:
        positive = validate_shares(db, payload.shares)
    except SharesValidationError as e:
        code = status.HTTP_404_NOT_FOUND if e.not_found else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(e)) from e

    # Проценты задаются РАБОЧЕМУ МЕСТУ (task_positions ч.A); не указано какому —
    # основному, как было до совместительства.
    position = emp.position_by_id(payload.position_id)
    position_id = position.id if position else None

    # Журнал изменений (task_audit_log): набор переписывается целиком Core-DELETE
    # мимо ORM, поэтому события сессии его не видят — пишем ОДНОЙ записью
    # «было → стало». Снимок «до» надо снять ДО удаления строк.
    shares_before = format_share_rows(db, _current_shares(db, emp_id, position_id))

    db.query(EmployeeCompanyShare).filter(
        EmployeeCompanyShare.employee_id == emp_id,
        or_(
            EmployeeCompanyShare.position_id == position_id,
            EmployeeCompanyShare.position_id.is_(None),
        ),
    ).delete(synchronize_session=False)
    for s in positive:
        db.add(EmployeeCompanyShare(
            employee_id=emp_id, position_id=position_id,
            company_id=s.company_id, percent=s.percent,
        ))
    log_action(db, actor, "employee_company_shares", emp_id, "set",
               after={"position_id": position_id,
                      "shares": {s.company_id: str(s.percent) for s in positive}})
    record_change(
        db,
        entity_type=EMPLOYEE_SHARES_ENTITY,
        entity_id=position_id or emp.id,
        entity_label=(
            f"{emp.full_name} / {position.display_title}" if position else emp.full_name
        ),
        field="shares",
        old_value=shares_before,
        new_value=format_share_rows(db, [(s.company_id, s.percent) for s in positive]),
        employee_id=emp.id,
    )
    db.commit()
    return _shares_response(db, emp, position)
