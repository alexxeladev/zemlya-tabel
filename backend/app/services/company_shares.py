"""
Проценты распределения по юрлицам: загрузка наборов и общая валидация.

Наборы (task_distribution_v2 ч.3), в порядке каскада приоритетов:
  1. CompanyShareOverride     — ручной % за конкретный месяц (правка в ведомости)
  2. EmployeeCompanyShare     — распределение в карточке сотрудника
  3. DepartmentCompanyShare   — дефолт отдела
  4. (нет ни одного)          — авто по фактическим часам табеля
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.company_shares import (
    CompanyShareOverride,
    DepartmentCompanyShare,
    EmployeeCompanyShare,
)

_ZERO = Decimal("0")


def _as_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def load_employee_shares(db: Session, emp_ids: list[int]) -> dict[int, dict[int, Decimal]]:
    """{employee_id: {company_id: percent}} — проценты из карточек сотрудников."""
    result: dict[int, dict[int, Decimal]] = {}
    if not emp_ids:
        return result
    rows = (
        db.query(EmployeeCompanyShare)
        .filter(EmployeeCompanyShare.employee_id.in_(emp_ids))
        .all()
    )
    for r in rows:
        result.setdefault(r.employee_id, {})[r.company_id] = _as_decimal(r.percent)
    return result


def load_department_shares(db: Session, dept_ids: list[int]) -> dict[int, dict[int, Decimal]]:
    """{department_id: {company_id: percent}} — дефолт распределения отделов."""
    result: dict[int, dict[int, Decimal]] = {}
    ids = [d for d in dept_ids if d is not None]
    if not ids:
        return result
    rows = (
        db.query(DepartmentCompanyShare)
        .filter(DepartmentCompanyShare.department_id.in_(ids))
        .all()
    )
    for r in rows:
        result.setdefault(r.department_id, {})[r.company_id] = _as_decimal(r.percent)
    return result


def load_month_overrides(
    db: Session, emp_ids: list[int], year: int, month: int
) -> dict[int, dict[int, Decimal]]:
    """{employee_id: {company_id: percent}} — переопределения за конкретный месяц."""
    result: dict[int, dict[int, Decimal]] = {}
    if not emp_ids:
        return result
    rows = (
        db.query(CompanyShareOverride)
        .filter(
            CompanyShareOverride.employee_id.in_(emp_ids),
            CompanyShareOverride.year == year,
            CompanyShareOverride.month == month,
        )
        .all()
    )
    for r in rows:
        result.setdefault(r.employee_id, {})[r.company_id] = _as_decimal(r.percent)
    return result


class SharesValidationError(ValueError):
    """Некорректный набор процентов (роутер превращает в 422/404)."""

    def __init__(self, message: str, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


def validate_shares(db: Session, shares, require_100: bool = True) -> list:
    """Проверить набор «компания → процент» и вернуть только положительные доли.

    Одна проверка для карточки сотрудника и для дефолта отдела: без дублей, без
    отрицательных, компании существуют, сумма ≈100% (99–101 из-за округления).
    """
    total = _ZERO
    seen: set[int] = set()
    for s in shares:
        if s.percent < _ZERO:
            raise SharesValidationError("Процент не может быть отрицательным")
        if s.company_id in seen:
            raise SharesValidationError("Компания указана дважды")
        seen.add(s.company_id)
        if not db.get(Company, s.company_id):
            raise SharesValidationError("Company not found", not_found=True)
        total += s.percent

    positive = [s for s in shares if s.percent > _ZERO]
    if require_100 and positive and not (Decimal("99") <= total <= Decimal("101")):
        raise SharesValidationError(f"Сумма процентов должна быть ≈100% (сейчас {total})")
    return positive
