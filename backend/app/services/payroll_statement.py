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
from app.services.absences import get_month_absences, sick_days_used_before_month
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
from app.services.payroll import calculate_employee_payroll

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ── Payroll summary (единый источник для табеля и ведомости) ──────────────────

def build_payroll_summary(
    db: Session,
    employees: list[Employee],
    entries,
    year: int,
    month: int,
) -> PayrollSummaryRead:
    """Сводный расчёт ЗП по сотрудникам (премии/KPI/удержания/«к выплате»).

    Единый источник: используется и табелем (/payroll), и ведомостью (/statement).
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
    adjustment_sums = load_adjustment_sums(db, emp_ids, year, month)
    loan_overrides = load_loan_overrides(db, emp_ids)
    # Годовой лимит больничного: сколько оплачиваемых дней Б уже израсходовано
    # с 1 января до этого месяца (часть 2).
    sick_used_before = sick_days_used_before_month(
        db, emp_ids, year, month, calendar_data
    )

    payroll_items: list[EmployeePayrollRead] = []
    for emp in employees:
        emp_entries = entries_by_employee.get(emp.id, [])
        p = calculate_employee_payroll(
            emp, emp_entries, calendar_data, year, month, companies_by_id,
            absences=absences_by_employee.get(emp.id, []),
            sick_days_used_before=sick_used_before.get(emp.id, 0),
        )

        sums = adjustment_sums.get(emp.id, {})
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
            rate=p.rate,
            schedule_name=p.schedule_name,
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
            weekend_pay_type=emp.weekend_pay_type,
            weekend_coefficient=emp.weekend_coefficient,
            weekend_fixed_rate=emp.weekend_fixed_rate,
            holiday_pay_type=emp.holiday_pay_type,
            holiday_coefficient=emp.holiday_coefficient,
            holiday_fixed_rate=emp.holiday_fixed_rate,
            premium_amount=payout.premium_amount,
            kpi_amount=payout.kpi_amount,
            advance_deduction=payout.advance_deduction,
            loan_deduction=payout.loan_deduction,
            loan_remaining=loan_state.remaining_after if loan_state else _ZERO,
            loan_planned_deduction=loan_state.planned if loan_state else _ZERO,
            loan_is_manual=loan_state.is_manual if loan_state else False,
            total_deductions=payout.total_deductions,
            net_payout=payout.net_payout,
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
        total_net_payout=sum((p.net_payout for p in payroll_items), _ZERO),
    )


# ── Распределение по компаниям (проценты) ─────────────────────────────────────

# Загрузка наборов процентов — в app.services.company_shares (общая с роутерами).

# Откуда взято распределение (task_distribution_v2 ч.3) — видно в ведомости и Excel.
SOURCE_MONTH = "month"          # ручной % за конкретный месяц
SOURCE_EMPLOYEE = "employee"    # распределение в карточке сотрудника
SOURCE_DEPARTMENT = "department"  # дефолт отдела
SOURCE_HOURS = "hours"          # авто по фактическим часам табеля


def resolve_shares(
    employee_id: int,
    department_id: int | None,
    month_overrides: dict[int, dict[int, Decimal]],
    employee_shares: dict[int, dict[int, Decimal]],
    department_shares: dict[int, dict[int, Decimal]],
) -> tuple[dict[int, Decimal], str]:
    """Каскад приоритетов распределения: берётся ПЕРВОЕ заданное сверху вниз —
    месячный % > карточка сотрудника > дефолт отдела > авто по часам.

    Дефолт отдела применяется ТОЛЬКО к сотрудникам без своего распределения:
    правка дефолта отдела не трогает тех, у кого задана карточка или месяц.
    Возвращает (проценты, источник). Для авто — пустой набор.
    """
    levels = (
        (month_overrides.get(employee_id, {}), SOURCE_MONTH),
        (employee_shares.get(employee_id, {}), SOURCE_EMPLOYEE),
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
) -> PayrollStatementRead:
    summary = build_payroll_summary(db, employees, entries, year, month)
    emp_by_id = {e.id: e for e in employees}
    emp_ids = [e.id for e in employees]

    companies = (
        db.query(Company).filter(Company.is_active == True)  # noqa: E712
        .order_by(Company.id).all()
    )
    company_refs = [
        StatementCompanyRef(id=c.id, code=c.code, name=c.name) for c in companies
    ]

    # Каскад приоритетов: месячный % > карточка > отдел > авто по часам.
    employee_shares = load_employee_shares(db, emp_ids)
    override_shares = load_month_overrides(db, emp_ids, year, month)
    dept_shares = load_department_shares(
        db, [e.department_id for e in employees if e.department_id]
    )

    rows: list[StatementRow] = []
    distribution_totals: dict[int, Decimal] = {c.id: _ZERO for c in companies}

    for p in summary.employees:
        emp = emp_by_id.get(p.employee_id)
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
        main_company = emp.default_company if emp else None

        shares, source = resolve_shares(
            employee_id=p.employee_id,
            department_id=emp.department_id if emp else None,
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

        overtime_coeff = getattr(emp, "overtime_coefficient", None) if emp else None
        overtime_coeff = Decimal("1.5") if overtime_coeff is None else Decimal(str(overtime_coeff))

        rows.append(StatementRow(
            employee_id=p.employee_id,
            tab_number=emp.tab_number if emp else None,
            employee_name=p.employee_name,
            main_company_id=main_company.id if main_company else None,
            main_company_name=main_company.name if main_company else None,
            department_name=(emp.department.name if emp and emp.department else None),
            position=emp.position if emp else None,
            schedule_name=p.schedule_name,
            rate=p.rate,
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
        distribution_totals=distribution_totals,
    )
