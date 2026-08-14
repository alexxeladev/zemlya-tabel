"""
Отпускные и больничные для СКОЛЬЗЯЩИХ графиков (task_vacation_shift_fix).

Правило: `сумма = оклад / норма_часов × часы_отсутствия`, где
`часы_отсутствия = рабочие СМЕНЫ ГРАФИКА в периоде × длина смены графика`.
Календарные дни периода сами по себе не оплачиваются: у сменщика выходные
цикла в оплату не идут, а смена стоит 12 ч, а не 8.

Регрессия: у окладника 5/2 (смена 8 ч, рабочие дни Пн–Пт) результат обязан
остаться прежним — там новое правило совпадает со старым.
"""
from datetime import date
from decimal import Decimal

from app.models.employee_absences import EmployeeAbsence
from app.models.employees import Employee
from app.models.schedules import Schedule
from app.services.payroll import calculate_employee_payroll

# Май 2026: нерабочие 1,3,4,10,11,17,18,24,25,31 + сокращённый 8*
MAY_167 = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,3,4,8*,10,11,17,18,24,25,31"}],
}


def cyclic(name: str, work: int, off: int, hours: int = 12, start=date(2026, 5, 1)):
    s = Schedule(
        name=name, hours_per_shift=hours, schedule_type="cyclic",
        cycle_start_date=start, cycle_work_days=work, cycle_off_days=off,
    )
    s.id = 1
    return s


def weekday(name: str = "5/2", hours: int = 8, days=None):
    s = Schedule(name=name, hours_per_shift=hours, schedule_type="weekday")
    s.work_weekdays = days
    s.id = 2
    return s


def make_employee(schedule: Schedule, rate: str = "90000") -> Employee:
    emp = Employee(full_name="Сменщик", rate=Decimal(rate), is_active=True)
    emp.id = 1
    emp.schedule = schedule
    return emp


def absences(days, kind: str = "vacation") -> list[EmployeeAbsence]:
    return [
        EmployeeAbsence(employee_id=1, work_date=date(2026, 5, d), kind=kind)
        for d in days
    ]


def pay(emp: Employee, absence_list) -> object:
    return calculate_employee_payroll(
        emp, [], MAY_167, 2026, 5, absences=absence_list
    )


# ── Скользящий график 3/1 ─────────────────────────────────────────────────────

class TestCyclic31:
    """
    3/1 по 12 ч, анкер 01.05.2026 → выходные цикла в мае: 4, 8, 12, 16, 20, 24, 28.
    Смен в мае 24 → норма 24 × 12 = 288 ч.
    """

    def test_norm_is_by_cycle(self):
        p = pay(make_employee(cyclic("3/1", 3, 1)), [])
        assert p.norm_hours == Decimal("288")
        assert p.norm_days == 24

    def test_vacation_counts_shifts_not_calendar_days(self):
        """
        AC1, AC2: отпуск 14 календарных дней (5–18 мая). Выходные цикла 8, 12, 16
        не оплачиваются → 11 смен × 12 ч = 132 ч.
        Отпускные = 90000/288 × 132 = 41250.
        Старое (календарные дни × 8) дало бы 10 × 8 = 80 ч → 25000.
        """
        emp = make_employee(cyclic("3/1", 3, 1))
        p = pay(emp, absences(range(5, 19)))

        assert p.vacation_days == 14          # календарных дней отпуска
        assert p.vacation_paid_days == 11     # из них смен по графику
        assert p.vacation_amount == Decimal("41250")
        assert p.vacation_amount != Decimal("25000")  # не старая формула «× 8»

    def test_vacation_only_on_cycle_days_off_is_not_paid(self):
        """AC5: отпуск, целиком попавший на выходные цикла, денег не даёт."""
        p = pay(make_employee(cyclic("3/1", 3, 1)), absences([4, 8, 12, 16]))

        assert p.vacation_days == 4
        assert p.vacation_paid_days == 0
        assert p.vacation_amount == Decimal("0")

    def test_shift_hours_taken_from_schedule(self):
        """Длина смены — из графика: те же 11 смен по 8 ч дали бы 27500."""
        emp = make_employee(cyclic("3/1", 3, 1, hours=12))
        p = pay(emp, absences(range(5, 19)))
        # 41250 / 27500 = 12/8 — разница ровно в длине смены
        assert p.vacation_amount * 8 == Decimal("27500") * 12


# ── Скользящий график 2/2 ─────────────────────────────────────────────────────

