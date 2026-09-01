from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.companies import Company
from app.models.company_shares import DepartmentCompanyShare
from app.models.departments import Department
from app.models.employees import Employee
from app.schemas.department import (
    DepartmentCreate,
    DepartmentManagerRead,
    DepartmentManagersRead,
    DepartmentManagersUpdate,
    DepartmentMoveMonth,
    DepartmentMovePreview,
    DepartmentMoveRequest,
    DepartmentMoveResult,
    DepartmentRead,
    DepartmentSharesRead,
    DepartmentSharesUpdate,
    DepartmentUpdate,
)
from app.schemas.payroll_statement import CompanyShareInput
from app.services.company_shares import SharesValidationError, validate_shares
from app.services.department_move import (
    MoveError,
    build_preview,
    move_department,
)
from app.services.org_access import (
    hides_finances,
    is_department_scoped,
    managed_department_ids,
)

router = APIRouter()

_admin_only = require_role("admin")
_readers = require_role("admin", "accountant", "manager")


def _to_dict(obj: Department) -> dict:
    return {
        "id": obj.id,
        "name": obj.name,
        "code": obj.code,
        "head_company_id": obj.head_company_id,
        "night_shift_fund": str(obj.night_shift_fund),
        "uses_quantity_distribution": obj.uses_quantity_distribution,
        "quantity_metric_name": obj.quantity_metric_name,
        "quantity_part1_name": obj.quantity_part1_name,
        "quantity_part2_name": obj.quantity_part2_name,
        "is_active": obj.is_active,
    }


def _read(dept: Department, actor: Employee) -> DepartmentRead:
    """Карточка отдела для ответа: фонд ночных смен — деньги, поэтому
    табельщику он не отдаётся (task_timekeeper_role). Число смен и остаток
    лимита ему видны отдельно, в табеле."""
    data = DepartmentRead.model_validate(dept)
    if hides_finances(actor):
        data.night_shift_fund = None
    return data


def _check_night_fund(value: Decimal | None) -> None:
    """Фонд ночных смен — неотрицательная сумма. Ноль допустим и означает
    «ночных смен в отделе нет»: лимит тогда 0 и отметить нельзя ни одной."""
    if value is not None and value < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Фонд ночных смен не может быть отрицательным",
        )


