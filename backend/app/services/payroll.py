from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from app.models.employee_absences import ABSENCE_CODES, AbsenceKind
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.services.absences import sick_limit_days, split_sick_dates_by_limit
from app.services.calendar import (
    is_holiday,
    is_short_day,
    norm_hours_for_period,
    workdays_in_month,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_ONE_HALF = Decimal("1.5")
_HUNDRED = Decimal("100")
_PERCENT_Q = Decimal("0.1")

# День отпуска/больничного оплачивается как стандартная смена 8 ч
# (формула образца финдира: оклад / норма_часов × дни × 8).
ABSENCE_DAY_HOURS = Decimal("8")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_ONE, rounding=ROUND_HALF_EVEN)


def _distribute_whole_rubles(
    total: Decimal, weights: dict[int, Decimal]
) -> dict[int, Decimal]:
    """
    Распределяет целую сумму total по ключам пропорционально weights так,
    чтобы сумма частей была РОВНО равна total (метод наибольших остатков).
    Независимое округление долей даёт расхождение с итогом до ±N/2 руб. —
    для разнесения по юрлицам это недопустимо.
    """
    weight_sum = sum(weights.values(), _ZERO)
    if weight_sum <= _ZERO or total == _ZERO:
        return {key: _ZERO for key in weights}

    parts: dict[int, Decimal] = {}
    remainders: dict[int, Decimal] = {}
    for key, w in weights.items():
        exact = total * w / weight_sum
        floor = exact.to_integral_value(rounding=ROUND_FLOOR)
        parts[key] = floor
        remainders[key] = exact - floor

    leftover = int(total - sum(parts.values(), _ZERO))
    # Остаток (0..N-1 руб.) — по рублю ключам с наибольшими остатками;
    # при равенстве — детерминированно по company_id.
    for key in sorted(weights, key=lambda k: (-remainders[k], k))[:leftover]:
        parts[key] += _ONE
    return parts


def _weekend_pay(employee: Employee, holiday_hours: Decimal, hourly_rate: Decimal) -> Decimal:
    """
    Оплата праздничных/выходных часов по настройкам конкретного сотрудника
    (правка 3.9-3). По умолчанию — коэффициент 1.5.
      - coefficient: holiday_hours × hourly_rate × коэффициент (0 = не оплачивается)
      - fixed_rate:  holiday_hours × фикс_ставка (не зависит от оклада)
    """
    if holiday_hours <= _ZERO:
        return _ZERO

    pay_type = getattr(employee, "weekend_pay_type", None) or "coefficient"

    if pay_type == "fixed_rate":
        fixed = getattr(employee, "weekend_fixed_rate", None)
        if fixed is None:
            return _ZERO
        return holiday_hours * Decimal(str(fixed))

    coeff = getattr(employee, "weekend_coefficient", None)
    coeff = _ONE_HALF if coeff is None else Decimal(str(coeff))
    return holiday_hours * hourly_rate * coeff


def _overtime_coeff(employee: Employee) -> Decimal:
    """Коэффициент переработки сотрудника (0/1/1.5), дефолт 1.5 (задача 3.11b п.0)."""
    coeff = getattr(employee, "overtime_coefficient", None)
    return _ONE_HALF if coeff is None else Decimal(str(coeff))


def daily_norm_hours(
    calendar_data: dict, work_date: date, hours_per_shift: int | Decimal
) -> Decimal:
    """
    Дневная норма сотрудника на конкретную дату:
      - обычный рабочий день → длительность смены;
      - сокращённый (предпраздничный) → смена − 1;
      - выходной/праздник → 0 (эти часы — отдельная категория «праздничные»).
    """
    if is_holiday(calendar_data, work_date.month, work_date.day):
        return _ZERO
    shift = Decimal(str(hours_per_shift))
    if is_short_day(calendar_data, work_date.month, work_date.day):
        shift -= _ONE
    return max(_ZERO, shift)


def daily_overtime_hours(
    calendar_data: dict,
    hours_by_date: dict[date, Decimal],
    hours_per_shift: int | Decimal,
) -> Decimal:
    """
    Переработка ПО ДНЯМ: для каждого дня max(0, факт − дневная норма), сумма за месяц.
    Часы всех компаний в дне уже должны быть просуммированы в hours_by_date.
    Недоработка одного дня НЕ компенсирует переработку другого.
    Праздничные/выходные дни сюда не попадают — там дневная норма 0 и часы
    учитываются отдельной категорией (см. вызывающий код).
    """
    overtime = _ZERO
    for work_date, hours in hours_by_date.items():
        norm = daily_norm_hours(calendar_data, work_date, hours_per_shift)
        if norm > _ZERO and hours > norm:
            overtime += hours - norm
    return overtime