class TestCyclic22:
    """
    2/2 по 12 ч, анкер 01.05.2026 → смены 1,2,5,6,9,10,13,14,17,18,21,22,25,26,29,30.
    16 смен → норма 192 ч.
    """

    def test_vacation_on_2_2(self):
        """AC1: отпуск 1–14 мая → 8 смен × 12 = 96 ч; 90000/192 × 96 = 45000."""
        emp = make_employee(cyclic("2/2", 2, 2))
        p = pay(emp, absences(range(1, 15)))

        assert p.norm_hours == Decimal("192")
        assert p.vacation_days == 14
        assert p.vacation_paid_days == 8
        assert p.vacation_amount == Decimal("45000")

    def test_holiday_calendar_does_not_shorten_shift_vacation(self):
        """
        Праздники календаря сменщику отпуск не режут: 1 и 3 мая нерабочие по
        календарю, но 1 мая — смена цикла и оплачивается.
        """
        emp = make_employee(cyclic("2/2", 2, 2))
        p = pay(emp, absences([1, 2, 3, 4]))

        assert p.vacation_paid_days == 2      # 1 и 2 мая — смены; 3 и 4 — выходные цикла
        assert p.vacation_amount == Decimal("11250")  # 90000/192 × 24

    def test_sick_on_shift_schedule(self):
        """
        AC3: больничный считается так же. 6–8 мая: 6-е — смена, 7-е и 8-е —
        выходные цикла. По календарю все три дня рабочие, старая формула дала бы
        3 × 8 = 24 ч; правильно — 1 смена × 12 ч.
        90000/192 × 12 = 5625.
        """
        emp = make_employee(cyclic("2/2", 2, 2))
        p = pay(emp, absences([6, 7, 8], kind="sick"))

        assert p.sick_days == 3
        assert p.sick_paid_days == 1
        assert p.sick_amount == Decimal("5625")

    def test_sick_limit_counts_only_shifts(self):
        """
        Годовой лимит расходуют только оплачиваемые дни, т.е. смены графика:
        10 календарных дней Б у сменщика лимит целиком не съедают.
        """
        emp = make_employee(cyclic("2/2", 2, 2))
        p = pay(emp, absences(range(1, 11), kind="sick"))

        assert p.sick_days == 10
        assert p.sick_paid_days == 6          # смены 1,2,5,6,9,10
        assert p.sick_unpaid_days == 0        # лимит 10 дней не исчерпан
        assert p.sick_limit_remaining == 4


# ── Регрессия: weekday-графики ────────────────────────────────────────────────

class TestWeekdayRegression:
    def test_5_2_vacation_unchanged(self):
        """
        AC4: окладник 5/2 считается как раньше — 8 ч за день, рабочие дни Пн–Пт.
        50000/167 × (5 × 8) = 11976,05 → 11976 (значение из test_absences).
        """
        emp = make_employee(weekday(), rate="50000")
        p = pay(emp, absences([5, 6, 7, 12, 13]))

        assert p.norm_hours == Decimal("167")
        assert p.vacation_paid_days == 5
        assert p.vacation_amount == Decimal("11976")

    def test_5_2_weekend_and_holiday_still_not_paid(self):
        """Регрессия: выходные и праздники календаря по-прежнему не оплачиваются."""
        emp = make_employee(weekday(), rate="50000")
        p = pay(emp, absences([1, 3, 4, 10, 11]))

        assert p.vacation_days == 5
        assert p.vacation_paid_days == 0
        assert p.vacation_amount == Decimal("0")

    def test_5_2_transferred_working_saturday_still_paid(self):
        """
        Регрессия на краевой случай: 9 мая 2026 — суббота, объявленная рабочей
        (перенос). Старое правило («рабочий день календаря») её оплачивало,
        новое («плановый день графика») тоже — у 5/2 перенос попадает в план.
        """
        emp = make_employee(weekday(), rate="50000")
        p = pay(emp, absences([9]))

        assert p.vacation_paid_days == 1

    def test_6_1_uses_nine_hour_shift(self):
        """
        Длина смены берётся из графика и у weekday: 6/1 по 9 ч.
        Отпуск 12–16 мая (Вт–Сб): у 6/1 суббота рабочая → 5 смен × 9 = 45 ч.
        """
        emp = make_employee(weekday("6/1", hours=9, days=[0, 1, 2, 3, 4, 5]), rate="90000")
        p = pay(emp, absences([12, 13, 14, 15, 16]))

        assert p.vacation_paid_days == 5
        # норма 6/1 в мае считается сервисом; проверяем формулу через неё
        assert p.vacation_amount == round(
            Decimal("90000") / p.norm_hours * Decimal("45")
        )
