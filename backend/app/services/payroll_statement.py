"""
Сводная ведомость «Расчёт ЗП» (задача 3.11b).

Использует ТОТ ЖЕ расчёт что табель (calculate_employee_payroll + compute_payout),
поверх него — управленческое распределение Итого начислено между юрлицами в %.
Распределение: дефолт из карточки сотрудника + помесячное переопределение (гибрид
как у займа). База распределения — Итого начислено (ДО вычета удержаний).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.schemas.payroll import (
    CompanyBreakdownRead,
    EmployeePayrollRead,
    PayrollSummaryRead,
)
from app.schemas.payroll_statement import (
    PayrollStatementRead,
    StatementCompanyAmount,
    StatementCompanyRef,
    StatementRow,
)
from app.services.absences import (
    get_month_absences,
    schedules_by_employee,
    sick_days_used_before_month,
)
from app.services.company_shares import (
    load_department_shares,
    load_employee_shares,
    load_month_overrides,
)
from app.services.distribution import distribute
from app.services.payout import (
    compute_payout,
    load_adjustment_sums,
    load_loan_overrides,
    loan_month_state,
)
from app.services.payroll import calculate_position_payroll
from app.services.positions import entries_by_position, visible_positions

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ── Payroll summary (единый источник для табеля и ведомости) ──────────────────

def _payroll_rows(
    employees: list[Employee],
    actor: Employee | None,
    department_id: int | None,
) -> list[tuple[Employee, EmployeePosition | None]]:
    """Строки расчёта: по одной на видимую позицию сотрудника.

    Без actor-а (внутренние вызовы, тесты) берутся все активные позиции. У
    сотрудника без единой позиции строка всё равно нужна — иначе он молча
    исчезнет из ведомости; считается она как «нет графика».
    """
    rows: list[tuple[Employee, EmployeePosition | None]] = []
    for emp in employees:
        positions = (
            visible_positions(emp, actor, department_id)
            if actor is not None
            else emp.active_positions
        )
        if not positions:
            # Ни одной видимой/активной позиции — строка всё равно нужна, иначе
            # сотрудник молча пропадёт из ведомости.
            rows.append((emp, emp.primary_position))
        else:
            rows.extend((emp, pos) for pos in positions)
    return rows


def _loan_belongs_to(employee: Employee, position: EmployeePosition | None) -> bool:
    """Займ гасится с ОДНОЙ позиции — той, что указана в карточке.

    `loan_position_id` пуст у займов, заведённых до появления позиций, — они
    удерживаются с основной, как и раньше.
    """
    if employee.loan_amount is None or position is None:
        return False
    if employee.loan_position_id is None:
        return bool(position.is_primary)
    return employee.loan_position_id == position.id


def build_payroll_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
    actor: Employee | None = None,
    department_id: int | None = None,
) -> PayrollSummaryRead:
    """Сводный расчёт ЗП — ОДНА СТРОКА НА ПОЗИЦИЮ (task_positions ч.A).

    Единый источник: используется и табелем (/payroll), и ведомостью (/statement).

    У сотрудника без совместительства позиция одна, и выдача совпадает с прежней
    построчно. У совместителя строк столько, сколько рабочих мест: каждое со
    своим окладом, графиком и нормой, а «к выплате» между ними НЕ суммируется —
    платят разные компании.

    actor/department_id — чтобы менеджер не увидел подработку сотрудника в чужом
    отделе; без actor берутся все активные позиции.
    """
    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    calendar_data = cal.data if cal else None

    companies = db.query(Company).filter(Company.is_active == True).all()  # noqa: E712
    companies_by_id = {c.id: (c.code, c.name) for c in companies}

    entries_by_employee: dict[int, list] = {}
    for e in entries:
        entries_by_employee.setdefault(e.employee_id, []).append(e)

    # Отсутствия месяца (ОТ/ДО/Б/Н) — дают отпускные/больничные и уменьшают
    # оклад пропорционально (в дне отсутствия часов нет).
    absences_by_employee: dict[int, list] = {}
    for a in get_month_absences(db, employees, year, month):
        absences_by_employee.setdefault(a.employee_id, []).append(a)

    emp_ids = [emp.id for emp in employees]
    primary_position_ids = {
        emp.id: (emp.primary_position.id if emp.primary_position else None)
        for emp in employees
    }
    adjustment_sums = load_adjustment_sums(
        db, emp_ids, year, month, primary_position_ids
    )
    loan_overrides = load_loan_overrides(db, emp_ids)
    # Годовой лимит больничного: сколько оплачиваемых дней Б уже израсходовано
    # с 1 января до этого месяца (часть 2).
    sick_used_before = sick_days_used_before_month(
        db, emp_ids, year, month, calendar_data, schedules_by_employee(employees)
    )

    payroll_items: list[EmployeePayrollRead] = []
    for emp, position in _payroll_rows(employees, actor, department_id):
        # Часы позиции: строки без position_id — доположенческие, они принадлежат
        # основной позиции (иначе миграция потеряла бы часы).
        by_position = entries_by_position(emp, entries_by_employee.get(emp.id, []))
        emp_entries = by_position.get(position.id, []) if position is not None else []
        p = calculate_position_payroll(
            emp, position, emp_entries, calendar_data, year, month, companies_by_id,
            absences=absences_by_employee.get(emp.id, []),
            sick_days_used_before=sick_used_before.get(emp.id, 0),
        )

        # Премии/KPI/аванс и займ адресованы КОНКРЕТНОЙ позиции: деньги
        # начисляются человеку, но попадают в «к выплате» того рабочего места,
        # на котором заработаны.
        position_id = position.id if position is not None else None
        sums = adjustment_sums.get(emp.id, {}).get(position_id, {})
        loan_state = None
        if _loan_belongs_to(emp, position):
            loan_state = loan_month_state(
                emp.loan_amount, emp.loan_term_months, emp.loan_start_date,
                year, month, loan_overrides.get(emp.id),
            )
        loan_deduction = loan_state.actual if loan_state else _ZERO
        payout = compute_payout(
            accrued_total=p.total_amount,
            premium_amount=sums.get("premium", _ZERO),
            kpi_amount=sums.get("kpi", _ZERO),
            advance_deduction=sums.get("advance", _ZERO),
            loan_deduction=loan_deduction,
        )

        breakdown = [
            CompanyBreakdownRead(
                company_id=bd.company_id,
                company_code=bd.company_code,
                company_name=bd.company_name,
                hours=bd.hours,
                percent=bd.percent,
                overtime_hours=bd.overtime_hours,
                off_schedule_hours=bd.off_schedule_hours,
                holiday_hours=bd.holiday_hours,
                base_amount=bd.base_amount,
                overtime_amount=bd.overtime_amount,
                off_schedule_amount=bd.off_schedule_amount,
                holiday_amount=bd.holiday_amount,
                total=bd.total,
            )
            for bd in p.breakdown_by_company
        ]
        payroll_items.append(EmployeePayrollRead(
            employee_id=p.employee_id,
            employee_name=p.employee_name,
            position_id=p.position_id,
            position_title=p.position_title,
            is_primary_position=p.is_primary_position,
            rate=p.rate,
            schedule_name=p.schedule_name,
            pay_type=p.pay_type,
            shift_rate=p.shift_rate,
            hour_rate=p.hour_rate,
            worked_shifts=p.worked_shifts,
            norm_shifts=p.norm_shifts,
            base_shifts=p.base_shifts,
            total_hours=p.total_hours,
            norm_hours=p.norm_hours,
            delta_hours=p.delta_hours,
            overtime_hours=p.overtime_hours,
            off_schedule_hours=p.off_schedule_hours,
            holiday_hours=p.holiday_hours,
            norm_days=p.norm_days,
            fact_days=p.fact_days,
            hourly_rate=p.hourly_rate,
            base_amount=p.base_amount,
            overtime_amount=p.overtime_amount,
            off_schedule_amount=p.off_schedule_amount,
            holiday_amount=p.holiday_amount,
            total_amount=p.total_amount,
            vacation_days=p.vacation_days,
            unpaid_days=p.unpaid_days,
            sick_days=p.sick_days,
            absent_days=p.absent_days,
            vacation_paid_days=p.vacation_paid_days,
            sick_paid_days=p.sick_paid_days,
            vacation_amount=p.vacation_amount,
            sick_amount=p.sick_amount,
            sick_limit_days=p.sick_limit_days,
            sick_days_used_before=p.sick_days_used_before,
            sick_unpaid_days=p.sick_unpaid_days,
            sick_limit_remaining=p.sick_limit_remaining,
            weekend_pay_type=position.weekend_pay_type if position else None,
            weekend_coefficient=position.weekend_coefficient if position else None,
            weekend_fixed_rate=position.weekend_fixed_rate if position else None,
            holiday_pay_type=position.holiday_pay_type if position else None,
            holiday_coefficient=position.holiday_coefficient if position else None,
            holiday_fixed_rate=position.holiday_fixed_rate if position else None,
            premium_amount=payout.premium_amount,
            kpi_amount=payout.kpi_amount,
            advance_deduction=payout.advance_deduction,
            loan_deduction=payout.loan_deduction,
            loan_remaining=loan_state.remaining_after if loan_state else _ZERO,
            loan_planned_deduction=loan_state.planned if loan_state else _ZERO,
            loan_is_manual=loan_state.is_manual if loan_state else False,
            total_deductions=payout.total_deductions,
            net_payout=payout.net_payout,
            net_payout_exact=payout.net_payout_exact,
            rounding_tail=payout.rounding_tail,
            breakdown_by_company=breakdown,
            is_calculable=p.is_calculable,
            reason_if_not_calculable=p.reason_if_not_calculable,
        ))

    return PayrollSummaryRead(
        year=year,
        month=month,
        employees=payroll_items,
        total_employees=len(payroll_items),
        total_hours=sum((p.total_hours for p in payroll_items), _ZERO),
        total_base_amount=sum((p.base_amount for p in payroll_items), _ZERO),
        total_overtime_amount=sum((p.overtime_amount for p in payroll_items), _ZERO),
        total_off_schedule_amount=sum((p.off_schedule_amount for p in payroll_items), _ZERO),
        total_holiday_amount=sum((p.holiday_amount for p in payroll_items), _ZERO),
        total_vacation_amount=sum((p.vacation_amount for p in payroll_items), _ZERO),
        total_sick_amount=sum((p.sick_amount for p in payroll_items), _ZERO),
        grand_total=sum((p.total_amount for p in payroll_items), _ZERO),
        total_premium=sum((p.premium_amount for p in payroll_items), _ZERO),
        total_kpi=sum((p.kpi_amount for p in payroll_items), _ZERO),
        total_deductions=sum((p.total_deductions for p in payroll_items), _ZERO),
        # Итог — сумма УЖЕ округлённых выплат, а не округление суммы.
        total_net_payout=sum((p.net_payout for p in payroll_items), _ZERO),
        total_net_payout_exact=sum((p.net_payout_exact for p in payroll_items), _ZERO),
        total_rounding_tail=sum((p.rounding_tail for p in payroll_items), _ZERO),
    )


# ── Распределение по компаниям (проценты) ─────────────────────────────────────

# Загрузка наборов процентов — в app.services.company_shares (общая с роутерами).

# Откуда взято распределение (task_distribution_v2 ч.3) — видно в ведомости и Excel.
SOURCE_MONTH = "month"          # ручной % за конкретный месяц
SOURCE_EMPLOYEE = "employee"    # распределение в карточке сотрудника
SOURCE_DEPARTMENT = "department"  # дефолт отдела
SOURCE_HOURS = "hours"          # авто по фактическим часам табеля


def resolve_shares(
    position_id: int | None,
    department_id: int | None,
    month_overrides: dict[int | None, dict[int, Decimal]],
    employee_shares: dict[int | None, dict[int, Decimal]],
    department_shares: dict[int, dict[int, Decimal]],
) -> tuple[dict[int, Decimal], str]:
    """Каскад приоритетов распределения: берётся ПЕРВОЕ заданное сверху вниз —
    месячный % > карточка (позиция) > дефолт отдела > авто по часам.

    Каскад разрешается для КОНКРЕТНОЙ ПОЗИЦИИ (task_positions ч.A): отдел берётся
    её, проценты — заданные для неё. Дефолт отдела применяется ТОЛЬКО к позициям
    без своего распределения: правка дефолта отдела не трогает тех, у кого задана
    карточка или месяц. Возвращает (проценты, источник). Для авто — пустой набор.
    """
    levels = (
        (month_overrides.get(position_id, {}), SOURCE_MONTH),
        (employee_shares.get(position_id, {}), SOURCE_EMPLOYEE),
        (department_shares.get(department_id, {}) if department_id else {}, SOURCE_DEPARTMENT),
    )
    for shares, source in levels:
        if sum(shares.values(), _ZERO) > _ZERO:
            return shares, source
    return {}, SOURCE_HOURS


def distribute_by_percent(
    total: Decimal, shares: dict[int, Decimal], main_company_id: int | None = None
) -> dict[int, Decimal]:
    """Распределяет total по компаниям пропорционально процентам так, чтобы сумма
    частей была РОВНО равна total. Вся логика округления — в `services.distribution`
    (один источник для экрана, Excel и фронта). Нормализует, если Σ% ≠ 100.
    """
    return distribute(total, shares, main_company_id)


def _auto_shares_by_hours(
    total: Decimal,
    breakdown,
    main_company,
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    """Авто-распределение, когда ручной % не задан: пропорционально фактическим
    часам сотрудника по компаниям (из табеля).

    Возвращает (shares %, amounts ₽). Доли в рублях — через общий `distribute`
    (сумма частей = total, остаток основной компании). Проценты — справочные
    (часы/всего × 100, до сотых).
    Если часов нет — вся сумма на основную компанию (default_company).
    """
    company_hours = {bd.company_id: bd.hours for bd in breakdown if bd.hours > _ZERO}
    total_hours = sum(company_hours.values(), _ZERO)

    if total_hours > _ZERO:
        amounts = distribute(
            total, company_hours, main_company.id if main_company else None
        )
        shares = {
            cid: (h / total_hours * _HUNDRED).quantize(Decimal("0.01"))
            for cid, h in company_hours.items()
        }
        return shares, amounts

    # Нет часов вообще → вся сумма на основную компанию.
    if main_company is not None:
        return {main_company.id: _HUNDRED}, {main_company.id: total}
    return {}, {}


# ── Сводная ведомость ─────────────────────────────────────────────────────────

def build_payroll_statement(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
    actor: Employee | None = None,
    department_id: int | None = None,
) -> PayrollStatementRead:
    summary = build_payroll_summary(
        db, employees, entries, year, month, actor, department_id
    )
    emp_by_id = {e.id: e for e in employees}
    emp_ids = [e.id for e in employees]
    # Позиция строки — из неё берутся отдел, основная компания и её проценты.
    position_by_id = {
        pos.id: pos for emp in employees for pos in emp.positions
    }
    primary_position_ids = {
        emp.id: (emp.primary_position.id if emp.primary_position else None)
        for emp in employees
    }

    companies = (
        db.query(Company).filter(Company.is_active == True)  # noqa: E712
        .order_by(Company.id).all()
    )
    company_refs = [
        StatementCompanyRef(id=c.id, code=c.code, name=c.name) for c in companies
    ]

    # Каскад приоритетов: месячный % > карточка (позиция) > отдел > авто по часам.
    employee_shares = load_employee_shares(db, emp_ids, primary_position_ids)
    override_shares = load_month_overrides(
        db, emp_ids, year, month, primary_position_ids
    )
    dept_shares = load_department_shares(
        db, [pos.department_id for pos in position_by_id.values() if pos.department_id]
    )

    rows: list[StatementRow] = []
    distribution_totals: dict[int, Decimal] = {c.id: _ZERO for c in companies}

    for p in summary.employees:
        emp = emp_by_id.get(p.employee_id)
        # Отдел, основная компания и проценты — у ПОЗИЦИИ строки, а не у человека.
        position = position_by_id.get(p.position_id)
        # «Начислено оклад» ведомости — оклад плюс обе повышенные категории
        # (вне графика и праздничные): отдельных колонок под них в форме
        # финдира нет, а в базу распределения по юрлицам они входить обязаны.
        base_salary = p.base_amount + p.off_schedule_amount + p.holiday_amount
        # Отпускные/больничные — часть «Итого начислено» и, значит, базы
        # распределения по юрлицам (само отсутствие к юрлицу не привязано).
        accrued = (
            base_salary + p.overtime_amount + p.vacation_amount + p.sick_amount
            + p.premium_amount + p.kpi_amount
        )
        main_company = position.company if position else None

        shares, source = resolve_shares(
            position_id=p.position_id,
            department_id=position.department_id if position else None,
            month_overrides=override_shares,
            employee_shares=employee_shares,
            department_shares=dept_shares,
        )
        is_overridden = source == SOURCE_MONTH
        is_auto = source == SOURCE_HOURS

        if not is_auto:
            dist_amounts = distribute_by_percent(
                accrued, shares, main_company.id if main_company else None
            )
        else:
            # Ни на одном уровне каскада % не задан → авто по фактическим часам.
            shares, dist_amounts = _auto_shares_by_hours(
                accrued, p.breakdown_by_company, main_company,
            )
        percent_sum = sum(shares.values(), _ZERO)

        distribution = [
            StatementCompanyAmount(
                company_id=cid,
                percent=shares[cid],
                amount=dist_amounts.get(cid, _ZERO),
            )
            for cid in sorted(shares.keys())
        ]
        for cid, amt in dist_amounts.items():
            distribution_totals[cid] = distribution_totals.get(cid, _ZERO) + amt

        overtime_coeff = getattr(position, "overtime_coefficient", None) if position else None
        overtime_coeff = Decimal("1.5") if overtime_coeff is None else Decimal(str(overtime_coeff))

        rows.append(StatementRow(
            employee_id=p.employee_id,
            position_id=p.position_id,
            is_primary_position=p.is_primary_position,
            tab_number=emp.tab_number if emp else None,
            employee_name=p.employee_name,
            main_company_id=main_company.id if main_company else None,
            main_company_name=main_company.name if main_company else None,
            department_name=(
                position.department.name if position and position.department else None
            ),
            # Должность — название рабочего места; у совместителя строки
            # различаются именно им.
            position=(position.display_title if position else (emp.position if emp else None)),
            schedule_name=p.schedule_name,
            rate=p.rate,
            pay_type=p.pay_type,
            shift_rate=p.shift_rate,
            hour_rate=p.hour_rate,
            worked_shifts=p.worked_shifts,
            norm_shifts=p.norm_shifts,
            base_shifts=p.base_shifts,
            norm_hours=p.norm_hours,
            fact_hours=p.total_hours,
            overtime_coefficient=overtime_coeff,
            overtime_hours=p.overtime_hours,
            overtime_amount=p.overtime_amount,
            base_salary=base_salary,
            premium_amount=p.premium_amount,
            kpi_amount=p.kpi_amount,
            premium_extra_amount=_ZERO,
            vacation_days=p.vacation_days,
            sick_days=p.sick_days,
            unpaid_days=p.unpaid_days,
            absent_days=p.absent_days,
            vacation_amount=p.vacation_amount,
            sick_amount=p.sick_amount,
            sick_limit_days=p.sick_limit_days,
            sick_unpaid_days=p.sick_unpaid_days,
            sick_limit_remaining=p.sick_limit_remaining,
            accrued_total=accrued,
            deductions=p.total_deductions,
            net_payout=p.net_payout,
            net_payout_exact=p.net_payout_exact,
            rounding_tail=p.rounding_tail,
            is_overridden=is_overridden,
            is_auto_distributed=is_auto,
            distribution_source=source,
            percent_sum=percent_sum,
            distribution=distribution,
            distribution_total=sum(dist_amounts.values(), _ZERO),
            is_calculable=p.is_calculable,
            note=p.reason_if_not_calculable,
        ))

    return PayrollStatementRead(
        year=year,
        month=month,
        companies=company_refs,
        rows=rows,
        total_overtime_amount=sum((r.overtime_amount for r in rows), _ZERO),
        total_base_salary=sum((r.base_salary for r in rows), _ZERO),
        total_vacation_amount=sum((r.vacation_amount for r in rows), _ZERO),
        total_sick_amount=sum((r.sick_amount for r in rows), _ZERO),
        total_premium=sum((r.premium_amount for r in rows), _ZERO),
        total_kpi=sum((r.kpi_amount for r in rows), _ZERO),
        total_accrued=sum((r.accrued_total for r in rows), _ZERO),
        total_deductions=sum((r.deductions for r in rows), _ZERO),
        total_net_payout=sum((r.net_payout for r in rows), _ZERO),
        total_net_payout_exact=sum((r.net_payout_exact for r in rows), _ZERO),
        total_rounding_tail=sum((r.rounding_tail for r in rows), _ZERO),
        distribution_totals=distribution_totals,
    )