def absence_pay(
    hourly_rate: Decimal | None, paid_days: int, day_hours: Decimal = ABSENCE_DAY_HOURS
) -> Decimal:
    """
    Оплата дней отпуска/больничного: `оклад / норма_часов × (дни × 8)`.

    Больничный в этой части — 100% по той же формуле, без годового лимита
    (лимит — отдельная задача, часть 2).
    """
    if hourly_rate is None or paid_days <= 0:
        return _ZERO
    return hourly_rate * day_hours * Decimal(paid_days)


@dataclass
class CompanyBreakdown:
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    percent: Decimal
    overtime_hours: Decimal
    holiday_hours: Decimal
    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total: Decimal


@dataclass
class EmployeePayroll:
    employee_id: int
    employee_name: str
    rate: Decimal | None
    schedule_name: str | None

    total_hours: Decimal
    norm_hours: Decimal | None
    delta_hours: Decimal | None
    overtime_hours: Decimal
    holiday_hours: Decimal

    norm_days: int | None
    fact_days: int

    hourly_rate: Decimal | None

    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total_amount: Decimal

    # Отсутствия (ОТ/ДО/Б/Н). *_days — сколько дней отмечено кодом,
    # *_paid_days — сколько из них рабочих по календарю (за них и платим).
    vacation_days: int
    unpaid_days: int
    sick_days: int
    absent_days: int
    vacation_paid_days: int
    sick_paid_days: int
    vacation_amount: Decimal
    sick_amount: Decimal

    # Годовой лимит больничного (часть 2): сколько оплачено, сколько сверх лимита
    sick_limit_days: int
    sick_days_used_before: int
    sick_unpaid_days: int
    sick_limit_remaining: int

    breakdown_by_company: list[CompanyBreakdown]
    is_calculable: bool
    reason_if_not_calculable: str | None


