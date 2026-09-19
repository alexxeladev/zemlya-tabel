from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.companies import Company
from app.models.company_shares import CompanyShareOverride
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.loan_deductions import LoanDeduction
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.absence import AbsenceInput, AbsenceRead
from app.schemas.night_shift import NightFundRead, NightShiftInput, NightShiftRead
from app.schemas.payout import (
    AdjustmentCreate,
    AdjustmentRead,
    LoanOverrideInput,
)
from app.schemas.payroll import (
    PayrollSummaryRead,
)
from app.schemas.payroll_statement import (
    DistributionOverrideInput,
    PayrollStatementRead,
)
from app.schemas.quantity import (
    DepartmentQuantitiesRead,
    DepartmentQuantitiesUpdate,
)
from app.schemas.timesheet import (
    AutofillPreview,
    AutofillRequest,
    RowCheckInput,
    RowCheckRead,
    TimesheetBatchInput,
    TimesheetBatchResponse,
    TimesheetCellCompanyChange,
    TimesheetCellInput,
    TimesheetEntryRead,
    TimesheetMonthResponse,
)
from app.schemas.timesheet_period import (
    AuditLogRead,
    StatusChangeReason,
    TasksResponse,
    TimesheetPeriodRead,
)
from app.services.absences import (
    absence_code,
    get_month_absences,
    over_limit_sick_dates,
    schedules_by_employee,
    set_absence,
)
from app.services.company_order import (
    company_display_name,
    company_order_by,
    order_index,
)
from app.services.employment_period import OutsideEmploymentPeriod
from app.services.guard_staff import (
    GuardAccrualError,
    ensure_no_guard_accrual,
    loan_position,
)
from app.services.finance_masking import (
    employees_for,
    mask_payroll_summary,
    positions_by_employee_for,
)
from app.services.night_shifts import (
    NightLimitExceeded,
    get_month_night_shifts,
    load_night_context,
    set_night_shift,
)
from app.services.org_access import (
    can_access_department,
    can_see_finances,
    hides_finances,
    is_department_scoped,
    managed_department_ids,
)
from app.services.payroll_statement import (
    build_payroll_statement,
    build_payroll_summary,
    build_quantity_distribution,
)
from app.services.positions import department_ids_of, visible_positions
from app.services.quantity_distribution import (
    department_quantities_state,
    set_department_quantities,
)
from app.services.row_checks import checked_position_ids, set_row_check
from app.services.timesheet import (
    apply_autofill,
    build_autofill_preview,
    compute_extra_companies_by_employee,
    get_month_entries,
    CellConflict,
    CellNotFound,
    move_cell_company,
    upsert_cell,
    upsert_cells_batch,
    visible_employees_for_actor,
)
from app.services.timesheet_periods import (
    PeriodLockedException,
    close_period,
    get_or_create_periods,
    list_review_tasks,
    make_period_read,
    reopen_period,
    return_to_draft,
    submit_for_review,
)

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_dept_access(actor: Employee, department_id: int | None) -> None:
    """Менеджер и табельщик получают данные только своих отделов
    (task_org_structure ч.2, task_timekeeper_role).
    `department_id is None` — фильтр не задан, отдавать все его отделы."""
    if not is_department_scoped(actor) or department_id is None:
        return
    if not can_access_department(actor, department_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")


def _check_year_month(year: int, month: int) -> None:
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )


def _quantity_department_scope(
    actor: Employee, department_id: int | None
) -> list[int] | None:
    """Отделы, чей количественный показатель отдавать: выбранный, все свои у
    менеджера, None («все с флагом») у admin/accountant. Отделы БЕЗ флага
    отсеет сам сервис.
    """
    if department_id is not None:
        return [department_id]
    if is_department_scoped(actor):
        return sorted(managed_department_ids(actor))
    return None


