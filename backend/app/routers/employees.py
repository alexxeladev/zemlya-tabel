import datetime
import secrets
import string
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.core.security import hash_password, revoke_sessions
from app.services.login_guard import locked_until_by_employee, unlock_login
from app.database import get_db
from app.models.company_shares import EmployeeCompanyShare
from app.models.employees import Employee
from app.models.positions import (
    EMPLOYEE_COMPAT_FIELDS,
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
from app.services.employees import (
    build_employee,
    normalize_tab_number,
    tab_number_conflict,
)
from app.services.employment_period import (
    bounds_snapshot,
    clear_entries_outside,
    clearing_report,
)
from app.services.finance_masking import employee_for, employees_for, position_for
from app.services.guard_staff import (
    GuardOwnedError,
    GuardTransferInError,
    ensure_loan_change_allowed,
    ensure_position_create_allowed,
    ensure_position_edit_allowed,
    ensure_position_owned_outside_vahta,
    ensure_transfer_into_guard_allowed,
    pin_loan_before_primary_change,
    is_guard_position,
)
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


# ── Табельный номер: уникальность ─────────────────────────────────────────────
# `employees.tab_number` уникален в БД. Без проверки заранее занятый номер
# доходил до Postgres, IntegrityError не ловился и пользователь получал 500.


def _guard_owned(exc: GuardOwnedError) -> HTTPException:
    """Охранную позицию ведёт вахта (task_guard_ownership): общий справочник
    получает отказ на бэке, а не только спрятанную кнопку."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _ensure_transfer_into_guard(db: Session, position, new_department_id) -> None:
    """Перевод обычного рабочего места в охрану при наличии на нём часов, премий
    или займа отклоняется: для расчёта вахты они стали бы невидимыми."""
    try:
        ensure_transfer_into_guard_allowed(db, position, new_department_id)
    except GuardTransferInError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


def _ensure_tab_number_free(
    db: Session, tab_number: str | None, exclude_id: int | None = None,
) -> None:
    """409, если табельный номер уже занят другим сотрудником (включая уволенных)."""
    conflict = tab_number_conflict(db, tab_number, exclude_id)
    if conflict is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)


# ── Смена дат периода работы: очистка часов за новой границей ─────────────────
# Границы сдвигают три места — правка карточки (дата приёма), увольнение и
# правка дат рабочего места. Очистка у них одна на всех: посчитать, что уедет за
# границу, показать числа и удалить только после подтверждения.


def _apply_employment_change(
    db: Session,
    actor: Employee,
    emp: Employee,
    confirm: bool,
    before_bounds: dict | None = None,
) -> int:
    """Убрать часы, оказавшиеся вне новых границ. Возвращает число ячеек.

    Вызывать ПОСЛЕ присвоения новых дат и до коммита: отчёт читает границы из
    ORM-объектов. Без `confirm` ничего не удаляет, а откатывает правку и отдаёт
    409 с числами — «сколько дней и на какую сумму». Очистка необратима, поэтому
    подтверждение обязательно (task_employment_period).

    Часы в периодах, закрытых для правки, не трогаются ни при каком
    подтверждении — о них сообщается отдельно.
    """
    # Границы не поехали — часы трогать нечем, и читать их незачем.
    if before_bounds is not None and before_bounds == bounds_snapshot(emp):
        return 0
    report = clearing_report(db, emp)
    if not report.has_changes:
        return 0
    if not confirm:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "employment_period_clearing_required",
                "message": (
                    "Изменение дат удалит часы за пределами периода работы. "
                    "Подтвердите сохранение."
                ),
                **report.as_dict(),
            },
        )
    return clear_entries_outside(db, actor, emp, report)


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
    # Сброс админом — тоже место, где задаётся пароль: 12 символов латиницы и
    # цифр политике (`core.security.password_policy_error`) удовлетворяют
    # всегда, это держит тест (task_stage2_access п.2.3).
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
        # Своя карточка: оклад виден, бюджет отдела (фонд ночных) — нет.
        return employees_for(current_user, [EmployeeRead.model_validate(e) for e in emp])

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
    # Табельщику — без окладов, сотруднику — без денег отдела (finance_masking).
    return _with_login_locks(
        db, current_user, rows,
        employees_for(current_user, [EmployeeRead.model_validate(e) for e in rows]),
    )


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

    return _with_login_locks(
        db, current_user, [emp], [employee_for(current_user, EmployeeRead.model_validate(emp))],
    )[0]


def _with_login_locks(
    db: Session, actor: Employee, rows: list[Employee], reads: list[EmployeeRead],
) -> list[EmployeeRead]:
    """Пометка «вход заблокирован до …» — только админу (снять может только он),
    одним запросом на весь список."""
    if actor.role != "admin":
        return reads
    locks = locked_until_by_employee(db, rows)
    for read in reads:
        until = locks.get(read.id)
        # Внутри naive UTC; наружу — с поясом, иначе браузер прочтёт как местное.
        read.login_locked_until = until.replace(tzinfo=datetime.timezone.utc) if until else None
    return reads


# ── Create ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    payload.tab_number = normalize_tab_number(payload.tab_number)
    _ensure_tab_number_free(db, payload.tab_number)
    # Основная позиция в охранном подразделении — это найм охранника, он
    # оформляется в вахте.
    try:
        ensure_position_create_allowed(db, payload.department_id)
    except GuardOwnedError as exc:
        raise _guard_owned(exc) from exc
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
    confirm: bool = Query(
        False,
        description="Подтвердить удаление часов, выпавших из периода работы",
    ),
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    data = payload.model_dump(exclude_unset=True)
    if "tab_number" in data:
        data["tab_number"] = normalize_tab_number(data["tab_number"])
        _ensure_tab_number_free(db, data["tab_number"], exclude_id=emp.id)

    # Плоские поля карточки пишут ОСНОВНУЮ позицию; если она охранная — её ведёт
    # вахта. Поля человека (ФИО, таб. №, даты, активность) правятся здесь.
    try:
        ensure_position_edit_allowed(db, emp.primary_position, {
            EMPLOYEE_COMPAT_FIELDS[name]: value
            for name, value in data.items()
            if name in EMPLOYEE_COMPAT_FIELDS
        })
        # Заём удерживается с рабочего места; на охранном его не ведут (аудит 2-Г).
        ensure_loan_change_allowed(db, emp, data)
    except GuardOwnedError as exc:
        raise _guard_owned(exc) from exc
    # Плоское `department_id` карточки переводит ОСНОВНУЮ позицию.
    if "department_id" in data:
        _ensure_transfer_into_guard(db, emp.primary_position, data["department_id"])

    before_bounds = bounds_snapshot(emp)
    before = _to_dict(emp)
    for field, value in data.items():
        setattr(emp, field, value)
    # Смена типа оплаты гасит поля чужих типов: у окладника не должно остаться
    # ставки за смену или за час, у посменного — оклада (иначе расчёт молча
    # возьмёт не ту базу).
    # Охранную основную позицию не трогаем вовсе: её ведёт вахта, и сохранение
    # ФИО в карточке не должно молча гасить её базу (task_guard_ownership).
    if not is_guard_position(db, emp.primary_position):
        for pay_type, base_field in _PAY_TYPE_BASE_FIELD.items():
            if emp.pay_type != pay_type:
                setattr(emp, base_field, None)
    db.flush()
    # Сдвинулась дата приёма/увольнения — часы за новой границей уходят
    # (task_employment_period), но только с подтверждения.
    _apply_employment_change(db, actor, emp, confirm, before_bounds)
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
    confirm: bool = Query(
        False,
        description="Подтвердить удаление часов после даты увольнения",
    ),
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

    before_bounds = bounds_snapshot(emp)
    before = _to_dict(emp)
    emp.is_active = False
    emp.dismissal_date = payload.dismissal_date
    # Уволенный не входит и так (is_active), но токен, выданный до увольнения,
    # ожил бы после возврата в пределах своих 8 часов (task_stage2_access п.2.4).
    revoke_sessions(emp)
    db.flush()
    # Уволен пятнадцатого — часы после пятнадцатого очищаются с подтверждения.
    _apply_employment_change(db, actor, emp, confirm, before_bounds)
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
    revoke_sessions(emp)
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
    # Сброс — это и есть «отозвать доступ»: сессии со старым паролем гаснут.
    revoke_sessions(emp)
    # С новым паролем человек должен войти сразу, а не ждать конца блокировки,
    # набранной старым (task_stage2_access п.2.6).
    unlock_login(emp)
    db.flush()
    log_action(db, actor, "employee", emp.id, "reset_password")
    db.commit()
    return {"temp_password": temp_password}


@router.post("/{emp_id}/unlock-login", response_model=EmployeeRead)
def unlock_employee_login(
    emp_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Снять блокировку входа после неудачных попыток (task_stage2_access п.2.6).
    Журнал попыток не трогается — неудачи до этого момента просто перестают
    считаться в порог."""
    emp = db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if emp.email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Employee has no system access"
        )
    before = locked_until_by_employee(db, [emp]).get(emp.id)
    unlock_login(emp)
    db.flush()
    log_action(db, actor, "employee", emp.id, "login_unlocked",
               before={"login_locked_until": before.isoformat() if before else None})
    db.commit()
    db.refresh(emp)
    return _with_login_locks(db, actor, [emp], [EmployeeRead.model_validate(emp)])[0]


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
    revoke_sessions(emp)
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
    # Табельщику — без ставок, сотруднику — без денег отдела (finance_masking).
    return [
        position_for(current_user, EmployeePositionRead.model_validate(p))
        for p in positions
    ]


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
    try:
        ensure_position_create_allowed(db, payload.department_id)
    except GuardOwnedError as exc:
        raise _guard_owned(exc) from exc
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
    confirm: bool = Query(
        False,
        description="Подтвердить удаление часов, выпавших из периода работы",
    ),
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    emp = _employee_for_write(db, emp_id)
    position = _position_or_404(emp, position_id)
    before_bounds = bounds_snapshot(emp)
    before = _position_dict(position)
    data = payload.model_dump(exclude_unset=True)
    # Охранную позицию правят в вахте. Обычную можно перевести в охрану отсюда —
    # после этого её ведёт вахта (перевод делает тот, кто владеет позицией ДО).
    try:
        ensure_position_edit_allowed(db, position, data)
    except GuardOwnedError as exc:
        raise _guard_owned(exc) from exc
    if "department_id" in data:
        _ensure_transfer_into_guard(db, position, data["department_id"])
    # Деактивировать основную нельзя — иначе сотрудник останется без рабочего
    # места, а расчёт молча съедет на случайную позицию.
    if data.get("is_active") is False and position.is_primary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нельзя отключить основную позицию — сначала назначьте основной другую",
        )
    apply_position_fields(position, data)
    db.flush()
    # Период работы на этой должности сдвинулся — часы за границей уходят
    # (task_employment_period), только с подтверждения.
    _apply_employment_change(db, actor, emp, confirm, before_bounds)
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
    # Заём без привязки живёт на основной позиции: если ею становится охранная,
    # он закрепляется за прежним местом, иначе удержание молча прекратилось бы.
    pinned_loan = pin_loan_before_primary_change(db, emp, position)
    try:
        set_primary(emp, position)
    except PositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.flush()
    log_action(db, actor, "employee_position", position.id, "make_primary",
               before={"primary_position_id": before},
               after={"primary_position_id": position.id,
                      **({"loan_position_id": pinned_loan} if pinned_loan else {})})
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
    try:
        ensure_position_owned_outside_vahta(db, position)
    except GuardOwnedError as exc:
        raise _guard_owned(exc) from exc
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
    # Распределение охранника берётся от места работы в вахте, не из карточки.
    try:
        ensure_position_owned_outside_vahta(db, position)
    except GuardOwnedError as exc:
        raise _guard_owned(exc) from exc

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
