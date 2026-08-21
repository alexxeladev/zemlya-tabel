"""
Ночные смены: вычисляемая ставка, лимит по фонду отдела, отметки в табеле
(task_night_shifts_rework).

Единственный источник правды по ночным. Ставка вручную НЕ задаётся — она следует
из фонда отдела:

    ставка = фонд_отдела / календарные_дни_месяца
    лимит_смен_отдела = фонд / ставка = календарные дни месяца

Из этого же следует главное свойство модели: `число_смен × ставка ≤ фонд`, то
есть фонд не перерасходуется, пока соблюдается лимит числа смен. Лимит —
СУММАРНЫЙ по отделу: в одну ночь могут выйти несколько человек, и каждый выход
тратит одну смену фонда.

Деньги здесь не начисляются — оплата (`смены × ставка`) считается в
`services.payroll` вместе с остальным расчётом, чтобы формула жила в одном месте.
"""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.departments import DEFAULT_NIGHT_SHIFT_FUND, Department
from app.models.employees import Employee
from app.models.night_shifts import NightShift
from app.models.positions import EmployeePosition

_ZERO = Decimal("0")
_KOPECKS = Decimal("0.01")

__all__ = [
    "NightContext",
    "NightLimitExceeded",
    "department_fund",
    "get_month_night_shifts",
    "load_night_context",
    "night_amount",
    "night_rate_for_month",
    "night_shift_limit",
    "set_night_shift",
]


class NightLimitExceeded(Exception):
    """Фонд отдела исчерпан: ещё одна смена вышла бы за лимит.

    Проверка авторитетная и живёт на бэке — фронт только показывает остаток
    заранее, но решает сервер (иначе двое табельщиков в разных вкладках
    перерасходовали бы фонд).
    """

    def __init__(self, remaining: int, limit: int):
        self.remaining = remaining
        self.limit = limit
        super().__init__(
            f"Лимит ночных смен отдела исчерпан "
            f"(осталось {remaining} смен из {limit})"
        )


def days_in_month(year: int, month: int) -> int:
    """Календарных дней в месяце — 28/29/30/31, НЕ рабочих: ночная смена к
    графику не привязана, поэтому и делитель ставки календарный."""
    return _cal.monthrange(year, month)[1]


def department_fund(department: Department | None) -> Decimal:
    """Фонд ночных смен отдела; без отдела ночных смен нет вовсе.

    None в колонке (объект ещё не сохранён — server_default не сработал)
    читается как дефолт, иначе ставка молча стала бы нулевой.
    """
    if department is None:
        return _ZERO
    fund = getattr(department, "night_shift_fund", None)
    if fund is None:
        return DEFAULT_NIGHT_SHIFT_FUND
    return Decimal(str(fund))


def night_rate_for_month(fund: Decimal, year: int, month: int) -> Decimal:
    """Ставка одной ночной смены = фонд / календарные дни месяца.

    Фиксирована на месяц: зависит только от фонда и длины месяца, от числа
    отмеченных смен НЕ зависит и в течение месяца не плавает. Округляем до
    копеек (100000 / 31 → 3225.81); рубли округляются уже в сумме начисления.
    """
    days = days_in_month(year, month)
    if fund <= _ZERO or days <= 0:
        return _ZERO
    return (Decimal(fund) / Decimal(days)).quantize(_KOPECKS, rounding=ROUND_HALF_EVEN)


def night_shift_limit(fund: Decimal, year: int, month: int) -> int:
    """Сколько ночных смен отдела оплачивает фонд за месяц.

    `лимит = фонд / ставка`, а так как ставка сама равна `фонд / дни`, лимит
    тождественно равен числу календарных дней месяца. Считаем именно так, а не
    делением на округлённую до копеек ставку: 100000 / 3225.81 = 30.99…, и
    floor отнял бы последнюю смену просто из-за округления.
    """
    if fund <= _ZERO:
        return 0
    return days_in_month(year, month)


def night_amount(shifts: int, rate: Decimal | None) -> Decimal:
    """Надбавка за ночные = число смен × ставка (округление — у вызывающего)."""
    if not shifts or rate is None or rate <= _ZERO:
        return _ZERO
    return Decimal(rate) * Decimal(shifts)


# ── Загрузка данных месяца ────────────────────────────────────────────────────

@dataclass
class NightContext:
    """Всё про ночные смены месяца одним куском: сколько у кого, почём и сколько
    ещё можно. Считается один раз на запрос — и расчётом ЗП, и табелем."""

    year: int
    month: int
    # position_id → сколько ночных смен отмечено (только у переданных сотрудников)
    shifts_by_position: dict[int, int] = field(default_factory=dict)
    # department_id → фонд / ставка / лимит / израсходовано ПО ВСЕМУ ОТДЕЛУ
    name_by_department: dict[int, str] = field(default_factory=dict)
    fund_by_department: dict[int, Decimal] = field(default_factory=dict)
    rate_by_department: dict[int, Decimal] = field(default_factory=dict)
    limit_by_department: dict[int, int] = field(default_factory=dict)
    used_by_department: dict[int, int] = field(default_factory=dict)

    def shifts_of(self, position: EmployeePosition | None) -> int:
        if position is None:
            return 0
        return self.shifts_by_position.get(position.id, 0)

    def rate_of(self, position: EmployeePosition | None) -> Decimal | None:
        """Ставка рабочего места = ставка его отдела. Без отдела ночных нет."""
        if position is None or position.department_id is None:
            return None
        return self.rate_by_department.get(position.department_id)

    def remaining_of(self, department_id: int | None) -> int:
        if department_id is None:
            return 0
        limit = self.limit_by_department.get(department_id, 0)
        return max(0, limit - self.used_by_department.get(department_id, 0))


