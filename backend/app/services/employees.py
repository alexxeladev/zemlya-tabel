"""
Общий конструктор сотрудника из `EmployeeCreate`.

Вынесен из роутера, чтобы импорт из Excel создавал карточки той же логикой,
что и обычное создание (одни правила по взаимоисключающим полям и дефолтам),
а не собирал `Employee` по-своему. Доступ (email/роль/пароль) сюда НЕ входит —
им управляет роутер.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.employees import Employee
from app.models.positions import (
    PAY_TYPE_HOURLY,
    PAY_TYPE_PER_SHIFT,
    PAY_TYPE_SALARY,
)
from app.schemas.employee import EmployeeCreate


def build_employee(payload: EmployeeCreate) -> Employee:
    return Employee(
        tab_number=payload.tab_number,
        full_name=payload.full_name,
        position=payload.position,
        department_id=payload.department_id,
        schedule_id=payload.schedule_id,
        default_company_id=payload.default_company_id,
        pay_type=payload.pay_type,
        # Оклад, ставка за смену и ставка за час взаимоисключающие — чужие поля
        # не сохраняем, иначе в карточке останется мусор от прошлого типа оплаты
        # и расчёт молча возьмёт не ту базу.
        rate=payload.rate if payload.pay_type == PAY_TYPE_SALARY else None,
        shift_rate=payload.shift_rate if payload.pay_type == PAY_TYPE_PER_SHIFT else None,
        hour_rate=payload.hour_rate if payload.pay_type == PAY_TYPE_HOURLY else None,
        weekend_pay_type=payload.weekend_pay_type,
        # default 1.5 для coefficient, чтобы не хранить NULL при старом поведении
        weekend_coefficient=(
            payload.weekend_coefficient
            if payload.weekend_coefficient is not None or payload.weekend_pay_type != "coefficient"
            else Decimal("1.5")
        ),
        weekend_fixed_rate=payload.weekend_fixed_rate,
        holiday_pay_type=payload.holiday_pay_type,
        # default 1.5 для coefficient — как у выходных, точная ставка в карточке
        holiday_coefficient=(
            payload.holiday_coefficient
            if payload.holiday_coefficient is not None or payload.holiday_pay_type != "coefficient"
            else Decimal("1.5")
        ),
        holiday_fixed_rate=payload.holiday_fixed_rate,
        overtime_coefficient=(
            payload.overtime_coefficient
            if payload.overtime_coefficient is not None
            else Decimal("1.5")
        ),
        loan_amount=payload.loan_amount,
        loan_term_months=payload.loan_term_months,
        loan_start_date=payload.loan_start_date,
        is_active=payload.is_active,
        hire_date=payload.hire_date,
        dismissal_date=payload.dismissal_date,
    )


# ── Табельный номер: уникальность ─────────────────────────────────────────────
# `employees.tab_number` уникален в БД. Без проверки заранее занятый номер
# доходил до Postgres, IntegrityError не ловился и пользователь получал 500.
# Нумерация ОДНА на всю систему (общий справочник и вахта), поэтому и проверка
# одна — ей пользуются оба места создания сотрудника.


def normalize_tab_number(value: str | None) -> str | None:
    """Пустой номер — это «номера нет» (NULL), а не значение.

    Пустая строка уникальна так же, как любая другая: два сотрудника с «» упёрлись
    бы в unique, хотя номера нет ни у одного.
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


def tab_number_conflict(
    db: Session, tab_number: str | None, exclude_id: int | None = None,
) -> str | None:
    """Текст ошибки, если номер уже занят другим сотрудником (включая уволенных)."""
    if tab_number is None:
        return None
    query = db.query(Employee).filter(Employee.tab_number == tab_number)
    if exclude_id is not None:
        query = query.filter(Employee.id != exclude_id)
    holder = query.first()
    if holder is None:
        return None
    suffix = "" if holder.is_active else " (уволен)"
    return f"Табельный номер {tab_number} уже занят: {holder.full_name}{suffix}"