def _check_head_company(db: Session, company_id: int | None) -> None:
    """Головная компания — только ярлык для дерева оргструктуры, но ссылаться
    она должна на существующее юрлицо."""
    if company_id is None:
        return
    if not db.get(Company, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


@router.get("", response_model=list[DepartmentRead])
def list_departments(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if is_department_scoped(current_user):
        # Менеджеру и табельщику — только его отделы: из этого списка строится
        # селектор отделов, и чужие в нём делать нечего (task_org_structure ч.2).
        managed = managed_department_ids(current_user)
        if not managed:
            return []
        rows = db.query(Department).filter(Department.id.in_(managed)).all()
    else:
        rows = db.query(Department).all()
    return [_read(d, current_user) for d in rows]


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    if db.query(Department).filter(Department.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    _check_head_company(db, payload.head_company_id)
    dept = Department(
        name=payload.name,
        code=payload.code,
        head_company_id=payload.head_company_id,
        is_active=True,
    )
    if payload.night_shift_fund is not None:
        _check_night_fund(payload.night_shift_fund)
        dept.night_shift_fund = payload.night_shift_fund
    if payload.uses_quantity_distribution is not None:
        dept.uses_quantity_distribution = payload.uses_quantity_distribution
    for field in ("quantity_metric_name", "quantity_part1_name", "quantity_part2_name"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(dept, field, value.strip() or None)
    db.add(dept)
    db.flush()
    log_action(db, actor, "department", dept.id, "create", after=_to_dict(dept))
    db.commit()
    db.refresh(dept)
    return _read(dept, actor)


@router.get("/{dept_id}", response_model=DepartmentRead)
def get_department(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return _read(dept, current_user)


@router.patch("/{dept_id}", response_model=DepartmentRead)
def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    before = _to_dict(dept)
    changes = payload.model_dump(exclude_unset=True)
    if "head_company_id" in changes:
        _check_head_company(db, changes["head_company_id"])
    if "night_shift_fund" in changes:
        _check_night_fund(changes["night_shift_fund"])
        # Фонд не обнуляется в NULL: колонка обязательная, а «нет фонда» —
        # это 0. Пришедший null трактуем как «не менять».
        if changes["night_shift_fund"] is None:
            changes.pop("night_shift_fund")
    if changes.get("uses_quantity_distribution") is None:
        # Флаг «по количественному показателю» — обязательная колонка: пришедший
        # null означает «не менять», как и у фонда.
        changes.pop("uses_quantity_distribution", None)
    # Подписи показателя, наоборот, обнуляются явно: пустая строка = «нет
    # подписи» (у показателя без разбивки частей нет вовсе).
    for field in ("quantity_metric_name", "quantity_part1_name", "quantity_part2_name"):
        if field in changes:
            changes[field] = (changes[field] or "").strip() or None
    for field, value in changes.items():
        setattr(dept, field, value)
    db.flush()
    log_action(db, actor, "department", dept.id, "update", before=before, after=_to_dict(dept))
    db.commit()
    db.refresh(dept)
    return _read(dept, actor)


@router.delete("/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    dept_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
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


# ── Менеджеры и табельщики отдела (task_org_structure ч.2) ────────────────────
#
# Связь many-to-many, управляется СО СТОРОНЫ ОТДЕЛА. Не путать с
# Employee.department_id: там сотрудник числится, здесь — руководит или ведёт
# табель. Табельщик (task_timekeeper_role) сидит в той же связи: по отделам он
# устроен как менеджер, различие только в доступе к финансам. Роль каждого видна
# в `DepartmentManagerRead.role`, разделять список на два бэку незачем.

def _managers_response(dept: Department) -> DepartmentManagersRead:
    return DepartmentManagersRead(
        department_id=dept.id,
        managers=[
            DepartmentManagerRead.model_validate(m)
            for m in sorted(dept.managers, key=lambda m: m.full_name)
        ],
    )


@router.get("/{dept_id}/managers", response_model=DepartmentManagersRead)
def get_department_managers(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(_readers),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return _managers_response(dept)


@router.put("/{dept_id}/managers", response_model=DepartmentManagersRead)
def set_department_managers(
    dept_id: int,
    payload: DepartmentManagersUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Задать полный список менеджеров и табельщиков отдела. Пустой список снимает
    всех — отдел останется без руководителя (видят только admin/accountant)."""
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")

    before = [m.id for m in dept.managers]
    wanted = sorted(set(payload.employee_ids))
    managers: list[Employee] = []
    for emp_id in wanted:
        emp = db.get(Employee, emp_id)
        if not emp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Employee {emp_id} not found"
            )
        if not is_department_scoped(emp):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"«{emp.full_name}» не руководитель и не табельщик — сначала "
                    "выдайте роль «Руководитель» или «Табельщик»"
                ),
            )
        managers.append(emp)

    dept.managers = managers
    db.flush()
    log_action(
        db, actor, "department_managers", dept.id, "set",
        before={"employee_ids": before},
        after={"employee_ids": wanted},
    )
    db.commit()
    db.refresh(dept)
    return _managers_response(dept)


# ── Дефолт распределения по юрлицам (task_distribution_v2 ч.3) ─────────────────

def _shares_response(db: Session, dept_id: int) -> DepartmentSharesRead:
    rows = (
        db.query(DepartmentCompanyShare)
        .filter(DepartmentCompanyShare.department_id == dept_id)
        .all()
    )
    return DepartmentSharesRead(
        department_id=dept_id,
        shares=[CompanyShareInput(company_id=r.company_id, percent=r.percent) for r in rows],
        percent_sum=sum((r.percent for r in rows), Decimal("0")),
    )


@router.get("/{dept_id}/company-shares", response_model=DepartmentSharesRead)
def get_department_shares(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(_readers),
):
    """Распределение по юрлицам по умолчанию для отдела. Наследуется сотрудниками
    отдела, у которых нет своего распределения."""
    if not db.get(Department, dept_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return _shares_response(db, dept_id)


@router.put("/{dept_id}/company-shares", response_model=DepartmentSharesRead)
def set_department_shares(
    dept_id: int,
    payload: DepartmentSharesUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Задать дефолт распределения отдела. Сумма ≈100% (99–101 из-за округления).
    Пустой набор убирает дефолт — сотрудники отдела уходят на авто по часам.
    Сотрудников с собственным распределением это не затрагивает (каскад)."""
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    try:
        positive = validate_shares(db, payload.shares)
    except SharesValidationError as e:
        code = status.HTTP_404_NOT_FOUND if e.not_found else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=str(e)) from e

    db.query(DepartmentCompanyShare).filter(
        DepartmentCompanyShare.department_id == dept_id
    ).delete(synchronize_session=False)
    for s in positive:
        db.add(DepartmentCompanyShare(
            department_id=dept_id, company_id=s.company_id, percent=s.percent,
        ))
    log_action(db, actor, "department_company_shares", dept_id, "set",
               after={s.company_id: str(s.percent) for s in positive})
    db.commit()
    return _shares_response(db, dept_id)


# ── Перенос отдела в другую компанию (task_move_department) ───────────────────

def _move_target(db: Session, dept_id: int, target_company_id: int):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    target = db.get(Company, target_company_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return dept, target


def _preview_response(preview) -> DepartmentMovePreview:
    return DepartmentMovePreview(
        department_id=preview.department_id,
        department_name=preview.department_name,
        source_company_id=preview.source_company_id,
        source_company_name=preview.source_company_name,
        target_company_id=preview.target_company_id,
        target_company_name=preview.target_company_name,
        employee_count=preview.employee_count,
        position_count=preview.position_count,
        untouched_position_count=preview.untouched_position_count,
        closed_months=[
            DepartmentMoveMonth(year=y, month=m) for y, m in preview.closed_months
        ],
        stale_share_position_count=preview.stale_share_position_count,
        department_shares_stale=preview.department_shares_stale,
        entries_to_reattribute=preview.entries_to_reattribute,
    )


@router.get("/{dept_id}/move-preview", response_model=DepartmentMovePreview)
def preview_department_move(
    dept_id: int,
    target_company_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Что будет затронуто переносом: сколько сотрудников и рабочих мест переедет,
    сколько подработок в других отделах останется на месте, какие закрытые месяцы
    будут зафиксированы. Ничего не меняет."""
    dept, target = _move_target(db, dept_id, target_company_id)
    return _preview_response(build_preview(db, dept, target))


@router.post("/{dept_id}/move", response_model=DepartmentMoveResult)
def do_department_move(
    dept_id: int,
    payload: DepartmentMoveRequest,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Перенести отдел в другую компанию: головная компания отдела + компания его
    рабочих мест, с текущего месяца вперёд.

    Закрытые месяцы перед сменой фиксируются месячным override-ом, поэтому их
    расклад по юрлицам остаётся ровно таким, каким его уже видела бухгалтерия.
    Всё одной транзакцией: при сбое не остаётся ни половины переноса, ни
    половины заморозки."""
    dept, target = _move_target(db, dept_id, payload.target_company_id)
    try:
        result = move_department(db, dept, target, actor)
    except MoveError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    db.commit()
    return DepartmentMoveResult(
        department_id=dept_id,
        target_company_id=target.id,
        positions_moved=result.positions_moved,
        employees_affected=result.employees_affected,
        closed_months_frozen=result.closed_months_frozen,
        override_rows_written=result.override_rows_written,
        entries_reattributed=result.entries_reattributed,
    )