def get_month_night_shifts(
    db: Session, employees: list[Employee], year: int, month: int
) -> list[NightShift]:
    """Отметки ночных смен переданных сотрудников за месяц."""
    if not employees:
        return []
    start = date(year, month, 1)
    end = date(year, month, days_in_month(year, month))
    return (
        db.query(NightShift)
        .filter(
            NightShift.employee_id.in_([e.id for e in employees]),
            NightShift.work_date >= start,
            NightShift.work_date <= end,
        )
        .all()
    )


def count_by_department(
    db: Session, department_ids: list[int], year: int, month: int
) -> dict[int, int]:
    """{department_id: сколько ночных смен отмечено в отделе за месяц}.

    Считаются ВСЕ смены отдела, а не только видимые актору сотрудники: фонд
    общий, и остаток лимита у менеджера обязан совпадать с реальным.
    """
    if not department_ids:
        return {}
    start = date(year, month, 1)
    end = date(year, month, days_in_month(year, month))
    rows = (
        db.query(EmployeePosition.department_id, func.count(NightShift.id))
        .join(NightShift, NightShift.position_id == EmployeePosition.id)
        .filter(
            EmployeePosition.department_id.in_(department_ids),
            NightShift.work_date >= start,
            NightShift.work_date <= end,
        )
        .group_by(EmployeePosition.department_id)
        .all()
    )
    return {dept_id: count for dept_id, count in rows}


def load_night_context(
    db: Session,
    employees: list[Employee],
    year: int,
    month: int,
    department_ids: list[int] | None = None,
) -> NightContext:
    """Ночные смены месяца для набора сотрудников + состояние фондов их отделов.

    `department_ids` — отделы, для которых нужен индикатор фонда; по умолчанию
    берутся отделы позиций переданных сотрудников.
    """
    ctx = NightContext(year=year, month=month)

    for shift in get_month_night_shifts(db, employees, year, month):
        ctx.shifts_by_position[shift.position_id] = (
            ctx.shifts_by_position.get(shift.position_id, 0) + 1
        )

    dept_ids = set(department_ids or [])
    if department_ids is None:
        dept_ids = {
            pos.department_id
            for emp in employees
            for pos in emp.positions
            if pos.department_id is not None
        }
    if not dept_ids:
        return ctx

    departments = (
        db.query(Department).filter(Department.id.in_(sorted(dept_ids))).all()
    )
    for dept in departments:
        fund = department_fund(dept)
        ctx.name_by_department[dept.id] = dept.name
        ctx.fund_by_department[dept.id] = fund
        ctx.rate_by_department[dept.id] = night_rate_for_month(fund, year, month)
        ctx.limit_by_department[dept.id] = night_shift_limit(fund, year, month)
    ctx.used_by_department = count_by_department(
        db, [d.id for d in departments], year, month
    )
    return ctx


# ── Мутация: отметить / снять ночную смену ────────────────────────────────────

def set_night_shift(
    db: Session,
    actor: Employee,
    employee_id: int,
    position_id: int | None,
    work_date: date,
    value: bool,
) -> NightShift | None:
    """
    Отметить (value=True) или снять (False) выход в ночь.

    Дневные часы этого дня НЕ трогаются: ночная смена — отдельная подработка,
    она сосуществует и с часами, и с кодом отсутствия.

    Блокировка фонда авторитетна: перед созданием считается СУММАРНОЕ число
    ночных смен отдела за месяц, и если ещё одна вышла бы за лимит — отметка не
    ставится (`NightLimitExceeded`). Снятие лимит освобождает — отдельного учёта
    «потрачено» нет, всегда считается по факту.

    Период должен быть в draft — как для часов и отсутствий.
    """
    from app.services.timesheet import _check_period_lock

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ValueError("Сотрудник не найден")

    position = employee.position_by_id(position_id)
    if position is None:
        raise ValueError("Рабочее место не найдено")
    if not position.has_night_shifts:
        raise ValueError(
            "Ночные смены не включены для этого рабочего места"
        )
    if position.department_id is None:
        # Фонд — свойство отдела, значит без отдела нет ни ставки, ни лимита.
        raise ValueError(
            "Ночные смены доступны только сотрудникам отдела: "
            "фонд задаётся на отделе"
        )

    _check_period_lock(db, employee_id, work_date, position.id)

    existing = (
        db.query(NightShift)
        .filter(
            NightShift.position_id == position.id,
            NightShift.work_date == work_date,
        )
        .first()
    )

    if not value:
        if existing is None:
            return None
        log_action(
            db, actor, "night_shift", existing.id, "delete",
            before={"employee_id": employee_id, "position_id": position.id,
                    "work_date": str(work_date)},
        )
        db.delete(existing)
        db.commit()
        return None

    if existing is not None:
        return existing  # уже отмечено — повторный клик лимит не тратит

    department = position.department
    fund = department_fund(department)
    limit = night_shift_limit(fund, work_date.year, work_date.month)
    used = count_by_department(
        db, [position.department_id], work_date.year, work_date.month
    ).get(position.department_id, 0)
    if used + 1 > limit:
        raise NightLimitExceeded(remaining=max(0, limit - used), limit=limit)

    shift = NightShift(
        employee_id=employee_id,
        position_id=position.id,
        work_date=work_date,
        created_by_id=actor.id,
    )
    db.add(shift)
    db.flush()
    log_action(
        db, actor, "night_shift", shift.id, "create",
        after={"employee_id": employee_id, "position_id": position.id,
               "work_date": str(work_date), "department_id": position.department_id},
    )
    db.commit()
    db.refresh(shift)
    return shift