def calculate_employee_payroll(
    employee: Employee,
    entries: list[TimesheetEntry],
    calendar_data: dict | None,
    year: int,
    month: int,
    companies_by_id: dict[int, tuple[str, str]] | None = None,
    absences: list | None = None,
    sick_days_used_before: int = 0,
    sick_limit: int | None = None,
) -> EmployeePayroll:
    """
    Чистая функция: считает зарплату сотрудника за период.
    Не лезет в БД, принимает все данные на вход.
    companies_by_id: dict[company_id → (code, name)]
    absences: список EmployeeAbsence сотрудника за месяц (ОТ/ДО/Б/Н).
    sick_days_used_before: оплачиваемых дней больничного израсходовано в этом
        году ДО текущего месяца (годовой лимит, часть 2) — считает вызывающий
        код по всем месяцам года, здесь только применяется остаток.
    sick_limit: годовой лимит дней; None — из настроек.
    """
    if companies_by_id is None:
        companies_by_id = {}
    if sick_limit is None:
        sick_limit = sick_limit_days()

    # ── Отсутствия: дни по видам + сколько из них рабочих по календарю ────────
    # Норма от отсутствий НЕ меняется, факт часов — тоже (в день отсутствия
    # часов нет по инварианту взаимоисключения). Меняются только начисления.
    absence_days: dict[str, int] = {kind: 0 for kind in ABSENCE_CODES}
    absence_paid_days: dict[str, int] = {kind: 0 for kind in ABSENCE_CODES}
    sick_dates: list[date] = []
    for a in absences or []:
        absence_days[a.kind] = absence_days.get(a.kind, 0) + 1
        if a.kind == AbsenceKind.sick.value:
            sick_dates.append(a.work_date)
            continue  # больничный считаем ниже, через годовой лимит
        # Платим только за рабочие дни: выходной/праздник в норму не входит,
        # иначе «оклад за отработанное + отпускные» вылезет за полный оклад.
        if calendar_data is None or not is_holiday(
            calendar_data, a.work_date.month, a.work_date.day
        ):
            absence_paid_days[a.kind] = absence_paid_days.get(a.kind, 0) + 1

    # Больничный: первые sick_limit рабочих дней в году — 100%, дальше 0.
    # Хронология внутри месяца и между месяцами — по дате (см. services.absences).
    sick_paid_dates, sick_over_dates = split_sick_dates_by_limit(
        sick_dates, calendar_data, sick_limit, sick_days_used_before,
    )
    absence_paid_days[AbsenceKind.sick.value] = len(sick_paid_dates)
    sick_unpaid_days = len(sick_over_dates)
    sick_limit_remaining = max(
        0, sick_limit - max(0, sick_days_used_before) - len(sick_paid_dates)
    )

    # Aggregate hours by company and by date; detect holiday hours per company.
    company_hours: dict[int, Decimal] = {}
    company_holiday_hours: dict[int, Decimal] = {}
    hours_by_date: dict[date, Decimal] = {}
    # Будние (непраздничные) часы по дням — сумма по ВСЕМ компаниям в этот день.
    # База для по-дневного расчёта переработки.
    regular_hours_by_date: dict[date, Decimal] = {}
    total_hours = _ZERO
    total_holiday_hours = _ZERO

    for entry in entries:
        cid = entry.company_id
        h = entry.hours if isinstance(entry.hours, Decimal) else Decimal(str(entry.hours))
        total_hours += h
        if cid not in company_hours:
            company_hours[cid] = _ZERO
            company_holiday_hours[cid] = _ZERO
        company_hours[cid] += h
        hours_by_date[entry.work_date] = hours_by_date.get(entry.work_date, _ZERO) + h

        if calendar_data is not None and is_holiday(
            calendar_data, entry.work_date.month, entry.work_date.day
        ):
            company_holiday_hours[cid] += h
            total_holiday_hours += h
        else:
            regular_hours_by_date[entry.work_date] = (
                regular_hours_by_date.get(entry.work_date, _ZERO) + h
            )

    # Норма/факт дней (правка 3.9-4) — справочные, в деньгах не участвуют.
    # Норма дней = рабочих дней по календарю (сокращённые считаются как полный день).
    # Факт дней = дней, в которых есть хотя бы один час работы (по всем компаниям).
    norm_days: int | None = (
        workdays_in_month(calendar_data, year, month) if calendar_data is not None else None
    )
    fact_days = len(hours_by_date)

    # Determine calculability and norm
    schedule = employee.schedule
    schedule_name = schedule.name if schedule else None
    is_calculable = True
    reason: str | None = None
    norm_hours: Decimal | None = None

    if schedule is None:
        is_calculable = False
        reason = "Не задан график"
    elif schedule.schedule_type == "shift":
        is_calculable = False
        reason = "Сменный график не поддерживается"
    elif calendar_data is None:
        is_calculable = False
        reason = "Производственный календарь не загружен"
    else:
        norm_val = norm_hours_for_period(calendar_data, year, month, schedule.hours_per_shift)
        norm_hours = Decimal(str(norm_val))
        if norm_hours == _ZERO:
            is_calculable = False
            reason = "Норма не определена (0 рабочих дней)"

    rate = employee.rate
    if is_calculable and (rate is None or rate == _ZERO):
        is_calculable = False
        reason = "Не задан оклад"

    # Переработка ПО ДНЯМ (task_overtime_daily, финальное решение — откат помесячного
    # варианта 3.11b п.0): для каждого дня max(0, факт_дня − дневная норма смены),
    # суммируем за месяц. Часы всех компаний в дне складываются, недоработка одного
    # дня не гасит переработку другого.
    # Праздничные/выходные часы — отдельная категория (правка 3.9-3): в переработку
    # и базу оклада не входят, оплачиваются по правилам выходных. Поэтому база
    # переработки — это будние (непраздничные) часы.
    regular_hours = total_hours - total_holiday_hours
    delta_hours: Decimal | None = None
    overtime_hours = _ZERO
    regular_credited_hours = regular_hours
    if norm_hours is not None and calendar_data is not None and schedule is not None:
        delta_hours = total_hours - norm_hours
        overtime_hours = daily_overtime_hours(
            calendar_data, regular_hours_by_date, schedule.hours_per_shift
        )
        # Зачётные будние часы = Σ min(факт_дня, дневная норма) ≤ месячная норма.
        regular_credited_hours = regular_hours - overtime_hours

    # Financial amounts
    hourly_rate: Decimal | None = None
    base_amount = _ZERO
    overtime_amount = _ZERO
    holiday_amount = _ZERO

    vacation_amount = _ZERO
    sick_amount = _ZERO

    if is_calculable and rate is not None and norm_hours is not None and norm_hours > _ZERO:
        hourly_rate = rate / norm_hours
        # Оклад ПРОПОРЦИОНАЛЬНО отработанному: зачётные будние часы / норма.
        # Дни отсутствия часов не дают, поэтому оклад за них не начисляется —
        # они оплачиваются отдельно (отпускные/больничные) и сумма не задваивается.
        base_amount = rate * min(_ONE, regular_credited_hours / norm_hours)
        # Переработка: (оклад/норма) × часы × коэффициент сотрудника (0/1/1.5).
        overtime_amount = overtime_hours * hourly_rate * _overtime_coeff(employee)
        # Праздничные/выходные — по персональным настройкам сотрудника (правка 3.9-3).
        holiday_amount = _weekend_pay(employee, total_holiday_hours, hourly_rate)
        # Отпуск и больничный — отдельные начисления по «дни × 8».
        vacation_amount = absence_pay(
            hourly_rate, absence_paid_days[AbsenceKind.vacation.value]
        )
        sick_amount = absence_pay(
            hourly_rate, absence_paid_days[AbsenceKind.sick.value]
        )

    base_amount = _round(base_amount)
    overtime_amount = _round(overtime_amount)
    holiday_amount = _round(holiday_amount)
    vacation_amount = _round(vacation_amount)
    sick_amount = _round(sick_amount)
    # Итог включает оплату отсутствий; распределение по компаниям (breakdown)
    # ниже — только по «рабочим» категориям: отсутствие к юрлицу не привязано,
    # разнесение отпускных по юрлицам делает ведомость (проценты каскада).
    total_amount = (
        base_amount + overtime_amount + holiday_amount + vacation_amount + sick_amount
    )

    # Company breakdown — суммы частей по каждой категории сходятся с итогом
    breakdown: list[CompanyBreakdown] = []
    if is_calculable and total_hours > _ZERO:
        base_parts = _distribute_whole_rubles(base_amount, company_hours)
        overtime_parts = _distribute_whole_rubles(overtime_amount, company_hours)
        holiday_parts = _distribute_whole_rubles(holiday_amount, company_holiday_hours)
        # Часы переработки по компании — пропорционально часам компании
        # (целочисленным методом наибольших остатков, сумма = overtime_hours).
        overtime_hours_parts = _distribute_whole_rubles(overtime_hours, company_hours)
        for cid in sorted(company_hours.keys()):
            comp_hours = company_hours[cid]
            proportion = comp_hours / total_hours
            percent = (proportion * _HUNDRED).quantize(_PERCENT_Q, rounding=ROUND_HALF_EVEN)
            comp_base = base_parts[cid]
            comp_overtime = overtime_parts[cid]
            comp_holiday = holiday_parts[cid]
            code, name = companies_by_id.get(cid, ("", ""))
            breakdown.append(CompanyBreakdown(
                company_id=cid,
                company_code=code,
                company_name=name,
                hours=comp_hours,
                percent=percent,
                overtime_hours=overtime_hours_parts.get(cid, _ZERO),
                holiday_hours=company_holiday_hours.get(cid, _ZERO),
                base_amount=comp_base,
                overtime_amount=comp_overtime,
                holiday_amount=comp_holiday,
                total=comp_base + comp_overtime + comp_holiday,
            ))

    return EmployeePayroll(
        employee_id=employee.id,
        employee_name=employee.full_name,
        rate=rate,
        schedule_name=schedule_name,
        total_hours=total_hours,
        norm_hours=norm_hours,
        delta_hours=delta_hours,
        overtime_hours=overtime_hours,
        holiday_hours=total_holiday_hours,
        norm_days=norm_days,
        fact_days=fact_days,
        hourly_rate=hourly_rate,
        base_amount=base_amount,
        overtime_amount=overtime_amount,
        holiday_amount=holiday_amount,
        total_amount=total_amount,
        vacation_days=absence_days[AbsenceKind.vacation.value],
        unpaid_days=absence_days[AbsenceKind.unpaid.value],
        sick_days=absence_days[AbsenceKind.sick.value],
        absent_days=absence_days[AbsenceKind.absent.value],
        vacation_paid_days=absence_paid_days[AbsenceKind.vacation.value],
        sick_paid_days=absence_paid_days[AbsenceKind.sick.value],
        vacation_amount=vacation_amount,
        sick_amount=sick_amount,
        sick_limit_days=sick_limit,
        sick_days_used_before=max(0, sick_days_used_before),
        sick_unpaid_days=sick_unpaid_days,
        sick_limit_remaining=sick_limit_remaining,
        breakdown_by_company=breakdown,
        is_calculable=is_calculable,
        reason_if_not_calculable=reason,
    )
