"""Tests for task 3.4: payroll calculation service and endpoints."""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.payroll import calculate_employee_payroll
from tests.conftest import get_token

# ── Test calendars ─────────────────────────────────────────────────────────────

# May 2026 simplified:
# Non-working days: 3,4,10,11,17,18,24,25,31 (9 regular weekends, May 1 is workday here)
# Workdays: 22, norm for 8h/shift = 176h, no short days
MAY_BASIC = {
    "year": 2026,
    "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}],
}

# May with May 1 as holiday and May 8 as short day:
# Non-working: 1,3,4,10,11,17,18,24,25,31 (10 days)
# Short days: 8*
# Workdays: 21, short=1, norm for 8h/shift = 21*8 - 1 = 167h
MAY_WITH_HOLIDAY = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,3,4,8*,10,11,17,18,24,25,31"}],
}

# Реальные дни недели мая 2026 (важно с task_schedule_based_pay: оплата часов
# определяется ГРАФИКОМ, а рабочие дни графика 5/2 — это Пн–Пт):
#   субботы: 2, 9, 16, 23, 30 | воскресенья: 3, 10, 17, 24, 31 | 1 мая — пятница.
# В синтетических календарях выше нерабочими помечены Вс+Пн, а субботы —
# рабочими (как «перенос»), поэтому для 5/2 выходными по графику там оказываются
# только воскресенья. Тесты «выхода в свой выходной» используют воскресенья.
MAY_SUNDAYS = (3, 10, 17, 24, 31)

# Календарь с «настоящими» выходными: Сб+Вс нерабочие, 1 мая (пятница) — праздник.
# Нерабочих 11 → рабочих 20 → норма для 8ч = 160.
MAY_REAL = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,2,3,9,10,16,17,23,24,30,31"}],
}

# Workdays in MAY_BASIC: all days NOT in the calendar's non-working set
# The calendar defines non-working = {3,4,10,11,17,18,24,25,31}, workdays = everything else
# norm_hours_for_period uses workdays_in_month which counts via the calendar, not weekday filter
MAY_BASIC_WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]
# = 22 days → norm for 8h/shift = 176h


# ── Unit test helpers ──────────────────────────────────────────────────────────

def make_employee(
    emp_id: int = 1,
    rate: Decimal | None = Decimal("80000"),
    schedule: Schedule | None = None,
) -> Employee:
    emp = Employee(full_name="Test Employee", rate=rate, is_active=True)
    emp.id = emp_id
    emp.schedule = schedule
    return emp


def make_schedule(hours_per_shift: int = 8, schedule_type: str = "standard") -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=hours_per_shift, schedule_type=schedule_type)
    s.id = 1
    return s


def make_entry(
    company_id: int = 1,
    work_date: date = date(2026, 5, 2),
    hours: Decimal = Decimal("8"),
    employee_id: int = 1,
) -> TimesheetEntry:
    e = TimesheetEntry(employee_id=employee_id, company_id=company_id,
                       work_date=work_date, hours=hours)
    return e


# ── Unit tests: calculate_employee_payroll ────────────────────────────────────

class TestFullNorm:
    def test_exact_norm_gives_full_salary(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS]
        assert sum(e.hours for e in entries) == Decimal("176")

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.is_calculable is True
        assert p.total_hours == Decimal("176")
        assert p.norm_hours == Decimal("176")
        assert p.delta_hours == Decimal("0")
        assert p.overtime_hours == Decimal("0")
        assert p.base_amount == Decimal("80000")
        assert p.overtime_amount == Decimal("0")
        assert p.off_schedule_amount == Decimal("0")
        assert p.total_amount == Decimal("80000")