def _check_cell_access(
    actor: Employee,
    target_employee_id: int,
    db: Session,
    position_id: int | None = None,
) -> Employee:
    """Доступ к данным сотрудника; отдел берётся у ПОЗИЦИИ (task_positions).

    Указана позиция — проверяем её отдел: подработку в чужом отделе менеджер
    трогать не должен. Позиция не указана — достаточно доступа к любому её
    рабочему месту, иначе менеджер не смог бы править сотрудника, который
    числится основной позицией в другом отделе.

    Табельщик по отделам устроен так же, как менеджер (task_timekeeper_role):
    часы и отсутствия своих отделов он ведёт, а до финансовых эндпойнтов, которые
    тоже вызывают этот хелпер, его не пускает `_require_finance_role` выше.
    """
    target = db.get(Employee, target_employee_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if actor.role in ("admin", "accountant"):
        return target
    if is_department_scoped(actor):
        if position_id is not None:
            position = target.position_by_id(position_id)
            allowed = can_access_department(
                actor, position.department_id if position else None
            )
        else:
            allowed = any(
                can_access_department(actor, pos.department_id)
                for pos in (target.active_positions or target.positions)
            )
        if not allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return target
    if actor.id != target_employee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return target


def _check_position_access(
    actor: Employee, db: Session, employee_id: int, position_id: int | None,
    loan: bool = False,
) -> tuple[Employee, EmployeePosition | None]:
    """Доступ к КОНКРЕТНОМУ рабочему месту, к которому относится запись
    (task_stage2_access п.2.8): премия/KPI/аванс — к своей позиции
    (`position_id IS NULL` — основная), заём — к месту удержания
    (`loan_position`). Проверяется отдел ЭТОЙ позиции.

    `_check_cell_access` без позиции пускает при доступе к ЛЮБОМУ месту
    сотрудника — так менеджер отдела, где у человека совместительство, удалял
    премию основной позиции и правил заём из чужого отдела.
    """
    # Сначала позиция, потом ОДНА проверка по её отделу: предварительная
    # проверка «по любому месту» требовала АКТИВНОЕ место в своём отделе и
    # отказывала менеджеру на снятой с учёта подработке его же отдела.
    target = db.get(Employee, employee_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    position = loan_position(target) if loan else target.position_by_id(position_id)
    _check_cell_access(actor, employee_id, db, position.id if position is not None else None)
    return target, position


def _check_company_exists(db: Session, company_id: int) -> None:
    if not db.get(Company, company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


def _require_finance_role(actor: Employee) -> None:
    """Деньги (расчёт, ведомость, премии/KPI/удержания/займ, распределение) видят и
    правят только admin/accountant/manager — см. `can_see_finances`. Табельщик
    получает 403: он ведёт время, а не зарплату (task_timekeeper_role)."""
    if not can_see_finances(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")


def _require_timesheet_role(actor: Employee) -> None:
    """Работа с табелем отдела: часы, автозаполнение, выгрузка Т-13 (только часы,
    без денег). Здесь табельщик есть, employee — нет."""
    if actor.role not in ("admin", "accountant", "manager", "timekeeper"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")


def _funding_company_names(db: Session, rows) -> dict[int, str]:
    """{company_id: отображаемое имя} для источников финансирования начислений."""
    ids = {r.funding_company_id for r in rows if r.funding_company_id}
    if not ids:
        return {}
    companies = db.query(Company).filter(Company.id.in_(ids)).all()
    return {c.id: company_display_name(c) for c in companies}


def _load_adjustments(
    db: Session, employees: list[Employee], year: int, month: int, actor: Employee,
) -> list[AdjustmentRead]:
    emp_ids = [e.id for e in employees]
    if not emp_ids:
        return []
    rows = (
        db.query(EmployeeAdjustment)
        .filter(
            EmployeeAdjustment.employee_id.in_(emp_ids),
            EmployeeAdjustment.year == year,
            EmployeeAdjustment.month == month,
        )
        .order_by(EmployeeAdjustment.created_at)
        .all()
    )
    # Менеджеру — только начисления рабочих мест ЕГО отделов (task_stage2_access
    # п.2.8): премия подработки в чужом отделе — не его данные. Позиция NULL —
    # основная. Admin/accountant видят всё, включая снятые с учёта места.
    if is_department_scoped(actor):
        by_id = {e.id: e for e in employees}

        def _visible(r: EmployeeAdjustment) -> bool:
            position = by_id[r.employee_id].position_by_id(r.position_id)
            return can_access_department(actor, position.department_id if position else None)

        rows = [r for r in rows if _visible(r)]
    # Имя юрлица-источника (task_funding_source) — чтобы список премий
    # подписывался без отдельного запроса справочника на фронте.
    funding_names = _funding_company_names(db, rows)
    return [
        AdjustmentRead(
            id=r.id,
            employee_id=r.employee_id,
            position_id=r.position_id,
            year=r.year,
            month=r.month,
            kind=r.kind,
            amount=r.amount,
            reason=r.reason,
            funding_company_id=r.funding_company_id,
            funding_company_name=funding_names.get(r.funding_company_id),
            created_by_id=r.created_by_id,
            created_at=str(r.created_at) if r.created_at else None,
        )
        for r in rows
    ]


def _get_period_or_404(db: Session, period_id: int) -> TimesheetPeriod:
    period = db.get(TimesheetPeriod, period_id)
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Period not found")
    return period


def _build_periods_for_response(
    db: Session,
    employees: list[Employee],
    year: int,
    month: int,
    actor: Employee,
) -> list[TimesheetPeriodRead]:
    """Create/fetch periods for all unique department_ids visible in this response.

    Отдел — у ПОЗИЦИИ (task_positions ч.A), поэтому у совместителя, работающего
    в двух отделах, нужны периоды обоих: иначе его часы во втором отделе не за
    что было бы закрыть.

    Коммитим ТОЛЬКО если период действительно создан (обычно это первый заход в
    месяц). Безусловный `commit` на GET обесценивал уже загруженные объекты, и
    сотрудники с часами перезагружались по одной строке. Вызывать эту функцию
    надо ДО загрузки часов — тогда даже первый заход ничего не обесценивает.
    """
    dept_ids: set[int | None] = {
        dept_id for e in employees for dept_id in department_ids_of(e)
    }
    by_dept, created = get_or_create_periods(db, dept_ids, year, month)
    if created:
        db.commit()
    return [make_period_read(by_dept[dept_id], actor) for dept_id in dept_ids]


# ── Payroll helper ────────────────────────────────────────────────────────────
# Единый расчёт ЗП (табель + ведомость) живёт в app.services.payroll_statement —
# здесь только тонкая обёртка, чтобы не дублировать формулы.

def _build_payroll_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
    actor: Employee,
    department_id: int | None = None,
) -> PayrollSummaryRead:
    return build_payroll_summary(
        db, employees, entries, year, month, actor, department_id
    )


# ── Tasks inbox (Bug 3) ───────────────────────────────────────────────────────

@router.get("/tasks", response_model=TasksResponse)
def get_review_tasks(
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Inbox для accountant/admin: периоды на проверке + недавно закрытые."""
    if actor.role not in ("admin", "accountant"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    pending, closed = list_review_tasks(db)
    return TasksResponse(pending_review=pending, recently_closed=closed)


# ── GET month ─────────────────────────────────────────────────────────────────

@router.get("/{year}/{month}/payroll", response_model=PayrollSummaryRead)
def get_payroll(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    # Manager видит только свои отделы — запрос финансов чужого отдела запрещён
    _require_dept_access(actor, department_id)
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    return _build_payroll_summary(db, employees, entries, year, month, actor, department_id)


# ── Payroll statement: сводная ведомость + распределение по % (задача 3.11b) ───

@router.get("/{year}/{month}/statement", response_model=PayrollStatementRead)
def get_payroll_statement(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    _require_dept_access(actor, department_id)
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    return build_payroll_statement(
        db, employees, entries, year, month, actor, department_id
    )


@router.put("/distribution", status_code=status.HTTP_200_OK)
def set_distribution_override(
    payload: DistributionOverrideInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Переопределить распределение по компаниям на конкретный месяц (правка в
    ведомости). Заменяет весь набор процентов сотрудника за этот период."""
    _require_finance_role(actor)
    target, _ = _check_position_access(actor, db, payload.employee_id, payload.position_id)
    if not (1 <= payload.month <= 12) or not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month")
    for s in payload.shares:
        _check_company_exists(db, s.company_id)
        if s.percent < 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Процент не может быть отрицательным")

    # Полностью заменяем набор РАБОЧЕГО МЕСТА за период (task_positions ч.A):
    # у совместителя каждое разносится по юрлицам отдельно.
    position = target.position_by_id(payload.position_id)
    position_id = position.id if position else None
    db.query(CompanyShareOverride).filter(
        CompanyShareOverride.employee_id == payload.employee_id,
        CompanyShareOverride.year == payload.year,
        CompanyShareOverride.month == payload.month,
        or_(
            CompanyShareOverride.position_id == position_id,
            # Строки без позиции заведены до неё и относятся к ОСНОВНОЙ — снимать
            # их можно только правкой основной (то же правило, что в DELETE).
            CompanyShareOverride.position_id.is_(None)
            if position is not None and position.is_primary
            else False,
        ),
    ).delete(synchronize_session=False)
    for s in payload.shares:
        if s.percent <= 0:
            continue
        db.add(CompanyShareOverride(
            employee_id=payload.employee_id,
            position_id=position_id,
            company_id=s.company_id,
            year=payload.year,
            month=payload.month,
            percent=s.percent,
            created_by_id=actor.id,
        ))
    log_action(
        db, actor, "company_share_override", payload.employee_id, "set",
        after={"position_id": position_id, "year": payload.year, "month": payload.month,
               "shares": {s.company_id: str(s.percent) for s in payload.shares}},
    )
    db.commit()
    return {"employee_id": payload.employee_id, "year": payload.year, "month": payload.month}


@router.delete(
    "/distribution/{employee_id}/{year}/{month}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_distribution_override(
    employee_id: int,
    year: int,
    month: int,
    position_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Убрать переопределение — вернуть проценты по умолчанию из карточки.

    Правка задаётся РАБОЧЕМУ МЕСТУ, поэтому и снимается по нему: без
    `position_id` у совместителя сбросились бы обе позиции разом.
    """
    _require_finance_role(actor)
    target, _ = _check_position_access(actor, db, employee_id, position_id)
    q = db.query(CompanyShareOverride).filter(
        CompanyShareOverride.employee_id == employee_id,
        CompanyShareOverride.year == year,
        CompanyShareOverride.month == month,
    )
    if position_id is not None:
        position = target.position_by_id(position_id)
        pid = position.id if position else position_id
        q = q.filter(
            or_(
                CompanyShareOverride.position_id == pid,
                # Строки без позиции заведены до неё и относятся к основной.
                CompanyShareOverride.position_id.is_(None)
                if position is not None and position.is_primary
                else False,
            )
        )
    else:
        # Без position_id снимаются правки ВСЕХ рабочих мест — значит, нужен
        # доступ к каждому из них (task_stage2_access п.2.8): иначе менеджер
        # подработки сбрасывал бы распределение основной позиции в чужом отделе.
        for (row_position_id,) in q.with_entities(CompanyShareOverride.position_id).distinct():
            _check_position_access(actor, db, employee_id, row_position_id)
    deleted = q.delete(synchronize_session=False)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Переопределение не найдено")
    log_action(db, actor, "company_share_override", employee_id, "delete",
               before={"year": year, "month": month, "position_id": position_id})
    db.commit()


# ── Количественный показатель отдела (заявки у HR, АРМ у ИТ) ──────────────────
#
# Отдел с флагом `uses_quantity_distribution` делит зарплату своих сотрудников
# по набранному за месяц количеству, а не по каскаду. Это управленческая
# настройка распределения, поэтому права те же, что у процентов в ведомости:
# финансовые роли, менеджер — только свои отделы. Статус периода не при чём:
# это не факт времени, а правило разнесения затрат.

@router.get("/{year}/{month}/quantities", response_model=list[DepartmentQuantitiesRead])
def get_quantities(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Количественный показатель за месяц и вычисленные из него проценты — по
    отделам с флагом. Отделов без флага в выдаче нет."""
    _require_finance_role(actor)
    _check_year_month(year, month)
    _require_dept_access(actor, department_id)
    return department_quantities_state(
        db, _quantity_department_scope(actor, department_id), year, month
    )


@router.put("/quantities", response_model=DepartmentQuantitiesRead)
def set_quantities(
    payload: DepartmentQuantitiesUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Задать количества отдела за месяц целиком (что прислали, то и будет).

    Только для отдела с флагом: без него показатель некуда применить, и молча
    сохранённый набор был бы данными-призраком.
    """
    _require_finance_role(actor)
    _check_year_month(payload.year, payload.month)
    _require_dept_access(actor, payload.department_id)
    dept = db.get(Department, payload.department_id)
    if not dept:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found"
        )
    if not dept.uses_quantity_distribution:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Отдел «{dept.name}» не распределяется по количественному "
                "показателю — включите признак в карточке отдела"
            ),
        )
    seen: set[int] = set()
    for item in payload.items:
        _check_company_exists(db, item.company_id)
        if item.company_id in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Компания указана дважды",
            )
        seen.add(item.company_id)

    set_department_quantities(
        db, dept.id, payload.year, payload.month, payload.items, actor.id
    )
    log_action(
        db, actor, "department_quantities", dept.id, "set",
        after={"year": payload.year, "month": payload.month,
               "items": {
                   i.company_id: {"part1": i.part1, "part2": i.part2}
                   for i in payload.items
               }},
    )
    db.commit()
    state = department_quantities_state(db, [dept.id], payload.year, payload.month)
    return state[0]


@router.get("/{year}/{month}/statement/export/excel")
def export_statement_excel(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Выгрузка сводной ведомости «Расчёт ЗП» в Excel (задача 3.11b п.3)."""
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    _require_dept_access(actor, department_id)
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    statement = build_payroll_statement(
        db, employees, entries, year, month, actor, department_id
    )

    from app.services.payroll_statement_export import generate_statement_excel
    excel_bytes = generate_statement_excel(statement)

    log_action(
        db, actor, "payroll_statement", None, "statement_exported_excel",
        after={"year": year, "month": month, "department_id": department_id},
    )
    db.commit()

    filename = f"vedomost_{year}_{month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{year}/{month}", response_model=TimesheetMonthResponse)
def get_month(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    include_payroll: bool = Query(default=False),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    # Отдел, которым менеджер не руководит, — явный 403, а не молча пустая выдача
    _require_dept_access(actor, department_id)

    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    companies = (
        db.query(Company).filter(Company.is_active == True)  # noqa: E712
        .order_by(*company_order_by()).all()
    )
    # Периоды — ДО загрузки часов: они могут коммитить (lazy-создание при первом
    # заходе в месяц), а коммит обесценил бы уже загруженные ячейки, и каждая
    # перезагружалась бы отдельным запросом.
    periods = _build_periods_for_response(db, employees, year, month, actor)
    entries = get_month_entries(db, employees, year, month)
    extra_companies = compute_extra_companies_by_employee(
        employees, entries, order_index(c.id for c in companies)
    )
    # Больничные сверх годового лимита помечаем сразу в выдаче: лимит считается
    # хронологически по всему году, ячейка сама этого знать не может.
    cal_for_year = db.query(ProductionCalendar).filter_by(year=year).first()
    over_limit = over_limit_sick_dates(
        db, [e.id for e in employees], year, cal_for_year.data if cal_for_year else None,
        schedules_by_employee(employees),
    )
    absences = [
        AbsenceRead(
            employee_id=a.employee_id,
            work_date=a.work_date,
            kind=a.kind,
            code=absence_code(a.kind),
            over_limit=a.work_date in over_limit.get(a.employee_id, ()),
        )
        for a in get_month_absences(db, employees, year, month)
    ]

    # Рабочие места, по которым в этом табеле вводят часы. Совместитель даёт
    # строку на позицию; в табеле отдела видна только позиция ЭТОГО отдела
    # (task_positions ч.B), у менеджера — только его отделы.
    positions_by_employee = {
        emp.id: visible_positions(emp, actor, department_id) for emp in employees
    }

    # Ночные смены: отметки + состояние фонда отделов, попавших в выдачу.
    # Фонд считается по ВСЕМУ отделу (не только по видимым сотрудникам) —
    # иначе остаток лимита на экране расходился бы с проверкой при сохранении.
    night = load_night_context(
        db,
        employees,
        year,
        month,
        department_ids=sorted({
            pos.department_id
            for positions in positions_by_employee.values()
            for pos in positions
            if pos.department_id is not None
        }),
    )
    night_shifts = [
        NightShiftRead(
            employee_id=s.employee_id,
            position_id=s.position_id,
            work_date=s.work_date,
        )
        for s in get_month_night_shifts(db, employees, year, month)
    ]
    hide_money = hides_finances(actor) or not can_see_finances(actor)
    night_funds = [
        NightFundRead(
            department_id=dept_id,
            department_name=night.name_by_department.get(dept_id),
            # Фонд и ставка — деньги: у табельщика и сотрудника их нет,
            # остаётся счётчик смен (сколько отмечено и сколько ещё можно).
            fund=None if hide_money else night.fund_by_department.get(dept_id),
            rate=None if hide_money else night.rate_by_department.get(dept_id),
            limit_shifts=night.limit_by_department.get(dept_id, 0),
            used_shifts=night.used_by_department.get(dept_id, 0),
            remaining_shifts=night.remaining_of(dept_id),
        )
        for dept_id in sorted(night.limit_by_department)
    ]

    payroll = None
    adjustments: list[AdjustmentRead] = []
    # Количественный показатель: блок ввода в табеле отдела с флагом.
    # Распределение — это деньги, поэтому набор отдаётся только финансовым ролям;
    # отделы берутся из тех, что реально попали в выдачу.
    quantities = (
        department_quantities_state(
            db, _quantity_department_scope(actor, department_id), year, month
        )
        if can_see_finances(actor)
        else []
    )
    if can_see_finances(actor):
        adjustments = _load_adjustments(db, employees, year, month, actor)
        if include_payroll:
            payroll = _build_payroll_summary(
                db, employees, entries, year, month, actor, department_id
            )
    elif include_payroll and hides_finances(actor):
        # Табельщику расчёт нужен ради ЧАСОВ: норма, переработка, часы вне
        # графика и праздничные, дни отпуска/больничного, остаток лимита Б —
        # без них не видно, правильно ли заполнен табель. Деньги из этого
        # расчёта вычищаются ниже (mask_payroll_summary). Премии/KPI/удержания
        # (adjustments) ему не отдаются вообще — это чистые деньги.
        payroll = _build_payroll_summary(
            db, employees, entries, year, month, actor, department_id
        )
    # Суммы распределения по показателю — для блока «Распределение» в табеле.
    # Только когда расчёт вообще считался: делить нечего, пока нет начисленного.
    quantity_distribution = (
        build_quantity_distribution(
            db, payroll, positions_by_employee, year, month,
            order_index(c.id for c in companies),
        )
        if quantities and payroll is not None
        else []
    )
    # include_payroll от employee игнорируем молча — проверка принудительная на
    # бэке, а не «по просьбе» фронта.

    # Личные отметки «проверено» — одним запросом вместе с табелем и только
    # СВОИ: выборка сужена по актору в самом сервисе (task_pilot_ux ч.3).
    checked_positions = checked_position_ids(
        db,
        actor,
        year,
        month,
        [pos.id for positions in positions_by_employee.values() for pos in positions],
    )

    response = TimesheetMonthResponse(
        year=year,
        month=month,
        employees=employees,
        companies=companies,
        entries=entries,
        periods=periods,
        extra_companies_by_employee=extra_companies,
        positions_by_employee=positions_by_employee,
        absences=absences,
        night_shifts=night_shifts,
        night_funds=night_funds,
        quantities=quantities,
        quantity_distribution=quantity_distribution,
        payroll=payroll,
        adjustments=adjustments,
        checked_positions=checked_positions,
    )
    # Карточки и позиции — часть этого же ответа: табельщику они идут без денег
    # вовсе, сотруднику — без денег ОТДЕЛА (фонд ночных вложен в `department`).
    # Кому что — решает finance_masking. Скрывать это только в UI недостаточно.
    response.employees = employees_for(actor, response.employees)
    response.positions_by_employee = positions_by_employee_for(
        actor, response.positions_by_employee
    )
    if hides_finances(actor):
        if response.payroll is not None:
            response.payroll = mask_payroll_summary(response.payroll)
    return response


# ── Cell mutations ────────────────────────────────────────────────────────────

def _guard_accrual(exc: GuardAccrualError) -> HTTPException:
    """Начисления на охранную позицию ведёт вахта — тот же 403, что у владения
    штатом (`routers/employees._guard_owned`)."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _cell_conflict(exc: CellConflict) -> HTTPException:
    """Ячейку успел изменить другой редактор — 409 с текущим значением."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Ячейку уже изменил другой пользователь: {exc}. "
            "Часы на экране обновлены — проверьте и введите заново"
        ),
    )


@router.put("/cell", response_model=Optional[TimesheetEntryRead])
def save_cell(
    payload: TimesheetCellInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _check_position_access(actor, db, payload.employee_id, payload.position_id)
    _check_company_exists(db, payload.company_id)
    try:
        result = upsert_cell(
            db, actor,
            payload.employee_id, payload.work_date, payload.company_id, payload.hours,
            payload.position_id, payload.expected_version,
        )
    except CellConflict as exc:
        raise _cell_conflict(exc)
    except GuardAccrualError as exc:
        raise _guard_accrual(exc)
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    except OutsideEmploymentPeriod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"День вне периода работы: {exc}",
        )
    return result


@router.put("/cell/company", response_model=TimesheetEntryRead)
def change_cell_company(
    payload: TimesheetCellCompanyChange,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Перенести часы ячейки на другое юрлицо ОДНОЙ транзакцией.

    Два отдельных `PUT /cell` (обнулить старую, записать новую) теряли часы при
    сбое второго. Здесь либо перенос выполнен целиком, либо не изменилось ничего.
    """
    _check_position_access(actor, db, payload.employee_id, payload.position_id)
    if payload.old_company_id == payload.new_company_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Часы уже стоят на этом юрлице",
        )
    _check_company_exists(db, payload.new_company_id)
    try:
        return move_cell_company(
            db, actor,
            payload.employee_id, payload.work_date,
            payload.old_company_id, payload.new_company_id, payload.position_id,
            payload.expected_version,
        )
    except CellConflict as exc:
        raise _cell_conflict(exc)
    except GuardAccrualError as exc:
        raise _guard_accrual(exc)
    except CellNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="В этой ячейке нет часов — переносить нечего",
        )
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    except OutsideEmploymentPeriod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"День вне периода работы: {exc}",
        )


@router.post("/cells/batch", response_model=TimesheetBatchResponse)
def save_cells_batch(
    payload: TimesheetBatchInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    for cell in payload.entries:
        _check_position_access(actor, db, cell.employee_id, cell.position_id)
        _check_company_exists(db, cell.company_id)

    cells = [
        (c.employee_id, c.work_date, c.company_id, c.hours, c.position_id, c.expected_version)
        for c in payload.entries
    ]
    try:
        results = upsert_cells_batch(db, actor, cells)
    except CellConflict as exc:
        raise _cell_conflict(exc)
    except GuardAccrualError as exc:
        raise _guard_accrual(exc)
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    except OutsideEmploymentPeriod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"День вне периода работы: {exc}",
        )
    return TimesheetBatchResponse(entries=results)


# ── Absences: коды ОТ / ДО / Б / Н ────────────────────────────────────────────

@router.put("/absence", response_model=Optional[AbsenceRead])
def save_absence(
    payload: AbsenceInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """
    Поставить/снять код отсутствия на день (kind=null — снять).

    Права те же, что у ячейки часов; период должен быть в draft. Постановка
    кода удаляет часы этого дня (взаимоисключение, удаление в audit log).
    """
    _check_cell_access(actor, payload.employee_id, db)
    try:
        result = set_absence(
            db, actor, payload.employee_id, payload.work_date, payload.kind,
        )
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    except OutsideEmploymentPeriod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"День вне периода работы: {exc}",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if result is None:
        return None
    return AbsenceRead(
        employee_id=result.employee_id,
        work_date=result.work_date,
        kind=result.kind,
        code=absence_code(result.kind),
    )


# ── Личная отметка «строку проверил» (task_pilot_ux ч.3) ──────────────────────

@router.put("/row-check", response_model=RowCheckRead)
def save_row_check(
    payload: RowCheckInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """
    Поставить/снять ЛИЧНУЮ отметку «эту строку я проверил» (value=false — снять).

    Закладка, а не статус табеля: привязана к пользователю, чужие её не видят
    и снять не могут. Поэтому отметка не зависит от статуса периода — закрытый
    месяц перечитывают и сверяют так же, как открытый, — и ничего не
    пересчитывает: ответ минимальный, фронт обновляет строку оптимистично и
    месяц не перезапрашивает.

    Права — как на просмотр строки: доступ к ОТДЕЛУ рабочего места
    (`_check_cell_access` с position_id). Отметить чужой отдел нельзя, иначе
    по ответам эндпойнта можно было бы нащупать чужие позиции.
    """
    position = db.get(EmployeePosition, payload.position_id)
    if position is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position not found"
        )
    _check_cell_access(actor, position.employee_id, db, position.id)
    checked = set_row_check(
        db, actor, position.id, payload.year, payload.month, payload.value
    )
    return RowCheckRead(
        position_id=position.id,
        year=payload.year,
        month=payload.month,
        checked=checked,
    )


# ── Ночные смены (task_night_shifts_rework) ───────────────────────────────────

@router.put("/night-shift", response_model=Optional[NightShiftRead])
def save_night_shift(
    payload: NightShiftInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """
    Отметить/снять выход в ночную смену (value=false — снять).

    Права те же, что у ячейки часов: это ФАКТ выхода, а не деньги, — поэтому
    отмечает и табельщик. Период должен быть в draft.

    Дневные часы дня не трогаются: ночная смена — отдельная подработка и
    сосуществует с ними. Превышение фонда отдела блокируется на бэке (409):
    авторитетная проверка здесь, фронт лишь показывает остаток заранее.
    """
    _require_timesheet_role(actor)
    _check_position_access(actor, db, payload.employee_id, payload.position_id)
    try:
        result = set_night_shift(
            db, actor, payload.employee_id, payload.position_id,
            payload.work_date, payload.value,
        )
    except NightLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    except OutsideEmploymentPeriod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"День вне периода работы: {exc}",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if result is None:
        return None
    return NightShiftRead(
        employee_id=result.employee_id,
        position_id=result.position_id,
        work_date=result.work_date,
    )


# ── Period workflow ───────────────────────────────────────────────────────────

@router.post("/periods/{period_id}/submit", response_model=TimesheetPeriodRead)
def submit_period(
    period_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = submit_for_review(db, period, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


@router.post("/periods/{period_id}/return", response_model=TimesheetPeriodRead)
def return_period(
    period_id: int,
    payload: StatusChangeReason,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = return_to_draft(db, period, actor, payload.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


@router.post("/periods/{period_id}/close", response_model=TimesheetPeriodRead)
def close_period_endpoint(
    period_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = close_period(db, period, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


@router.post("/periods/{period_id}/reopen", response_model=TimesheetPeriodRead)
def reopen_period_endpoint(
    period_id: int,
    payload: StatusChangeReason,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    period = _get_period_or_404(db, period_id)
    try:
        period = reopen_period(db, period, actor, payload.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return make_period_read(period, actor)


# ── Autofill ─────────────────────────────────────────────────────────────────

@router.post("/autofill/preview", response_model=AutofillPreview)
def autofill_preview(
    payload: AutofillRequest,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_timesheet_role(actor)
    _require_dept_access(actor, payload.department_id)
    try:
        return build_autofill_preview(db, actor, payload.year, payload.month, payload.department_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/autofill/apply")
def autofill_apply(
    payload: AutofillRequest,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_timesheet_role(actor)
    _require_dept_access(actor, payload.department_id)
    try:
        preview = build_autofill_preview(db, actor, payload.year, payload.month, payload.department_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    try:
        count = apply_autofill(db, actor, preview)
    except PeriodLockedException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Период закрыт для редактирования, статус: {exc.status}",
        )
    except OutsideEmploymentPeriod as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"День вне периода работы: {exc}",
        )

    log_action(
        db, actor, "timesheet", None, "timesheet_autofilled",
        after={"entries_created": count, "employees_count": preview.employees_processed},
    )
    db.commit()
    return {"entries_created": count, "employees_count": preview.employees_processed}


# ── Period history ────────────────────────────────────────────────────────────

@router.get("/periods/{period_id}/history", response_model=list[AuditLogRead])
def get_period_history(
    period_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """История согласования периода (task_stage2_access п.2.1): кто и когда
    переводил статус, тексты причин возврата. Видят роли с доступом к ОТДЕЛУ
    периода — admin/accountant все, manager и табельщик свои (табельщику
    адресованы причины возврата). Сотрудник — 403 до поиска периода: иначе по
    404/403 перебирались бы существующие id."""
    _require_timesheet_role(actor)
    period = _get_period_or_404(db, period_id)
    if not can_access_department(actor, period.department_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_type == "timesheet_period",
            AuditLog.entity_id == period_id,
        )
        .order_by(AuditLog.created_at)
        .all()
    )
    result = []
    for log in logs:
        actor_emp = db.get(Employee, log.actor_id)
        result.append(
            AuditLogRead(
                id=log.id,
                actor_id=log.actor_id,
                actor_name=actor_emp.full_name if actor_emp else None,
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                action=log.action,
                before=log.before,
                after=log.after,
                reason=log.reason,
                created_at=str(log.created_at),
            )
        )
    return result


# ── Excel export ──────────────────────────────────────────────────────────────

@router.get("/{year}/{month}/export/excel")
def export_excel(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Экспорт табеля в Excel формата Т-13."""
    # Т-13 — только часы, рублей в файле нет, поэтому табельщику он доступен.
    _require_timesheet_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    _require_dept_access(actor, department_id)

    from app.services.timesheet_export import generate_t13_excel
    excel_bytes = generate_t13_excel(db, actor, year, month, department_id)

    log_action(
        db, actor, "timesheet", None, "timesheet_exported_excel",
        after={"year": year, "month": month, "department_id": department_id},
    )
    db.commit()

    filename = f"timesheet_T13_{year}_{month:02d}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Adjustments: премии / KPI / аванс (задача 3.11a) ───────────────────────────

@router.get("/{year}/{month}/adjustments", response_model=list[AdjustmentRead])
def list_adjustments(
    year: int,
    month: int,
    department_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    _require_dept_access(actor, department_id)
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)
    return _load_adjustments(db, employees, year, month, actor)


@router.post("/adjustments", response_model=AdjustmentRead, status_code=status.HTTP_201_CREATED)
def create_adjustment(
    payload: AdjustmentCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    # Отдел рабочего места, на которое ляжет начисление (без position_id — основное).
    target, position = _check_position_access(actor, db, payload.employee_id, payload.position_id)
    if not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year")

    # Охранной позиции премии, KPI и аванс основной системы не начисляются: у
    # вахты свои премия, штраф и официальная выплата, а расчёт строки вахты эти
    # записи игнорирует (аудит 2-Г). Удаление уже введённого — разрешено.
    try:
        ensure_no_guard_accrual(db, position, "Премии, KPI и аванс", "начисляются")
    except GuardAccrualError as exc:
        raise _guard_accrual(exc)

    # Источник финансирования (task_funding_source): только у премии и KPI —
    # аванс это удержание, затрат юрлица за ним нет.
    funding_company = None
    if payload.funding_company_id is not None:
        if payload.kind == "advance":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Источник финансирования указывается только у премии и KPI",
            )
        funding_company = db.get(Company, payload.funding_company_id)
        if funding_company is None or not funding_company.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Компания-источник финансирования не найдена",
            )

    adj = EmployeeAdjustment(
        employee_id=payload.employee_id,
        position_id=position.id if position else None,
        year=payload.year,
        month=payload.month,
        kind=payload.kind,
        amount=payload.amount,
        reason=payload.reason,
        funding_company_id=funding_company.id if funding_company else None,
        created_by_id=actor.id,
    )
    db.add(adj)
    db.flush()
    log_action(
        db, actor, "employee_adjustment", adj.id, "create",
        after={"employee_id": adj.employee_id, "year": adj.year, "month": adj.month,
               "kind": adj.kind, "amount": str(adj.amount), "reason": adj.reason,
               "funding_company_id": adj.funding_company_id},
    )
    db.commit()
    db.refresh(adj)
    return AdjustmentRead(
        id=adj.id, employee_id=adj.employee_id, position_id=adj.position_id,
        year=adj.year, month=adj.month,
        kind=adj.kind, amount=adj.amount, reason=adj.reason,
        funding_company_id=adj.funding_company_id,
        funding_company_name=(
            company_display_name(funding_company) if funding_company else None
        ),
        created_by_id=adj.created_by_id, created_at=str(adj.created_at) if adj.created_at else None,
    )


@router.delete("/adjustments/{adjustment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_adjustment(
    adjustment_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    _require_finance_role(actor)
    adj = db.get(EmployeeAdjustment, adjustment_id)
    if not adj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
    _check_position_access(actor, db, adj.employee_id, adj.position_id)
    log_action(
        db, actor, "employee_adjustment", adj.id, "delete",
        before={"employee_id": adj.employee_id, "year": adj.year, "month": adj.month,
                "kind": adj.kind, "amount": str(adj.amount)},
    )
    db.delete(adj)
    db.commit()


# ── Loan: ручная правка удержания за месяц (задача 3.11a) ───────────────────────

@router.post("/loan-override", status_code=status.HTTP_200_OK)
def set_loan_override(
    payload: LoanOverrideInput,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Скорректировать сумму удержания по займу за конкретный месяц."""
    _require_finance_role(actor)
    target, _ = _check_position_access(actor, db, payload.employee_id, None, loan=True)
    try:
        ensure_no_guard_accrual(db, loan_position(target), "Удержание по займу", "правится")
    except GuardAccrualError as exc:
        raise _guard_accrual(exc)
    if target.loan_amount is None or target.loan_term_months is None or target.loan_start_date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="У сотрудника не настроен займ",
        )
    if not (1 <= payload.month <= 12) or not (2000 <= payload.year <= 2100):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month")

    from app.services.payout import load_loan_overrides, loan_month_state

    # Плановая доля на этот месяц (справочно) — без учёта самой правки этого месяца.
    overrides = load_loan_overrides(db, [target.id]).get(target.id, {})
    overrides.pop((payload.year, payload.month), None)
    state = loan_month_state(
        target.loan_amount, target.loan_term_months, target.loan_start_date,
        payload.year, payload.month, overrides,
    )
    planned = state.planned if state else payload.actual_amount

    existing = (
        db.query(LoanDeduction)
        .filter(
            LoanDeduction.employee_id == payload.employee_id,
            LoanDeduction.year == payload.year,
            LoanDeduction.month == payload.month,
        )
        .first()
    )
    if existing:
        before = {"actual_amount": str(existing.actual_amount)}
        existing.actual_amount = payload.actual_amount
        existing.planned_amount = planned
        db.flush()
        log_action(db, actor, "loan_deduction", existing.id, "update",
                   before=before, after={"actual_amount": str(payload.actual_amount)})
        row = existing
    else:
        row = LoanDeduction(
            employee_id=payload.employee_id,
            year=payload.year,
            month=payload.month,
            planned_amount=planned,
            actual_amount=payload.actual_amount,
            created_by_id=actor.id,
        )
        db.add(row)
        db.flush()
        log_action(db, actor, "loan_deduction", row.id, "create",
                   after={"year": payload.year, "month": payload.month,
                          "actual_amount": str(payload.actual_amount)})
    db.commit()
    return {"employee_id": payload.employee_id, "year": payload.year,
            "month": payload.month, "planned_amount": str(planned),
            "actual_amount": str(payload.actual_amount)}


@router.delete("/loan-override/{employee_id}/{year}/{month}", status_code=status.HTTP_204_NO_CONTENT)
def delete_loan_override(
    employee_id: int,
    year: int,
    month: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Убрать ручную правку — вернуть плановое удержание за месяц."""
    _require_finance_role(actor)
    _check_position_access(actor, db, employee_id, None, loan=True)
    row = (
        db.query(LoanDeduction)
        .filter(
            LoanDeduction.employee_id == employee_id,
            LoanDeduction.year == year,
            LoanDeduction.month == month,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Правка не найдена")
    log_action(db, actor, "loan_deduction", row.id, "delete",
               before={"actual_amount": str(row.actual_amount)})
    db.delete(row)
    db.commit()
