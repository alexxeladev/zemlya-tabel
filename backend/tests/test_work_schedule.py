"""
Tests for task_schedule_based_pay: «рабочий день по ГРАФИКУ сотрудника».

Май 2026: 1 — пятница (праздник), 2 — суббота, 3 — воскресенье, 5 — вторник.
"""
from datetime import date

from app.models.schedules import Schedule
from app.services.work_schedule import (
    DEFAULT_WORK_WEEKDAYS,
    is_off_schedule_day,
    is_schedule_work_day,
    parse_pattern,
    work_weekdays,
)

# Календарь с настоящими выходными: 1 мая праздник, Сб/Вс нерабочие.
MAY_REAL = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,2,3,9,10,16,17,23,24,30,31"}],
}

# Календарь с перенесённым выходным: суббота 16 мая объявлена рабочей.
MAY_WORKING_SATURDAY = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,2,3,9,10,17,23,24,30,31"}],
}


def _schedule(name="5/2", schedule_type="standard", hours=8, **kwargs):
    s = Schedule(name=name, hours_per_shift=hours, schedule_type=schedule_type)
    for key, value in kwargs.items():
        setattr(s, key, value)
    return s


class TestPattern:
    def test_parse(self):
        assert parse_pattern("5/2") == (5, 2)
        assert parse_pattern("2/2") == (2, 2)
        assert parse_pattern("3/3") == (3, 3)
        assert parse_pattern(None) is None
        assert parse_pattern("вс-чт") is None

    def test_weekdays_from_name(self):
        assert work_weekdays(_schedule("5/2")) == frozenset({0, 1, 2, 3, 4})
        assert work_weekdays(_schedule("6/1")) == frozenset({0, 1, 2, 3, 4, 5})

    def test_weekdays_default_when_name_unparsed(self):
        assert work_weekdays(_schedule("вс-чт")) == DEFAULT_WORK_WEEKDAYS
        # 2/2 не недельный паттерн (2+2 ≠ 7) → дефолт Пн–Пт
        assert work_weekdays(_schedule("2/2")) == DEFAULT_WORK_WEEKDAYS

    def test_explicit_weekdays_win(self):
        s = _schedule("5/2")
        s.work_weekdays = [6, 0, 1, 2, 3]  # вс–чт
        assert work_weekdays(s) == frozenset({6, 0, 1, 2, 3})


class TestWeekdaySchedule:
    def test_holiday_on_weekday_is_work_day(self):
        """Календарный праздник в рабочий день недели — рабочий день графика."""
        assert is_schedule_work_day(_schedule("5/2"), date(2026, 5, 1), MAY_REAL) is True
        assert is_off_schedule_day(_schedule("5/2"), date(2026, 5, 1), MAY_REAL) is False

    def test_saturday_and_sunday_are_off_for_five_two(self):
        for day in (2, 3):
            assert is_schedule_work_day(_schedule("5/2"), date(2026, 5, day), MAY_REAL) is False

    def test_saturday_is_work_day_for_six_one(self):
        assert is_schedule_work_day(_schedule("6/1"), date(2026, 5, 2), MAY_REAL) is True
        assert is_schedule_work_day(_schedule("6/1"), date(2026, 5, 3), MAY_REAL) is False

    def test_transferred_working_saturday(self):
        """Перенос: календарь сделал субботу рабочей → она рабочая и по графику."""
        assert is_schedule_work_day(
            _schedule("5/2"), date(2026, 5, 16), MAY_WORKING_SATURDAY
        ) is True
        assert is_schedule_work_day(
            _schedule("5/2"), date(2026, 5, 16), MAY_REAL
        ) is False

    def test_works_without_calendar(self):
        assert is_schedule_work_day(_schedule("5/2"), date(2026, 5, 5)) is True
        assert is_schedule_work_day(_schedule("5/2"), date(2026, 5, 3)) is False

    def test_no_schedule_is_unknown(self):
        assert is_schedule_work_day(None, date(2026, 5, 3), MAY_REAL) is None
        # None не считается выходом вне графика — лишней доплаты не возникает
        assert is_off_schedule_day(None, date(2026, 5, 3), MAY_REAL) is False


class TestCyclicSchedule:
    """
    Сменный график 2/2 от стартовой даты. Поля даты старта в модели Schedule
    пока нет (задача сменных графиков), поэтому анкер выставляем атрибутом —
    функция читает `cycle_start_date`.
    """

    def _shift(self, name="2/2", start=date(2026, 5, 1)):
        return _schedule(name, schedule_type="shift", hours=12, cycle_start_date=start)

    def test_shift_on_holiday_is_work_day(self):
        """AC3: смена сменщика, попавшая на праздник → рабочий день (оклад)."""
        s = self._shift()  # цикл с 1 мая: 1,2 — смены; 3,4 — выходные
        assert is_schedule_work_day(s, date(2026, 5, 1), MAY_REAL) is True
        assert is_off_schedule_day(s, date(2026, 5, 1), MAY_REAL) is False

    def test_own_day_off_in_cycle(self):
        """AC4: выход в свой выходной цикла → вне графика."""
        s = self._shift()
        assert is_schedule_work_day(s, date(2026, 5, 3), MAY_REAL) is False
        assert is_off_schedule_day(s, date(2026, 5, 3), MAY_REAL) is True

    def test_calendar_does_not_affect_cycle(self):
        """Суббота/воскресенье календаря — обычные смены, если цикл рабочий."""
        s = self._shift()
        # 9 мая — суббота; цикл 2/2 от 1 мая: (9−1)%4 = 0 → смена
        assert is_schedule_work_day(s, date(2026, 5, 9), MAY_REAL) is True
        # 5 мая — вторник (рабочий по календарю); (5−1)%4 = 0 → тоже смена
        assert is_schedule_work_day(s, date(2026, 5, 5), MAY_REAL) is True
        # 7 мая — четверг, но по циклу выходной
        assert is_schedule_work_day(s, date(2026, 5, 7), MAY_REAL) is False

    def test_three_three_cycle(self):
        s = self._shift(name="3/3")
        # с 1 мая: 1,2,3 — смены; 4,5,6 — выходные; 7 — снова смена
        assert [is_schedule_work_day(s, date(2026, 5, d), MAY_REAL) for d in range(1, 8)] == [
            True, True, True, False, False, False, True
        ]

    def test_no_anchor_is_unknown(self):
        """Дата старта цикла не задана → определить нельзя (None), не выходной."""
        s = self._shift(start=None)
        assert is_schedule_work_day(s, date(2026, 5, 3), MAY_REAL) is None
        assert is_off_schedule_day(s, date(2026, 5, 3), MAY_REAL) is False

    def test_dates_before_anchor(self):
        """Даты до старта цикла считаются по тому же модулю — цикл бесконечен."""
        s = self._shift(start=date(2026, 5, 11))
        # 9 мая: (9−11) % 4 = 2 → выходной цикла
        assert is_schedule_work_day(s, date(2026, 5, 9), MAY_REAL) is False
        # 10 мая: (10−11) % 4 = 3 → выходной цикла
        assert is_schedule_work_day(s, date(2026, 5, 10), MAY_REAL) is False
        # 11 мая: старт → смена
        assert is_schedule_work_day(s, date(2026, 5, 11), MAY_REAL) is True
