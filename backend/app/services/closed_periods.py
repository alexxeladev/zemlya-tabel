"""
Запрет правок закрытого периода (task_stage3_historicity, часть 2).

> Корректировка закрытого периода запрещена — только через переоткрытие. Правка
> премий, аванса, займа и переопределений в закрытом периоде отклоняется на
> бэке во всех точках входа.

Правило то же, что у часов табеля (`can_edit_cells`): правится только
черновик, **`pending_review` блокирует наравне с `closed`** — период,
отправленный бухгалтеру, не должен меняться под ним (решение заказчика).

Как устроено: слушатель `before_flush` смотрит на КАЖДУЮ запись помесячных
денежных данных, уходящую в базу, — премии/KPI/аванс, ручное удержание займа,
месячные проценты распределения, количественный показатель отдела, набор
процентов рабочего места с датой начала, — определяет её (отдел, месяц) и
отклоняет, если месяц не в черновике. Проверка не в обработчиках, а в сессии:
новый эндпойнт, пишущий те же данные, не сможет про неё забыть (тот же приём,
что у журнала справочников и кэша дашборда). Строка периода берётся под
разделяемую блокировку (`lock_period`), как при записи часов: закрытие не
проскочит между проверкой и коммитом.

Часы, отсутствия, ночные и назначения вахты по-прежнему проверяет своя воронка
(`_check_period_lock`, `month_lock_status`) — сюда они не дублируются.

Исключение — `allow_closed_writes(db)`: служебные записи, которым по задаче
разрешено трогать закрытые месяцы (заморозка раскладов при переносе отдела,
которую этап 3 не трогает).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.company_shares import CompanyShareOverride, EmployeeCompanyShare
from app.models.department_quantities import DepartmentQuantity
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.loan_deductions import LoanDeduction
from app.models.position_terms import TERMS_BEGINNING
from app.models.positions import EmployeePosition
from app.models.timesheet_periods import TimesheetPeriod
from app.services.position_terms import ClosedPeriodError

_BYPASS = "closed_period_bypass"

_KIND_LABELS = {
    EmployeeAdjustment: "премии, KPI и аванс",
    LoanDeduction: "удержание займа",
    CompanyShareOverride: "проценты распределения",
    DepartmentQuantity: "количественный показатель",
    EmployeeCompanyShare: "распределение рабочего места",
}


def _period(db: Session, department_id: int | None, year: int, month: int):
    query = db.query(TimesheetPeriod).filter(
        TimesheetPeriod.year == year, TimesheetPeriod.month == month
    )
    if department_id is None:
        query = query.filter(TimesheetPeriod.department_id.is_(None))
    else:
        query = query.filter(TimesheetPeriod.department_id == department_id)
    return query.first()


def month_status(
    db: Session, department_id: int | None, year: int, month: int, *, lock: bool = False,
) -> str | None:
    """Статус, которым месяц отдела закрыт для правок, или None — черновик.

    Строки периода нет — месяц никто не открывал, это черновик. `lock=True` —
    строка периода берётся под разделяемую блокировку до конца транзакции.
    """
    period = _period(db, department_id, year, month)
    if period is None:
        return None
    if lock:
        from app.services.timesheet_periods import lock_period

        period = lock_period(db, period, exclusive=False)
    return None if period.status == "draft" else period.status


def closed_message(what: str, year: int, month: int, status: str) -> str:
    label = "закрыт" if status == "closed" else "на проверке у бухгалтера"
    return (
        f"Период {month:02d}.{year} {label}: {what} за него не меняются. "
        "Правка — только после переоткрытия (возврата в черновик)."
    )


def ensure_month_open(
    db: Session, department_id: int | None, year: int, month: int, what: str,
) -> None:
    status = month_status(db, department_id, year, month, lock=True)
    if status is not None:
        raise ClosedPeriodError(closed_message(what, year, month, status))


@contextmanager
def allow_closed_writes(db: Session):
    """Служебная запись в закрытый месяц (перенос отдела замораживает расклад)."""
    prev = db.info.get(_BYPASS)
    db.info[_BYPASS] = True
    try:
        yield
    finally:
        if prev is None:
            db.info.pop(_BYPASS, None)
        else:
            db.info[_BYPASS] = prev


# ── Кому какой отдел ──────────────────────────────────────────────────────────

def _position_dept(db: Session, employee_id: int, position_id: int | None) -> int | None:
    """Отдел рабочего места записи; `position_id IS NULL` — основная позиция."""
    if position_id is not None:
        position = db.get(EmployeePosition, position_id)
        return position.department_id if position is not None else None
    employee = db.get(Employee, employee_id)
    primary = employee.primary_position if employee is not None else None
    return primary.department_id if primary is not None else None


def _loan_dept(db: Session, employee_id: int) -> int | None:
    from app.services.guard_staff import loan_position

    employee = db.get(Employee, employee_id)
    position = loan_position(employee) if employee is not None else None
    return position.department_id if position is not None else None


def _keys(db: Session, obj) -> list[tuple[int | None, int, int]]:
    """(отдел, год, месяц), которые задевает запись. Пусто — не помесячная."""
    if isinstance(obj, EmployeeAdjustment):
        return [(_position_dept(db, obj.employee_id, obj.position_id), obj.year, obj.month)]
    if isinstance(obj, CompanyShareOverride):
        return [(_position_dept(db, obj.employee_id, obj.position_id), obj.year, obj.month)]
    if isinstance(obj, LoanDeduction):
        return [(_loan_dept(db, obj.employee_id), obj.year, obj.month)]
    if isinstance(obj, DepartmentQuantity):
        return [(obj.department_id, obj.year, obj.month)]
    if isinstance(obj, EmployeeCompanyShare):
        start = obj.effective_from
        if not isinstance(start, date) or start <= TERMS_BEGINNING:
            return []
        return [(_position_dept(db, obj.employee_id, obj.position_id), start.year, start.month)]
    return []


@event.listens_for(Session, "before_flush")
def _reject_closed_month_writes(session: Session, flush_context, instances) -> None:
    if session.info.get(_BYPASS):
        return
    objs = [
        o for o in list(session.new) + list(session.dirty) + list(session.deleted)
        if isinstance(o, tuple(_KIND_LABELS))
    ]
    if not objs:
        return
    checked: dict[tuple[int | None, int, int], str | None] = {}
    with session.no_autoflush:
        for obj in objs:
            if obj in session.dirty and not session.is_modified(obj):
                continue
            for key in _keys(session, obj):
                if key not in checked:
                    checked[key] = month_status(session, *key, lock=True)
                status = checked[key]
                if status is not None:
                    raise ClosedPeriodError(
                        closed_message(_KIND_LABELS[type(obj)], key[1], key[2], status)
                    )
