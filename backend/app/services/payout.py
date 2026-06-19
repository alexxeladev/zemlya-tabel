"""
Расчёт «к выплате» (задача 3.11a): займ, премии/KPI, аванс.

Раздел отдельной ведомости и распределение по % — НЕ здесь (задача Б).
Все деньги — Decimal, округление до целых рублей ROUND_HALF_EVEN.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy.orm import Session

from app.models.employee_adjustments import EmployeeAdjustment
from app.models.loan_deductions import LoanDeduction

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_ONE, rounding=ROUND_HALF_EVEN)


def _month_index(year: int, month: int) -> int:
    """Порядковый индекс месяца для сравнения/итерации."""
    return year * 12 + (month - 1)


@dataclass
class LoanMonth:
    planned: Decimal          # плановая доля за этот месяц (с учётом остатка)
    actual: Decimal           # фактически удержано (override или плановая доля)
    remaining_before: Decimal  # остаток до удержания этого месяца
    remaining_after: Decimal   # остаток после удержания этого месяца
    is_manual: bool            # сумма этого месяца скорректирована вручную
    active: bool               # займ ещё гасится в этом месяце


def loan_month_state(
    loan_amount: Decimal | None,
    term_months: int | None,
    start_date: date | None,
    target_year: int,
    target_month: int,
    overrides: dict[tuple[int, int], Decimal] | None = None,
) -> LoanMonth | None:
    """
    Состояние займа на конкретный месяц.

    Гасится равными долями `доля = round(сумма / срок)`. Каждый месяц от
    `start_date` удерживается доля, пока остаток не дойдёт до нуля. Последний
    месяц — удерживается остаток (если он меньше доли). Ручная правка за месяц
    (overrides) меняет только сумму этого месяца; остаток = сумма − фактически
    удержанное суммарно, поэтому при недоудержании займ гасится дольше.

    Возвращает None, если займа нет или target-месяц раньше старта.
    """
    if loan_amount is None or term_months is None or start_date is None:
        return None
    if loan_amount <= _ZERO or term_months <= 0:
        return None

    start_idx = _month_index(start_date.year, start_date.month)
    target_idx = _month_index(target_year, target_month)
    if target_idx < start_idx:
        return None

    overrides = overrides or {}
    share = _round(loan_amount / Decimal(term_months))
    if share <= _ZERO:
        share = loan_amount  # вырожденный случай: доля округлилась до 0

    remaining = loan_amount
    result: LoanMonth | None = None

    for idx in range(start_idx, target_idx + 1):
        y, m = divmod(idx, 12)
        m += 1
        remaining_before = remaining
        if remaining_before <= _ZERO:
            planned = _ZERO
            actual = _ZERO
            is_manual = False
            active = False
        else:
            planned = min(share, remaining_before)
            ov = overrides.get((y, m))
            if ov is not None:
                # нельзя удержать больше, чем осталось
                actual = min(_round(ov), remaining_before)
                is_manual = True
            else:
                actual = planned
                is_manual = False
            active = True
        remaining = remaining_before - actual
        if idx == target_idx:
            result = LoanMonth(
                planned=planned,
                actual=actual,
                remaining_before=remaining_before,
                remaining_after=remaining,
                is_manual=is_manual,
                active=active,
            )
    return result


@dataclass
class PayoutResult:
    premium_amount: Decimal
    kpi_amount: Decimal
    advance_deduction: Decimal
    loan_deduction: Decimal
    total_deductions: Decimal
    net_payout: Decimal


def compute_payout(
    accrued_total: Decimal,
    premium_amount: Decimal,
    kpi_amount: Decimal,
    advance_deduction: Decimal,
    loan_deduction: Decimal,
) -> PayoutResult:
    """
    К выплате = начислено (оклад+переработка+праздничные) + премии + KPI − удержано,
    где удержано = доля займа + аванс. Все слагаемые — целые рубли.
    """
    premium_amount = _round(premium_amount)
    kpi_amount = _round(kpi_amount)
    advance_deduction = _round(advance_deduction)
    loan_deduction = _round(loan_deduction)
    total_deductions = advance_deduction + loan_deduction
    net = accrued_total + premium_amount + kpi_amount - total_deductions
    return PayoutResult(
        premium_amount=premium_amount,
        kpi_amount=kpi_amount,
        advance_deduction=advance_deduction,
        loan_deduction=loan_deduction,
        total_deductions=total_deductions,
        net_payout=net,
    )


# ── DB-aware helpers ───────────────────────────────────────────────────────────

def load_adjustment_sums(
    db: Session, emp_ids: list[int], year: int, month: int
) -> dict[int, dict[str, Decimal]]:
    """{employee_id: {"premium": Σ, "kpi": Σ, "advance": Σ}} за период."""
    result: dict[int, dict[str, Decimal]] = {}
    if not emp_ids:
        return result
    rows = (
        db.query(EmployeeAdjustment)
        .filter(
            EmployeeAdjustment.employee_id.in_(emp_ids),
            EmployeeAdjustment.year == year,
            EmployeeAdjustment.month == month,
        )
        .all()
    )
    for r in rows:
        bucket = result.setdefault(
            r.employee_id, {"premium": _ZERO, "kpi": _ZERO, "advance": _ZERO}
        )
        amount = r.amount if isinstance(r.amount, Decimal) else Decimal(str(r.amount))
        bucket[r.kind] = bucket.get(r.kind, _ZERO) + amount
    return result


def load_loan_overrides(
    db: Session, emp_ids: list[int]
) -> dict[int, dict[tuple[int, int], Decimal]]:
    """{employee_id: {(year, month): actual_amount}} — все ручные правки займа."""
    result: dict[int, dict[tuple[int, int], Decimal]] = {}
    if not emp_ids:
        return result
    rows = db.query(LoanDeduction).filter(LoanDeduction.employee_id.in_(emp_ids)).all()
    for r in rows:
        amount = (
            r.actual_amount
            if isinstance(r.actual_amount, Decimal)
            else Decimal(str(r.actual_amount))
        )
        result.setdefault(r.employee_id, {})[(r.year, r.month)] = amount
    return result