class TestUnderNorm:
    def test_proportional_salary(self):
        """total=140, norm=176 → base = 80000*140/176 rounded HALF_EVEN"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # Create 17 days * 8h + 1 day * 4h = 140h
        days = MAY_BASIC_WORKDAYS[:17]
        entries = [make_entry(work_date=date(2026, 5, d)) for d in days]
        entries.append(make_entry(work_date=date(2026, 5, MAY_BASIC_WORKDAYS[17]), hours=Decimal("4")))
        assert sum(e.hours for e in entries) == Decimal("140")

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        expected = (Decimal("80000") * Decimal("140") / Decimal("176")).quantize(Decimal("1"))
        assert p.base_amount == expected
        assert p.overtime_amount == Decimal("0")
        assert p.total_amount == expected


class TestOvertime:
    def test_overtime_adds_to_full_salary(self):
        """total=180, norm=176 → base=80000, overtime for 4 extra hours"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # 22 workdays × 8h = 176 norm; add 4h extra via second company on one day
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS]
        entries.append(make_entry(work_date=date(2026, 5, 1), hours=Decimal("4"), company_id=2))

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        hourly = Decimal("80000") / Decimal("176")
        expected_overtime = (Decimal("4") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.is_calculable is True
        assert p.base_amount == Decimal("80000")
        assert p.overtime_hours == Decimal("4")
        assert p.overtime_amount == expected_overtime
        assert p.total_amount == Decimal("80000") + expected_overtime


class TestDailyOvertime:
    """
    task_overtime_daily: переработка считается ПО ДНЯМ —
    для каждого дня max(0, факт − дневная норма смены), сумма за месяц.
    """

    def test_single_day_overtime(self):
        """10ч в один день при дневной норме 8 → переработка 2ч."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, 2), hours=Decimal("10"))]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.overtime_hours == Decimal("2")
        # зачётных будних часов 8 (10 − 2 переработки) из нормы 176
        expected_base = (Decimal("80000") * Decimal("8") / Decimal("176")).quantize(Decimal("1"))
        assert p.base_amount == expected_base

    def test_undertime_does_not_offset_overtime(self):
        """Пн 10ч (+2) и Вт 6ч (0) при норме 8 → переработка 2ч, а не 0 (AC 1)."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [
            make_entry(work_date=date(2026, 5, 2), hours=Decimal("10")),
            make_entry(work_date=date(2026, 5, 5), hours=Decimal("6")),
        ]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.total_hours == Decimal("16")
        assert p.overtime_hours == Decimal("2")
        # зачётные будние = 8 + 6 = 14
        expected_base = (Decimal("80000") * Decimal("14") / Decimal("176")).quantize(Decimal("1"))
        assert p.base_amount == expected_base

    def test_multicompany_hours_in_day_are_summed(self):
        """8ч компания А + 4ч компания Б в один день (норма 8) → переработка 4ч (AC 2)."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 2), hours=Decimal("8")),
            make_entry(company_id=2, work_date=date(2026, 5, 2), hours=Decimal("4")),
        ]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.overtime_hours == Decimal("4")

    def test_short_day_norm_is_shift_minus_one(self):
        """Сокращённый день: норма 7ч, отработано 8ч → переработка 1ч."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # 8 мая в MAY_WITH_HOLIDAY — сокращённый день
        entries = [make_entry(work_date=date(2026, 5, 8), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.overtime_hours == Decimal("1")

    def test_daily_overtime_summed_across_days(self):
        """Переработки разных дней складываются: +2, +3, 0 → 5ч."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [
            make_entry(work_date=date(2026, 5, 2), hours=Decimal("10")),   # +2
            make_entry(work_date=date(2026, 5, 5), hours=Decimal("11")),   # +3
            make_entry(work_date=date(2026, 5, 6), hours=Decimal("8")),    # 0
            make_entry(work_date=date(2026, 5, 7), hours=Decimal("3")),    # 0 (недоработка)
        ]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.overtime_hours == Decimal("5")

    def test_overtime_above_norm(self):
        """Факт 180 будних часов при норме 176, 12ч в один день → переработка 4ч."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # 22 дня × 8ч = 176, плюс ещё один день со сверхнормой
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS]
        entries.append(make_entry(work_date=date(2026, 5, 2), hours=Decimal("4"), company_id=2))
        assert sum(e.hours for e in entries) == Decimal("180")

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.overtime_hours == Decimal("4")
        assert p.base_amount == Decimal("80000")
        hourly = Decimal("80000") / Decimal("176")
        expected_ot = (Decimal("4") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.overtime_amount == expected_ot

    def test_undertime_never_negative(self):
        """Недоработка в день не уходит в минус (AC 4)."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [
            make_entry(work_date=date(2026, 5, 2), hours=Decimal("4")),
            make_entry(work_date=date(2026, 5, 5), hours=Decimal("6")),
        ]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.overtime_hours == Decimal("0")
        assert p.total_hours == Decimal("10")

    def test_exact_norm_no_overtime(self):
        """Ровно норма → переработки нет, полный оклад."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        assert p.overtime_hours == Decimal("0")
        assert p.base_amount == Decimal("80000")

    def test_overtime_coefficient_from_employee(self):
        """Коэффициент переработки берётся из карточки сотрудника (0/1/1.5)."""
        schedule = make_schedule(8)
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS]
        entries.append(make_entry(work_date=date(2026, 5, 2), hours=Decimal("4"), company_id=2))
        hourly = Decimal("80000") / Decimal("176")

        # коэффициент 1.0
        emp1 = make_employee(schedule=schedule)
        emp1.overtime_coefficient = Decimal("1")
        p1 = calculate_employee_payroll(emp1, entries, MAY_BASIC, 2026, 5)
        assert p1.overtime_amount == (Decimal("4") * hourly * Decimal("1")).quantize(Decimal("1"))

        # коэффициент 0 → переработка не оплачивается
        emp0 = make_employee(schedule=schedule)
        emp0.overtime_coefficient = Decimal("0")
        p0 = calculate_employee_payroll(emp0, entries, MAY_BASIC, 2026, 5)
        assert p0.overtime_hours == Decimal("4")
        assert p0.overtime_amount == Decimal("0")

    def test_off_schedule_hours_excluded_from_overtime(self):
        """Часы вне графика не попадают в переработку (отдельная категория)."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # Рабочие дни MAY_WITH_HOLIDAY (без 1 мая — праздник): заполняем ровно по норме.
        # 8ч в каждый, кроме сокращённого 8 мая (7ч) → 20*8 + 7 = 167 = норма.
        workdays = [d for d in range(1, 32) if d not in (1, 3, 4, 10, 11, 17, 18, 24, 25, 31)]
        entries = [
            make_entry(work_date=date(2026, 5, d), hours=Decimal("7" if d == 8 else "8"))
            for d in workdays
        ]
        # 17 мая — воскресенье, выходной по графику 5/2; 12 часов сверху
        # идут в категорию «вне графика» и переработки не дают.
        entries.append(make_entry(work_date=date(2026, 5, 17), hours=Decimal("12")))

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.norm_hours == Decimal("167")
        assert p.off_schedule_hours == Decimal("12")
        # часы рабочих дней графика = 167 = норма → переработки нет
        assert p.overtime_hours == Decimal("0")
        assert p.base_amount == Decimal("80000")


class TestNormFactDays:
    """Правка 3.9-4: справочные колонки норма/факт дней."""

    def test_norm_days_counts_workdays(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        p = calculate_employee_payroll(emp, [], MAY_BASIC, 2026, 5)
        # MAY_BASIC: 22 рабочих дня
        assert p.norm_days == 22
        assert p.fact_days == 0

    def test_fact_days_counts_distinct_days_with_hours(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # 3 разных дня, на один из них две компании — это всё равно 1 день
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 5), hours=Decimal("4")),
            make_entry(company_id=2, work_date=date(2026, 5, 5), hours=Decimal("4")),
            make_entry(company_id=1, work_date=date(2026, 5, 6), hours=Decimal("8")),
            make_entry(company_id=1, work_date=date(2026, 5, 7), hours=Decimal("8")),
        ]
        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)
        assert p.fact_days == 3

    def test_norm_days_none_without_calendar(self):
        emp = make_employee(schedule=make_schedule(8))
        p = calculate_employee_payroll(emp, [], calendar_data=None, year=2026, month=5)
        assert p.norm_days is None


class TestOffScheduleHours:
    """Часы ВНЕ ГРАФИКА (task_schedule_based_pay) — бывшие «праздничные»."""

    def test_off_schedule_hours_get_extra_pay(self):
        """8ч в воскресенье (выходной по графику 5/2) → 8 × hourly × 1.5.

        База оклада эти часы НЕ включает (правка 3.9-2), поэтому выход вне
        графика оплачивается полным коэффициентом (по умолчанию 1.5),
        а не доплатой 0.5 как раньше.
        """
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # norm in MAY_WITH_HOLIDAY = 167h; 8ч в воскресенье 17 мая
        entries = [make_entry(work_date=date(2026, 5, 17), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.is_calculable is True
        assert p.off_schedule_hours == Decimal("8")
        # Часы вне графика не идут в зачёт оклада
        assert p.base_amount == Decimal("0")
        hourly = Decimal("80000") / Decimal("167")
        expected_holiday = (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.off_schedule_amount == expected_holiday

    def test_calendar_holiday_is_holiday_not_off_schedule(self):
        """
        1 мая (праздник, пятница) — отдельная категория «праздничные», а не
        «вне графика»: приоритет праздника выше выходного по графику.
        """
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.holiday_hours == Decimal("8")
        assert p.off_schedule_hours == Decimal("0")
        assert p.off_schedule_amount == Decimal("0")
        assert p.overtime_hours == Decimal("0")
        # Праздничные часы в зачёт оклада не идут — оплачиваются отдельно ×2.
        assert p.base_amount == Decimal("0")

    def test_short_day_has_no_holiday_extra(self):
        """May 8 is a short day (not holiday) → no holiday_amount"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, 8), hours=Decimal("7"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.off_schedule_hours == Decimal("0")
        assert p.off_schedule_amount == Decimal("0")


class TestWeekendPay:
    """Правка 3.9-3: оплата выхода вне графика per-employee.

    С task_schedule_based_pay триггер — выходной по ГРАФИКУ сотрудника
    (17 мая 2026 — воскресенье), а не праздник по календарю.
    """

    def _holiday_emp(self, pay_type="coefficient", coeff=None, fixed=None):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        emp.weekend_pay_type = pay_type
        emp.weekend_coefficient = coeff
        emp.weekend_fixed_rate = fixed
        return emp

    def test_coefficient_1_5(self):
        emp = self._holiday_emp("coefficient", coeff=Decimal("1.5"))
        entries = [make_entry(work_date=date(2026, 5, 17), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        hourly = Decimal("80000") / Decimal("167")
        expected = (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.off_schedule_amount == expected

    def test_coefficient_zero_not_paid(self):
        emp = self._holiday_emp("coefficient", coeff=Decimal("0"))
        entries = [make_entry(work_date=date(2026, 5, 17), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        # часы видны, но доплаты нет
        assert p.off_schedule_hours == Decimal("8")
        assert p.off_schedule_amount == Decimal("0")

    def test_fixed_rate_740(self):
        """Фикс-ставка 740 ₽/ч, 8ч в свой выходной по графику → 5920 ₽."""
        emp = self._holiday_emp("fixed_rate", fixed=Decimal("740"))
        entries = [make_entry(work_date=date(2026, 5, 17), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        assert p.off_schedule_amount == Decimal("5920")

    def test_default_coefficient_when_unset(self):
        """Поля не заданы (None) → коэффициент по умолчанию 1.5."""
        emp = make_employee(schedule=make_schedule(8))  # без weekend_* атрибутов = None
        entries = [make_entry(work_date=date(2026, 5, 17), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        hourly = Decimal("80000") / Decimal("167")
        expected = (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.off_schedule_amount == expected


class TestScheduleBasedPay:
    """
    task_schedule_based_pay: точка отсчёта оплаты — ГРАФИК сотрудника.
    Календарь с настоящими выходными (MAY_REAL): 1 мая — праздник-пятница,
    2 мая — суббота, норма 8ч-графика = 160.
    """

    def _emp(self, pay_type="coefficient", coeff=None, fixed=None):
        emp = make_employee(schedule=make_schedule(8))
        emp.weekend_pay_type = pay_type
        emp.weekend_coefficient = coeff
        emp.weekend_fixed_rate = fixed
        return emp

    def test_holiday_on_workday_is_paid_as_holiday(self):
        """
        5/2 работал в праздник, попавший на будний день. Раньше такие часы шли
        по окладу и при полном месяце пропадали совсем; теперь это отдельная
        категория «праздничные» (дефолт ×2).
        """
        emp = self._emp()
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.norm_hours == Decimal("160")
        assert p.holiday_hours == Decimal("8")
        assert p.off_schedule_hours == Decimal("0")
        assert p.base_amount == Decimal("0")
        hourly = Decimal("80000") / Decimal("160")
        assert p.holiday_amount == (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))

    def test_saturday_is_off_schedule(self):
        """AC2: 5/2 вышел в субботу (свой выходной) → часы ×1.5."""
        emp = self._emp()
        entries = [make_entry(work_date=date(2026, 5, 2), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.off_schedule_hours == Decimal("8")
        assert p.base_amount == Decimal("0")
        hourly = Decimal("80000") / Decimal("160")
        assert p.off_schedule_amount == (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))

    def test_fixed_rate_on_own_day_off(self):
        """AC5: электрик с фикс-ставкой 740 ₽/ч вышел в воскресенье → 8 × 740."""
        emp = self._emp("fixed_rate", fixed=Decimal("740"))
        entries = [make_entry(work_date=date(2026, 5, 3), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.off_schedule_hours == Decimal("8")
        assert p.off_schedule_amount == Decimal("5920")

    def test_overtime_on_schedule_workday_stays_overtime(self):
        """AC6: 10ч в рабочий день графика → 8ч в оклад, 2ч переработки."""
        emp = self._emp()
        entries = [make_entry(work_date=date(2026, 5, 5), hours=Decimal("10"))]

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.overtime_hours == Decimal("2")
        assert p.off_schedule_hours == Decimal("0")
        hourly = Decimal("80000") / Decimal("160")
        assert p.overtime_amount == (Decimal("2") * hourly * Decimal("1.5")).quantize(Decimal("1"))

    def test_holiday_hours_are_not_overtime(self):
        """
        12ч в праздник — все 12 праздничные, переработки нет: у праздничного
        дня нет «смены», сверх которой считать превышение.
        """
        emp = self._emp()
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("12"))]

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.holiday_hours == Decimal("12")
        assert p.off_schedule_hours == Decimal("0")
        assert p.overtime_hours == Decimal("0")

    def test_six_day_schedule_works_saturday_by_salary(self):
        """График 6/1: суббота — рабочий день графика → оклад, не ×1.5."""
        emp = self._emp()
        emp.schedule = Schedule(name="6/1", hours_per_shift=8, schedule_type="standard")
        entries = [make_entry(work_date=date(2026, 5, 2), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.off_schedule_hours == Decimal("0")
        assert p.base_amount > Decimal("0")

    def test_off_schedule_hours_not_in_base(self):
        """Полная норма + выход в воскресенье: оклад 100% + отдельная оплата."""
        emp = self._emp()
        workdays = [d for d in range(1, 32) if d not in (1, 2, 3, 9, 10, 16, 17, 23, 24, 30, 31)]
        entries = [make_entry(work_date=date(2026, 5, d)) for d in workdays]
        assert sum(e.hours for e in entries) == Decimal("160")
        entries.append(make_entry(work_date=date(2026, 5, 10), hours=Decimal("8")))

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.base_amount == Decimal("80000")
        assert p.overtime_hours == Decimal("0")
        assert p.off_schedule_hours == Decimal("8")
        hourly = Decimal("80000") / Decimal("160")
        expected = (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.off_schedule_amount == expected
        assert p.total_amount == Decimal("80000") + expected


class TestNotCalculable:
    def test_no_rate(self):
        emp = make_employee(rate=None, schedule=make_schedule())
        p = calculate_employee_payroll(emp, [make_entry()], MAY_BASIC, 2026, 5)
        assert p.is_calculable is False
        assert "оклад" in (p.reason_if_not_calculable or "").lower()
        assert p.base_amount == Decimal("0")
        assert p.total_amount == Decimal("0")

    def test_zero_rate(self):
        emp = make_employee(rate=Decimal("0"), schedule=make_schedule())
        p = calculate_employee_payroll(emp, [make_entry()], MAY_BASIC, 2026, 5)
        assert p.is_calculable is False

    def test_no_schedule(self):
        emp = make_employee(schedule=None)
        p = calculate_employee_payroll(emp, [make_entry()], MAY_BASIC, 2026, 5)
        assert p.is_calculable is False
        assert "график" in (p.reason_if_not_calculable or "").lower()
        assert p.norm_hours is None

    def test_shift_schedule_without_cycle_anchor(self):
        """
        task_shift_schedules: сменный график считается, но только с анкером
        цикла — без стартовой даты фазу определить нельзя (см. test_shift_schedules).
        """
        emp = make_employee(schedule=make_schedule(12, "shift"))
        p = calculate_employee_payroll(emp, [make_entry()], MAY_BASIC, 2026, 5)
        assert p.is_calculable is False
        assert "цикл" in (p.reason_if_not_calculable or "").lower()

    def test_no_calendar(self):
        emp = make_employee(schedule=make_schedule())
        p = calculate_employee_payroll(emp, [make_entry()], calendar_data=None, year=2026, month=5)
        assert p.is_calculable is False
        assert p.norm_hours is None

    def test_no_rate_still_shows_hours(self):
        emp = make_employee(rate=None, schedule=make_schedule())
        entries = [make_entry(hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)
        assert p.is_calculable is False
        assert p.total_hours == Decimal("8")


class TestCompanyBreakdown:
    def test_two_companies_equal_split(self):
        """4h/company A + 4h/company B every day → each gets 50% of salary"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = []
        for d in MAY_BASIC_WORKDAYS:
            entries.append(make_entry(company_id=1, work_date=date(2026, 5, d), hours=Decimal("4")))
            entries.append(make_entry(company_id=2, work_date=date(2026, 5, d), hours=Decimal("4")))

        companies = {1: ("CA", "Company A"), 2: ("CB", "Company B")}
        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5, companies)

        assert p.total_hours == Decimal("176")
        assert len(p.breakdown_by_company) == 2
        bd_a = next(b for b in p.breakdown_by_company if b.company_id == 1)
        bd_b = next(b for b in p.breakdown_by_company if b.company_id == 2)
        assert bd_a.base_amount == Decimal("40000")
        assert bd_b.base_amount == Decimal("40000")
        # Правка 3.9-5: процент по компаниям
        assert bd_a.percent == Decimal("50.0")
        assert bd_b.percent == Decimal("50.0")
        assert bd_a.percent + bd_b.percent == Decimal("100.0")

    def test_no_entries_empty_breakdown(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        p = calculate_employee_payroll(emp, [], MAY_BASIC, 2026, 5)
        assert p.total_hours == Decimal("0")
        assert p.base_amount == Decimal("0")
        assert p.breakdown_by_company == []

    def test_holiday_distributed_by_company_holiday_hours(self):
        """Все часы вне графика в компании A → ей и вся оплата вне графика."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # 17 мая — воскресенье (выходной по графику 5/2): 8ч на компанию A;
        # 2 мая — рабочий день графика: 8ч на компанию B.
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 17), hours=Decimal("8")),
            make_entry(company_id=2, work_date=date(2026, 5, 2), hours=Decimal("8")),
        ]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        assert p.off_schedule_hours == Decimal("8")
        bd_a = next(b for b in p.breakdown_by_company if b.company_id == 1)
        bd_b = next(b for b in p.breakdown_by_company if b.company_id == 2)
        assert bd_a.off_schedule_amount == p.off_schedule_amount
        assert bd_b.off_schedule_amount == Decimal("0")

    def test_breakdown_sums_equal_total_uneven_split(self):
        """Доли 1/3 не делятся нацело — сумма частей всё равно обязана сходиться с итогом."""
        schedule = make_schedule(8)
        emp = make_employee(rate=Decimal("10000"), schedule=schedule)
        # 8h в трёх разных днях на три компании — каждой по 1/3 часов
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 2), hours=Decimal("8")),
            make_entry(company_id=2, work_date=date(2026, 5, 5), hours=Decimal("8")),
            make_entry(company_id=3, work_date=date(2026, 5, 6), hours=Decimal("8")),
        ]
        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        # base = 10000 × 24/176 = 1363.64 → 1364; деление на 3 даёт 454.67 на компанию.
        # Независимое округление дало бы 455×3 = 1365 ≠ 1364.
        assert p.base_amount == Decimal("1364")
        assert sum(b.base_amount for b in p.breakdown_by_company) == p.base_amount
        assert sum(b.total for b in p.breakdown_by_company) == p.total_amount
        parts = sorted(b.base_amount for b in p.breakdown_by_company)
        assert parts == [Decimal("454"), Decimal("455"), Decimal("455")]

    def test_breakdown_sums_for_all_categories(self):
        """base/overtime/holiday: каждая категория по компаниям сходится со своим итогом."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)  # rate 80000, норма 167 (MAY_WITH_HOLIDAY)
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 2), hours=Decimal("6")),
            # вместе с 6h по c1 это 10h за день → 2h переработки
            make_entry(company_id=2, work_date=date(2026, 5, 2), hours=Decimal("4")),
            make_entry(company_id=1, work_date=date(2026, 5, 5), hours=Decimal("5")),
            # 1 мая — праздник, но пятница = рабочий день графика → идёт в оклад
            make_entry(company_id=1, work_date=date(2026, 5, 1), hours=Decimal("5")),
            make_entry(company_id=2, work_date=date(2026, 5, 1), hours=Decimal("3")),
            # 3 мая — воскресенье, выходной по графику → «вне графика»
            make_entry(company_id=3, work_date=date(2026, 5, 3), hours=Decimal("7")),
        ]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        # по дням: 2 мая 6+4=10ч при дневной норме 8 → 2ч переработки;
        # 1 мая 5+3=8ч ровно по норме смены → переработки нет
        assert p.overtime_hours == Decimal("2")
        assert p.off_schedule_hours == Decimal("7")
        bd = p.breakdown_by_company
        assert len(bd) == 3
        assert sum(b.base_amount for b in bd) == p.base_amount
        assert sum(b.overtime_amount for b in bd) == p.overtime_amount
        assert sum(b.off_schedule_amount for b in bd) == p.off_schedule_amount
        assert sum(b.total for b in bd) == p.total_amount
        for b in bd:
            assert b.base_amount >= Decimal("0")
            assert b.overtime_amount >= Decimal("0")
            assert b.off_schedule_amount >= Decimal("0")

    def test_breakdown_hours_sum_to_totals(self):
        """Часы переработки/вне графика по компаниям сходятся с итоговыми часами."""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 2), hours=Decimal("6")),
            make_entry(company_id=2, work_date=date(2026, 5, 2), hours=Decimal("4")),  # 10h → 2h overtime
            # 3 мая — воскресенье: часы вне графика, привязаны к своим компаниям
            make_entry(company_id=1, work_date=date(2026, 5, 3), hours=Decimal("5")),
            make_entry(company_id=2, work_date=date(2026, 5, 3), hours=Decimal("3")),
        ]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        bd = p.breakdown_by_company
        assert sum(b.overtime_hours for b in bd) == p.overtime_hours
        assert sum(b.off_schedule_hours for b in bd) == p.off_schedule_hours
        # часы компаний сходятся с total_hours
        assert sum(b.hours for b in bd) == p.total_hours
        # часы вне графика привязаны к компании, где они отработаны
        bd_a = next(b for b in bd if b.company_id == 1)
        bd_b = next(b for b in bd if b.company_id == 2)
        assert bd_a.off_schedule_hours == Decimal("5")
        assert bd_b.off_schedule_hours == Decimal("3")


class TestRounding:
    def test_all_amounts_are_whole_rubles(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS[:5]]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        for val in [p.base_amount, p.overtime_amount, p.off_schedule_amount, p.total_amount]:
            assert val == val.quantize(Decimal("1")), f"{val} is not whole ruble"

    def test_round_half_even_not_half_up(self):
        """ROUND_HALF_EVEN: 2.5 → 2 (not 3 as ROUND_HALF_UP would give)"""
        # rate=5, hours_per_shift=2, 1 workday → norm=2, total=1 → base=5*1/2=2.5 → 2
        all_non_working = ",".join(str(d) for d in range(1, 32) if d != 2)
        single_day_cal = {"year": 2026, "months": [{"month": 5, "days": all_non_working}]}
        emp = make_employee(rate=Decimal("5"), schedule=make_schedule(hours_per_shift=2))
        entries = [make_entry(work_date=date(2026, 5, 2), hours=Decimal("1"))]

        p = calculate_employee_payroll(emp, entries, single_day_cal, 2026, 5)

        assert p.norm_hours == Decimal("2")
        assert p.base_amount == Decimal("2")  # ROUND_HALF_EVEN: 2.5 → 2


# ── Integration test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Test Dept", code="PD", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="PC", name="Payroll Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def standard_schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2-pay", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def admin_pay(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Pay Admin",
        email="payadmin@example.com",
        hashed_password=hash_password("admin123"),
        role="admin",
        is_active=True,
        must_change_password=False,
        is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def accountant_pay(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Pay Accountant",
        email="payacct@example.com",
        hashed_password=hash_password("acct123"),
        role="accountant",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def manager_pay(db_session: Session, dept: Department) -> Employee:
    emp = Employee(
        full_name="Pay Manager",
        email="paymgr@example.com",
        hashed_password=hash_password("mgr123"),
        role="manager",
        is_active=True,
        must_change_password=False,
        department_id=dept.id,
        managed_departments=[dept],
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session: Session, company: Company, standard_schedule: Schedule, dept: Department) -> Employee:
    emp = Employee(
        full_name="Pay Worker",
        is_active=True,
        rate=Decimal("80000"),
        schedule_id=standard_schedule.id,
        default_company_id=company.id,
        department_id=dept.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker_no_rate(db_session: Session, company: Company, standard_schedule: Schedule) -> Employee:
    emp = Employee(
        full_name="Pay Worker No Rate",
        is_active=True,
        rate=None,
        schedule_id=standard_schedule.id,
        default_company_id=company.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_BASIC, source="manual")
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


def _add_entries(
    db: Session,
    employee_id: int,
    company_id: int,
    days_hours: list[tuple[int, str]],
) -> None:
    for day, h in days_hours:
        db.add(TimesheetEntry(
            employee_id=employee_id,
            work_date=date(2026, 5, day),
            company_id=company_id,
            hours=int(h),
        ))
    db.commit()


# ── Integration tests ─────────────────────────────────────────────────────────

class TestPayrollEndpointAccess:
    def test_admin_can_get_payroll(self, client: TestClient, admin_pay: Employee,
                                    calendar_2026: ProductionCalendar):
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["year"] == 2026
        assert data["month"] == 5
        assert "employees" in data

    def test_accountant_can_get_payroll(self, client: TestClient, accountant_pay: Employee,
                                         calendar_2026: ProductionCalendar):
        token = get_token(client, "payacct@example.com", "acct123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_manager_can_get_own_department(self, client: TestClient, manager_pay: Employee,
                                             worker: Employee, calendar_2026: ProductionCalendar):
        token = get_token(client, "paymgr@example.com", "mgr123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        # видит только сотрудников своего отдела
        emp_ids = {e["employee_id"] for e in data["employees"]}
        assert worker.id in emp_ids

    def test_manager_forbidden_foreign_department(self, client: TestClient, manager_pay: Employee,
                                                  calendar_2026: ProductionCalendar):
        token = get_token(client, "paymgr@example.com", "mgr123")
        resp = client.get(f"/api/timesheet/2026/5/payroll?department_id={manager_pay.department_id + 999}",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_include_payroll_true_for_admin(self, client: TestClient, admin_pay: Employee,
                                             calendar_2026: ProductionCalendar):
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5?include_payroll=true",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["payroll"] is not None
        assert data["payroll"]["year"] == 2026

    def test_include_payroll_for_manager(self, client: TestClient, manager_pay: Employee,
                                         calendar_2026: ProductionCalendar):
        token = get_token(client, "paymgr@example.com", "mgr123")
        resp = client.get("/api/timesheet/2026/5?include_payroll=true",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["payroll"] is not None

    def test_include_payroll_ignored_for_employee(self, client: TestClient, db_session: Session):
        emp = Employee(
            full_name="Pay Employee",
            email="payemp@example.com",
            hashed_password=hash_password("emp123"),
            role="employee",
            is_active=True,
            must_change_password=False,
        )
        db_session.add(emp)
        db_session.commit()
        token = get_token(client, "payemp@example.com", "emp123")
        resp = client.get("/api/timesheet/2026/5?include_payroll=true",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["payroll"] is None

    def test_no_include_payroll_returns_null(self, client: TestClient, admin_pay: Employee,
                                              calendar_2026: ProductionCalendar):
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["payroll"] is None


class TestPayrollCalculationsIntegration:
    def test_full_norm_salary(self, client: TestClient, admin_pay: Employee,
                               worker: Employee, company: Company,
                               calendar_2026: ProductionCalendar, db_session: Session):
        _add_entries(db_session, worker.id, company.id,
                     [(d, "8") for d in MAY_BASIC_WORKDAYS])
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        emp_data = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert emp_data["is_calculable"] is True
        assert Decimal(emp_data["total_hours"]) == Decimal("176")
        assert Decimal(emp_data["base_amount"]) == Decimal("80000")
        assert Decimal(emp_data["overtime_amount"]) == Decimal("0")
        assert Decimal(emp_data["total_amount"]) == Decimal("80000")

    def test_no_entries_zero_amounts(self, client: TestClient, admin_pay: Employee,
                                      worker: Employee, calendar_2026: ProductionCalendar):
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        emp_data = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert emp_data["total_hours"] == "0"
        assert emp_data["base_amount"] == "0"

    def test_no_rate_not_calculable(self, client: TestClient, admin_pay: Employee,
                                     worker_no_rate: Employee, company: Company,
                                     calendar_2026: ProductionCalendar, db_session: Session):
        _add_entries(db_session, worker_no_rate.id, company.id, [(2, "8")])
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        emp_data = next(e for e in resp.json()["employees"]
                        if e["employee_id"] == worker_no_rate.id)
        assert emp_data["is_calculable"] is False
        assert Decimal(emp_data["base_amount"]) == Decimal("0")
        # hours still visible
        assert Decimal(emp_data["total_hours"]) == Decimal("8")

    def test_department_filter(self, client: TestClient, admin_pay: Employee,
                                worker: Employee, dept: Department,
                                calendar_2026: ProductionCalendar):
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get(f"/api/timesheet/2026/5/payroll?department_id={dept.id}",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        ids = [e["employee_id"] for e in resp.json()["employees"]]
        assert worker.id in ids

    def test_summary_aggregates_correct(self, client: TestClient, admin_pay: Employee,
                                         worker: Employee, company: Company,
                                         calendar_2026: ProductionCalendar, db_session: Session):
        _add_entries(db_session, worker.id, company.id,
                     [(d, "8") for d in MAY_BASIC_WORKDAYS])
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        data = resp.json()
        assert Decimal(data["grand_total"]) == (
            Decimal(data["total_base_amount"])
            + Decimal(data["total_overtime_amount"])
            + Decimal(data["total_off_schedule_amount"])
        )

    def test_overtime_in_payroll(self, client: TestClient, admin_pay: Employee,
                                  worker: Employee, company: Company,
                                  calendar_2026: ProductionCalendar, db_session: Session):
        # Fill all 22 workdays with 9h each → overtime = 22h above norm 176
        _add_entries(db_session, worker.id, company.id,
                     [(d, "9") for d in MAY_BASIC_WORKDAYS])
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        emp_data = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert emp_data["is_calculable"] is True
        assert Decimal(emp_data["total_hours"]) == Decimal("198")  # 22 * 9
        assert Decimal(emp_data["overtime_hours"]) == Decimal("22")  # 198 - 176
        assert Decimal(emp_data["overtime_amount"]) > 0
        assert Decimal(emp_data["base_amount"]) == Decimal("80000")

    def test_company_breakdown_present(self, client: TestClient, admin_pay: Employee,
                                        worker: Employee, company: Company,
                                        calendar_2026: ProductionCalendar, db_session: Session):
        _add_entries(db_session, worker.id, company.id, [(2, "8")])
        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        emp_data = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert len(emp_data["breakdown_by_company"]) == 1
        bd = emp_data["breakdown_by_company"][0]
        assert bd["company_id"] == company.id
        assert bd["company_code"] == "PC"

    def test_head_company_does_not_limit_payroll(
        self, client: TestClient, admin_pay: Employee, worker: Employee,
        company: Company, dept: Department, calendar_2026: ProductionCalendar,
        db_session: Session,
    ):
        """Головная компания отдела — ярлык для дерева оргструктуры
        (task_org_structure ч.1). Сотрудник работает на другие юрлица, и расчёт
        распределяет часы по фактическим компаниям, а не по головной."""
        other = Company(code="OTH", name="Другое юрлицо", is_active=True)
        db_session.add(other)
        db_session.flush()
        # Головная компания отдела — company, а часы есть и на company, и на other
        dept.head_company_id = company.id
        db_session.commit()

        _add_entries(db_session, worker.id, company.id, [(4, "8"), (5, "8")])
        _add_entries(db_session, worker.id, other.id, [(6, "8"), (7, "8")])

        token = get_token(client, "payadmin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5/payroll",
                          headers={"Authorization": f"Bearer {token}"})
        emp_data = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        by_id = {b["company_id"]: b for b in emp_data["breakdown_by_company"]}
        assert set(by_id) == {company.id, other.id}
        assert Decimal(by_id[company.id]["hours"]) == Decimal("16")
        assert Decimal(by_id[other.id]["hours"]) == Decimal("16")
        assert (
            Decimal(by_id[company.id]["total"]) + Decimal(by_id[other.id]["total"])
            == Decimal(emp_data["total_amount"])
        )

    def test_off_schedule_24h_edge_case(self):
        """24 часа в выходной по графику → расчёт без переполнения"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, 17), hours=Decimal("24"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.is_calculable is True
        assert p.off_schedule_hours == Decimal("24")
        assert p.off_schedule_amount >= Decimal("0")
        # Total should be base + overtime + holiday
        assert p.total_amount == p.base_amount + p.overtime_amount + p.off_schedule_amount


class TestHolidayPay:
    """
    Работа в нерабочий ПРАЗДНИЧНЫЙ день — отдельная категория (не «вне графика»
    и не переработка). MAY_REAL: 1 мая — праздник-пятница, норма 5/2 = 160 ч.
    """

    def _emp(self, pay_type=None, coeff=None, fixed=None):
        emp = make_employee(schedule=make_schedule(8))
        if pay_type is not None:
            emp.holiday_pay_type = pay_type
        emp.holiday_coefficient = coeff
        emp.holiday_fixed_rate = fixed
        return emp

    def test_default_coefficient_is_one_and_half(self):
        """Поля не заданы → дефолт ×1.5, как у выходных."""
        emp = make_employee(schedule=make_schedule(8))
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)
        hourly = Decimal("80000") / Decimal("160")
        assert p.holiday_amount == (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))

    def test_custom_coefficient(self):
        """Ставка задаётся в карточке — ставим двойной вручную."""
        emp = self._emp("coefficient", coeff=Decimal("2"))
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)
        hourly = Decimal("80000") / Decimal("160")
        assert p.holiday_amount == (Decimal("8") * hourly * Decimal("2")).quantize(Decimal("1"))

    def test_zero_coefficient_shows_hours_without_pay(self):
        emp = self._emp("coefficient", coeff=Decimal("0"))
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)
        assert p.holiday_hours == Decimal("8")
        assert p.holiday_amount == Decimal("0")

    def test_fixed_rate(self):
        emp = self._emp("fixed_rate", fixed=Decimal("740"))
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]
        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)
        assert p.holiday_amount == Decimal("5920")

    def test_holiday_settings_independent_from_weekend(self):
        """Настройки праздников и выходных не пересекаются."""
        emp = make_employee(schedule=make_schedule(8))
        emp.weekend_pay_type = "fixed_rate"
        emp.weekend_fixed_rate = Decimal("740")
        emp.holiday_pay_type = "coefficient"
        emp.holiday_coefficient = Decimal("2")
        entries = [
            make_entry(work_date=date(2026, 5, 1), hours=Decimal("8")),   # праздник
            make_entry(work_date=date(2026, 5, 2), hours=Decimal("8")),   # суббота
        ]
        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)
        hourly = Decimal("80000") / Decimal("160")
        assert p.holiday_amount == (Decimal("8") * hourly * Decimal("2")).quantize(Decimal("1"))
        assert p.off_schedule_amount == Decimal("5920")

    def test_full_month_plus_holiday_is_paid(self):
        """
        Регресс на исходную дыру: полный месяц + выход в праздник раньше давал
        ровно оклад (часы упирались в потолок min(1, зачётные/норма) и пропадали).
        """
        emp = make_employee(schedule=make_schedule(8))
        workdays = [
            d for d in range(1, 32)
            if d not in (1, 2, 3, 9, 10, 16, 17, 23, 24, 30, 31)
        ]
        entries = [make_entry(work_date=date(2026, 5, d)) for d in workdays]
        assert sum(e.hours for e in entries) == Decimal("160")
        entries.append(make_entry(work_date=date(2026, 5, 1), hours=Decimal("8")))

        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        hourly = Decimal("80000") / Decimal("160")
        expected = (Decimal("8") * hourly * Decimal("1.5")).quantize(Decimal("1"))
        assert p.base_amount == Decimal("80000")
        assert p.holiday_hours == Decimal("8")
        assert p.holiday_amount == expected
        assert p.total_amount == Decimal("80000") + expected

    def test_holiday_distributed_to_the_company_where_worked(self):
        """Праздничные ₽ идут в ту компанию, где праздник отработан."""
        emp = make_employee(schedule=make_schedule(8))
        entries = [
            make_entry(work_date=date(2026, 5, 5), hours=Decimal("8"), company_id=1),
            make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"), company_id=2),
        ]
        p = calculate_employee_payroll(
            emp, entries, MAY_REAL, 2026, 5, companies_by_id={1: ("a", "A"), 2: ("b", "B")}
        )
        by_id = {b.company_id: b for b in p.breakdown_by_company}
        assert by_id[1].holiday_amount == Decimal("0")
        assert by_id[2].holiday_amount == p.holiday_amount
        assert by_id[2].holiday_hours == Decimal("8")
        assert sum(b.total for b in p.breakdown_by_company) == (
            p.base_amount + p.overtime_amount + p.off_schedule_amount + p.holiday_amount
        )


class TestPerShiftPay:
    """
    Посменная оплата (task_search_and_shiftpay): оклада нет, база = число
    отработанных смен × ставка. Меняется ТОЛЬКО база — переработки и доплат
    за выход вне графика/в праздник у посменного нет, всё остальное общее.

    MAY_REAL: 1 мая — праздник-пятница, Сб/Вс нерабочие; для 5/2 по 8 ч
    норма 20 смен / 160 ч.
    """

    RATE = Decimal("2500")
    # Плановые рабочие дни мая 2026 для 5/2 в MAY_REAL (20 смен)
    WORKDAYS = [d for d in range(1, 32) if d not in (1, 2, 3, 9, 10, 16, 17, 23, 24, 30, 31)]

    def _emp(self, shift_rate=RATE):
        emp = make_employee(rate=None, schedule=make_schedule(8))
        emp.pay_type = "per_shift"
        emp.shift_rate = shift_rate
        return emp

    def _entries(self, days, hours=Decimal("8"), company_id=1):
        return [
            make_entry(work_date=date(2026, 5, d), hours=hours, company_id=company_id)
            for d in days
        ]

    def test_base_is_shifts_times_rate(self):
        """AC5: 15 смен × 2500 = 37500."""
        p = calculate_employee_payroll(
            self._emp(), self._entries(self.WORKDAYS[:15]), MAY_REAL, 2026, 5
        )
        assert p.is_calculable is True
        assert p.pay_type == "per_shift"
        assert p.worked_shifts == 15
        assert p.base_amount == Decimal("37500")
        assert p.total_amount == Decimal("37500")

    def test_no_salary_needed(self):
        """Оклад пустой — расчёт не падает и не жалуется на «не задан оклад»."""
        emp = self._emp()
        assert emp.rate is None
        p = calculate_employee_payroll(emp, self._entries(self.WORKDAYS), MAY_REAL, 2026, 5)
        assert p.is_calculable is True
        assert p.reason_if_not_calculable is None

    def test_missing_shift_rate_not_calculable(self):
        p = calculate_employee_payroll(
            self._emp(shift_rate=None), self._entries(self.WORKDAYS), MAY_REAL, 2026, 5
        )
        assert p.is_calculable is False
        assert "ставка за смену" in (p.reason_if_not_calculable or "")

    def test_extra_shift_off_schedule_is_just_another_shift(self):
        """AC6: доп. смена в свой выходной = ещё одна ставка, без ×1.5."""
        base = calculate_employee_payroll(
            self._emp(), self._entries(self.WORKDAYS), MAY_REAL, 2026, 5
        )
        # 2 мая — суббота, выходной по графику 5/2
        extra = calculate_employee_payroll(
            self._emp(), self._entries(self.WORKDAYS + [2]), MAY_REAL, 2026, 5
        )
        assert extra.worked_shifts == base.worked_shifts + 1
        assert extra.base_amount == base.base_amount + self.RATE
        assert extra.off_schedule_hours == Decimal("0")
        assert extra.off_schedule_amount == Decimal("0")
        assert extra.total_amount == base.total_amount + self.RATE

    def test_holiday_shift_is_just_another_shift(self):
        """Смена в праздник тоже стоит ровно ставку — без удвоения."""
        p = calculate_employee_payroll(
            self._emp(), self._entries(self.WORKDAYS + [1]), MAY_REAL, 2026, 5
        )
        assert p.holiday_hours == Decimal("0")
        assert p.holiday_amount == Decimal("0")
        assert p.base_amount == self.RATE * Decimal(len(self.WORKDAYS) + 1)

    def test_no_overtime(self):
        """Смена длиннее нормы часов переработки не даёт — платим за смену."""
        p = calculate_employee_payroll(
            self._emp(), self._entries(self.WORKDAYS[:5], hours=Decimal("12")),
            MAY_REAL, 2026, 5,
        )
        assert p.total_hours == Decimal("60")
        assert p.overtime_hours == Decimal("0")
        assert p.overtime_amount == Decimal("0")
        assert p.base_amount == self.RATE * Decimal(5)

    def test_notional_salary_for_absences(self):
        """
        AC7: отпуск считается от условного оклада = ставка × норма смен.
        Май 2026 для 5/2: норма 20 смен / 160 ч → условный оклад 50000.
        5 дней отпуска → 50000 / 160 × (5 × 8).
        """
        from app.models.employee_absences import EmployeeAbsence

        vacation_days = self.WORKDAYS[:5]
        absences = [
            EmployeeAbsence(employee_id=1, work_date=date(2026, 5, d), kind="vacation")
            for d in vacation_days
        ]
        p = calculate_employee_payroll(
            self._emp(), self._entries(self.WORKDAYS[5:]), MAY_REAL, 2026, 5,
            absences=absences,
        )
        assert p.norm_shifts == 20
        assert p.rate == Decimal("50000")  # условный оклад = 2500 × 20
        assert p.vacation_paid_days == 5
        expected = (
            Decimal("50000") / Decimal("160") * Decimal("8") * Decimal("5")
        ).quantize(Decimal("1"))
        assert p.vacation_amount == expected
        # База — только за фактически отработанные смены, задвоения нет
        assert p.base_amount == self.RATE * Decimal(len(self.WORKDAYS) - 5)
        assert p.total_amount == p.base_amount + expected

    def test_breakdown_distributes_base_by_company(self):
        """Распределение по компаниям работает как у окладников."""
        entries = (
            self._entries(self.WORKDAYS[:10], company_id=1)
            + self._entries(self.WORKDAYS[10:], company_id=2)
        )
        p = calculate_employee_payroll(
            self._emp(), entries, MAY_REAL, 2026, 5,
            companies_by_id={1: ("a", "A"), 2: ("b", "B")},
        )
        assert sum(b.base_amount for b in p.breakdown_by_company) == p.base_amount
        assert sum(b.total for b in p.breakdown_by_company) == p.base_amount
        assert {b.company_id for b in p.breakdown_by_company} == {1, 2}

    def test_salary_employee_unchanged(self):
        """AC9: окладник считается ровно как раньше — та же полная норма → оклад."""
        emp = make_employee(schedule=make_schedule(8))  # pay_type по умолчанию
        p = calculate_employee_payroll(
            emp, self._entries(self.WORKDAYS), MAY_REAL, 2026, 5
        )
        assert p.pay_type == "salary"
        assert p.shift_rate is None
        assert p.total_hours == Decimal("160")
        assert p.base_amount == Decimal("80000")
        assert p.total_amount == Decimal("80000")
