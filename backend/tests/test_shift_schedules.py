"""
task_shift_schedules — сменные графики (2/2, 3/3) и weekday-графики
с произвольными днями недели.

Эталон из 1С, июнь 2026 (1 июня — понедельник, 12 июня — пятница, праздник,
11 июня — сокращённый):
  - 2/2 смена 1: 1,4,5,8,9,12,13,16,17,20,21,24,25,28,29 (15 смен, 180 ч)
  - 2/2 смена 2: 2,3,6,7,10,11,14,15,18,19,22,23,26,27,30 (15 смен, 180 ч)
  - 3/3 смена 1: 4,5,6,10,11,12,16,17,18,22,23,24,28,29,30 (15 смен, 180 ч)
  - 3/3 смена 2: противофаза к смене 1 (сдвиг на 3 дня)
  - 6/1: Пн–Сб, 9 ч
"""
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
from app.services.work_schedule import (
    is_planned_work_day,
    is_schedule_work_day,
    norm_hours_for_schedule,
    normalize_schedule_type,
    planned_work_dates,
    schedule_issue,
    work_weekdays,
)
from tests.conftest import get_token

YEAR, MONTH = 2026, 6

# Реальный производственный календарь 2026 (xmlcalendar.ru), июнь:
# 6,7 — выходные; 11 — сокращённый; 12 — День России; 13,14,20,21,27,28 — выходные.
CAL_JUNE = {
    "year": 2026,
    "months": [{"month": 6, "days": "6,7,11*,12,13,14,20,21,27,28"}],
}

# Эталонные фазы из 1С
REF_2_2_SHIFT1 = [1, 4, 5, 8, 9, 12, 13, 16, 17, 20, 21, 24, 25, 28, 29]
REF_2_2_SHIFT2 = [2, 3, 6, 7, 10, 11, 14, 15, 18, 19, 22, 23, 26, 27, 30]
REF_3_3_SHIFT1 = [4, 5, 6, 10, 11, 12, 16, 17, 18, 22, 23, 24, 28, 29, 30]
REF_3_3_SHIFT2 = [1, 2, 3, 7, 8, 9, 13, 14, 15, 19, 20, 21, 25, 26, 27]


def cyclic(name, start, work=2, off=2, hours=12):
    return Schedule(
        name=name, hours_per_shift=hours, schedule_type="cyclic",
        cycle_start_date=start, cycle_work_days=work, cycle_off_days=off,
    )


def weekday_sched(name="5/2", hours=8, days=None):
    s = Schedule(name=name, hours_per_shift=hours, schedule_type="weekday")
    s.work_weekdays = days
    return s


def days_of(schedule, calendar_data=CAL_JUNE):
    return [d.day for d in planned_work_dates(schedule, YEAR, MONTH, calendar_data)]


# ── Тип графика ───────────────────────────────────────────────────────────────

class TestScheduleType:
    def test_legacy_values_normalized(self):
        assert normalize_schedule_type("standard") == "weekday"
        assert normalize_schedule_type("shift") == "cyclic"
        assert normalize_schedule_type("weekday") == "weekday"
        assert normalize_schedule_type("cyclic") == "cyclic"
        assert normalize_schedule_type(None) == "weekday"

    def test_cyclic_without_anchor_has_issue(self):
        s = cyclic("2/2", None)
        assert schedule_issue(s) is not None
        assert "дата начала цикла" in schedule_issue(s)

    def test_weekday_never_has_issue(self):
        assert schedule_issue(weekday_sched()) is None


# ── Цикл 2/2 и 3/3: сверка фаз с 1С ───────────────────────────────────────────

class TestCyclicPhases:
    def test_2_2_shift1_matches_1c(self):
        """AC4: 2/2 смена 1 в июне 2026 — ровно эталонный список из 1С."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        assert days_of(s) == REF_2_2_SHIFT1

    def test_2_2_shift2_matches_1c(self):
        s = cyclic("2/2 смена 2", date(2026, 6, 2))
        assert days_of(s) == REF_2_2_SHIFT2

    def test_shifts_are_antiphase(self):
        """AC3: две смены одного графика не пересекаются и покрывают весь месяц."""
        s1 = set(days_of(cyclic("2/2 смена 1", date(2026, 5, 31))))
        s2 = set(days_of(cyclic("2/2 смена 2", date(2026, 6, 2))))
        assert s1 & s2 == set()
        assert s1 | s2 == set(range(1, 31))

    def test_3_3_shift1_matches_1c(self):
        s = cyclic("3/3 смена 1", date(2026, 6, 4), work=3, off=3)
        assert days_of(s) == REF_3_3_SHIFT1

    def test_3_3_shift2_is_antiphase(self):
        """Смена 2 — тот же цикл со сдвигом на 3 дня."""
        s = cyclic("3/3 смена 2", date(2026, 6, 7), work=3, off=3)
        assert days_of(s) == REF_3_3_SHIFT2
        assert set(days_of(s)) & set(REF_3_3_SHIFT1) == set()

    def test_anchor_far_in_the_past(self):
        """Стартовая дата в прошлом году — фаза та же (mod-арифметика)."""
        near = cyclic("2/2", date(2026, 5, 31))
        far = cyclic("2/2", date(2024, 1, 6))  # 2026-05-31 − 2024-01-06 кратно 4
        assert (date(2026, 5, 31) - date(2024, 1, 6)).days % 4 == 0
        assert days_of(far) == days_of(near) == REF_2_2_SHIFT1

    def test_calendar_does_not_shift_the_cycle(self):
        """Праздник 12 июня — обычная смена; выходные календаря цикл не трогают."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        assert is_planned_work_day(s, date(2026, 6, 12), CAL_JUNE) is True
        assert is_schedule_work_day(s, date(2026, 6, 12), CAL_JUNE) is True
        # Без календаря — тот же результат
        assert days_of(s, calendar_data=None) == REF_2_2_SHIFT1

    def test_pattern_from_name_when_columns_empty(self):
        """Паттерн не задан колонками → выводится из имени «2/2»."""
        s = Schedule(name="2/2", hours_per_shift=12, schedule_type="cyclic")
        s.cycle_start_date = date(2026, 5, 31)
        assert days_of(s) == REF_2_2_SHIFT1


# ── Норма ─────────────────────────────────────────────────────────────────────

class TestNorm:
    def test_cyclic_norm_is_shifts_times_hours(self):
        """AC5: 2/2 по 12 ч в июне 2026 → 15 смен × 12 = 180 ч (не 167)."""
        for start in (date(2026, 5, 31), date(2026, 6, 2)):
            assert norm_hours_for_schedule(cyclic("2/2", start), YEAR, MONTH, CAL_JUNE) == 180

    def test_cyclic_3_3_norm(self):
        s = cyclic("3/3", date(2026, 6, 4), work=3, off=3)
        assert norm_hours_for_schedule(s, YEAR, MONTH, CAL_JUNE) == 180

    def test_cyclic_norm_ignores_short_day(self):
        """Сокращённый день календаря смену сменщика не укорачивает."""
        s = cyclic("2/2 смена 2", date(2026, 6, 2))  # 11 июня — смена и сокращённый день
        assert 11 in days_of(s)
        assert norm_hours_for_schedule(s, YEAR, MONTH, CAL_JUNE) == 180

    def test_five_two_norm_unchanged(self):
        """Существующие 5/2 не сломались: июнь 2026 = 21 день × 8 − 1 = 167."""
        assert norm_hours_for_schedule(weekday_sched("5/2"), YEAR, MONTH, CAL_JUNE) == 167

    def test_six_one_norm(self):
        """6/1 по 9 ч: Пн–Сб минус праздник 12 июня = 25 дней; 11 июня сокращён."""
        s = weekday_sched("6/1", hours=9)
        assert len(days_of(s)) == 25
        assert norm_hours_for_schedule(s, YEAR, MONTH, CAL_JUNE) == 25 * 9 - 1

    def test_no_norm_without_anchor(self):
        assert norm_hours_for_schedule(cyclic("2/2", None), YEAR, MONTH, CAL_JUNE) is None


# ── Weekday-графики с произвольными днями недели ──────────────────────────────

class TestWeekdaySchedules:
    def test_explicit_weekdays_win_over_name(self):
        s = weekday_sched("5/2 вс-чт", days=[6, 0, 1, 2, 3])
        assert work_weekdays(s) == frozenset({6, 0, 1, 2, 3})

    def test_sunday_to_thursday(self):
        """Вс–Чт: воскресенья рабочие, пятницы и субботы — нет."""
        s = weekday_sched("вс-чт", days=[6, 0, 1, 2, 3])
        got = days_of(s)
        assert 7 in got and 14 in got          # воскресенья
        assert 5 not in got and 6 not in got   # пятница и суббота
        assert 12 not in got                   # 12 июня пятница, и не в графике

    def test_tuesday_to_saturday(self):
        """Вт–Сб: субботы рабочие, понедельники — выходные."""
        s = weekday_sched("вт-сб", days=[1, 2, 3, 4, 5])
        got = days_of(s)
        assert 6 in got and 13 in got          # субботы
        assert 1 not in got and 8 not in got   # понедельники
        assert 12 not in got                   # праздник исключён из плана

    def test_holiday_excluded_but_still_paid_as_work_day(self):
        """
        12 июня: в план (норму/автозаполнение) не входит, но фактический выход
        оплачивается по окладу — это рабочий день недели графика.
        """
        s = weekday_sched("5/2")
        assert is_planned_work_day(s, date(2026, 6, 12), CAL_JUNE) is False
        assert is_schedule_work_day(s, date(2026, 6, 12), CAL_JUNE) is True

    def test_saturday_off_for_five_two(self):
        s = weekday_sched("5/2")
        assert 6 not in days_of(s)


# ── Расчёт ЗП сменщика ────────────────────────────────────────────────────────

def _employee(schedule, rate="60000"):
    emp = Employee(full_name="Сменщик", rate=Decimal(rate))
    emp.id = 1
    emp.schedule = schedule
    return emp


def _entries(days, hours=12, company_id=1):
    out = []
    for d in days:
        e = TimesheetEntry(
            employee_id=1, work_date=date(YEAR, MONTH, d),
            company_id=company_id, hours=hours,
        )
        out.append(e)
    return out


class TestShiftPayroll:
    def test_is_calculable(self):
        """AC6: сменный график больше не отбраковывается."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        p = calculate_employee_payroll(
            _employee(s), _entries(REF_2_2_SHIFT1), CAL_JUNE, YEAR, MONTH
        )
        assert p.is_calculable is True
        assert p.reason_if_not_calculable is None
        assert p.norm_hours == 180
        assert p.norm_days == 15

    def test_full_month_no_overtime_full_salary(self):
        """Отработал ровно норму 180 ч → переработки нет, оклад полный."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        p = calculate_employee_payroll(
            _employee(s), _entries(REF_2_2_SHIFT1), CAL_JUNE, YEAR, MONTH
        )
        assert p.total_hours == 180
        assert p.overtime_hours == 0
        assert p.holiday_hours == 0
        assert p.base_amount == Decimal("60000")
        assert p.total_amount == Decimal("60000")

    def test_overtime_from_shift_hours_not_eight(self):
        """
        192 ч вместо 180 → переработка 12 ч. Дневная норма сменщика 12, не 8:
        одна лишняя смена целиком уходит в переработку.
        """
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        extra = REF_2_2_SHIFT1 + [2]  # 2 июня — выходной цикла… берём внутри смены
        # Переработка внутри смен: один день 24 ч вместо 12
        entries = _entries(REF_2_2_SHIFT1)
        entries.append(TimesheetEntry(
            employee_id=1, work_date=date(YEAR, MONTH, 1), company_id=2, hours=12,
        ))
        p = calculate_employee_payroll(_employee(s), entries, CAL_JUNE, YEAR, MONTH)
        assert p.total_hours == 192
        assert p.overtime_hours == 12
        assert p.base_amount == Decimal("60000")
        assert extra  # список выше — только для читаемости сценария

    def test_work_on_own_day_off_is_off_schedule(self):
        """Выход в выходной ЦИКЛА → часы вне графика, не переработка."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        entries = _entries(REF_2_2_SHIFT1 + [2])  # 2 июня — выходной цикла
        p = calculate_employee_payroll(_employee(s), entries, CAL_JUNE, YEAR, MONTH)
        assert p.holiday_hours == 12
        assert p.overtime_hours == 0
        assert p.base_amount == Decimal("60000")
        # 12 ч × (60000/180) × 1.5 = 6000
        assert p.holiday_amount == Decimal("6000")

    def test_shift_on_holiday_paid_as_regular(self):
        """Смена 12 июня (праздник) — обычная смена, доплаты нет."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        assert 12 in REF_2_2_SHIFT1
        p = calculate_employee_payroll(
            _employee(s), _entries(REF_2_2_SHIFT1), CAL_JUNE, YEAR, MONTH
        )
        assert p.holiday_hours == 0

    def test_cyclic_calculable_without_calendar(self):
        """Сменщику производственный календарь не нужен — цикл самодостаточен."""
        s = cyclic("2/2 смена 1", date(2026, 5, 31))
        p = calculate_employee_payroll(
            _employee(s), _entries(REF_2_2_SHIFT1), None, YEAR, MONTH
        )
        assert p.is_calculable is True
        assert p.norm_hours == 180


# ── API: CRUD, превью, автозаполнение ─────────────────────────────────────────

@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Admin", email="admin@example.com",
        hashed_password=hash_password("admin123"), role="admin", is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def calendar_june(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=YEAR, data=CAL_JUNE, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Смены", code="SHF", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(name="ООО Смена", code="shift", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


def _headers(client):
    return {"Authorization": f"Bearer {get_token(client, 'admin@example.com', 'admin123')}"}


class TestScheduleApi:
    def test_create_cyclic_schedule(self, client: TestClient, admin: Employee):
        """AC2: cyclic-график со стартовой датой и паттерном."""
        resp = client.post("/api/schedules", json={
            "name": "2/2 смена 1", "hours_per_shift": 12, "schedule_type": "cyclic",
            "cycle_start_date": "2026-05-31", "cycle_work_days": 2, "cycle_off_days": 2,
        }, headers=_headers(client))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["schedule_type"] == "cyclic"
        assert body["cycle_start_date"] == "2026-05-31"
        assert body["cycle_work_days"] == 2

    def test_create_weekday_schedule_with_custom_days(self, client: TestClient, admin: Employee):
        """AC1: weekday-график с произвольными рабочими днями (вс–чт)."""
        resp = client.post("/api/schedules", json={
            "name": "Пятидневка вс-чт", "hours_per_shift": 8,
            "schedule_type": "weekday", "work_weekdays": [6, 0, 1, 2, 3],
        }, headers=_headers(client))
        assert resp.status_code == 201, resp.text
        assert resp.json()["work_weekdays"] == [0, 1, 2, 3, 6]

    def test_cyclic_requires_anchor(self, client: TestClient, admin: Employee):
        resp = client.post("/api/schedules", json={
            "name": "2/2 без старта", "hours_per_shift": 12, "schedule_type": "cyclic",
            "cycle_work_days": 2, "cycle_off_days": 2,
        }, headers=_headers(client))
        assert resp.status_code == 422

    def test_legacy_type_accepted(self, client: TestClient, admin: Employee):
        resp = client.post("/api/schedules", json={
            "name": "5/2", "hours_per_shift": 8, "schedule_type": "standard",
        }, headers=_headers(client))
        assert resp.status_code == 201
        assert resp.json()["schedule_type"] == "weekday"

    def test_preview_cyclic(self, client: TestClient, admin: Employee,
                            calendar_june: ProductionCalendar):
        """AC7: превью показывает рабочие дни месяца и норму."""
        resp = client.post("/api/schedules/preview", json={
            "year": YEAR, "month": MONTH, "hours_per_shift": 12,
            "schedule_type": "cyclic", "cycle_start_date": "2026-05-31",
            "cycle_work_days": 2, "cycle_off_days": 2,
        }, headers=_headers(client))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["work_days"] == 15
        assert body["norm_hours"] == 180
        assert [d["day"] for d in body["days"] if d["is_work_day"]] == REF_2_2_SHIFT1

    def test_preview_weekday_six_one(self, client: TestClient, admin: Employee,
                                     calendar_june: ProductionCalendar):
        resp = client.post("/api/schedules/preview", json={
            "year": YEAR, "month": MONTH, "hours_per_shift": 9,
            "schedule_type": "weekday", "work_weekdays": [0, 1, 2, 3, 4, 5],
        }, headers=_headers(client))
        assert resp.status_code == 200
        body = resp.json()
        assert body["work_days"] == 25
        assert body["norm_hours"] == 25 * 9 - 1

    def test_preview_forbidden_for_employee(self, client: TestClient, db_session: Session):
        emp = Employee(
            full_name="Рядовой", email="emp@example.com",
            hashed_password=hash_password("emp12345"), role="employee", is_active=True,
        )
        db_session.add(emp)
        db_session.commit()
        token = get_token(client, "emp@example.com", "emp12345")
        resp = client.post("/api/schedules/preview", json={"year": YEAR, "month": MONTH},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403


class TestAutofill:
    def _employee_on(self, db_session, schedule, dept, company, name="Сменщик"):
        db_session.add(schedule)
        db_session.commit()
        db_session.refresh(schedule)
        emp = Employee(
            full_name=name, is_active=True, department_id=dept.id,
            schedule_id=schedule.id, default_company_id=company.id,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        return emp

    def test_autofill_cyclic_matches_1c(
        self, client: TestClient, admin: Employee, calendar_june: ProductionCalendar,
        dept: Department, company: Company, db_session: Session,
    ):
        """AC4: автозаполнение расставляет смены ровно по эталону 1С."""
        emp = self._employee_on(
            db_session, cyclic("2/2 смена 1", date(2026, 5, 31)), dept, company
        )
        resp = client.post("/api/timesheet/autofill/preview",
                           json={"year": YEAR, "month": MONTH}, headers=_headers(client))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert emp.id not in [s["employee_id"] for s in body["employees_skipped"]]
        mine = [e for e in body["entries_to_create"] if e["employee_id"] == emp.id]
        assert [int(e["work_date"][-2:]) for e in mine] == REF_2_2_SHIFT1
        assert {int(e["hours"]) for e in mine} == {12}

    def test_autofill_apply_creates_entries(
        self, client: TestClient, admin: Employee, calendar_june: ProductionCalendar,
        dept: Department, company: Company, db_session: Session,
    ):
        emp = self._employee_on(
            db_session, cyclic("3/3 смена 1", date(2026, 6, 4), work=3, off=3),
            dept, company,
        )
        resp = client.post("/api/timesheet/autofill/apply",
                           json={"year": YEAR, "month": MONTH}, headers=_headers(client))
        assert resp.status_code == 200, resp.text
        rows = (
            db_session.query(TimesheetEntry)
            .filter(TimesheetEntry.employee_id == emp.id).all()
        )
        assert sorted(r.work_date.day for r in rows) == REF_3_3_SHIFT1
        assert sum(int(r.hours) for r in rows) == 180

    def test_autofill_weekday_custom_days(
        self, client: TestClient, admin: Employee, calendar_june: ProductionCalendar,
        dept: Department, company: Company, db_session: Session,
    ):
        """6/1 заполняется по субботам, 12 июня (праздник) пропускается."""
        emp = self._employee_on(
            db_session, weekday_sched("6/1", hours=9, days=[0, 1, 2, 3, 4, 5]),
            dept, company,
        )
        resp = client.post("/api/timesheet/autofill/preview",
                           json={"year": YEAR, "month": MONTH}, headers=_headers(client))
        assert resp.status_code == 200
        mine = [e for e in resp.json()["entries_to_create"] if e["employee_id"] == emp.id]
        days = [int(e["work_date"][-2:]) for e in mine]
        assert len(days) == 25
        assert 6 in days and 13 in days      # субботы
        assert 12 not in days                # праздник
        assert 7 not in days                 # воскресенье
        # 11 июня сокращённый → 8 ч вместо 9
        hours_by_day = {int(e["work_date"][-2:]): int(e["hours"]) for e in mine}
        assert hours_by_day[11] == 8
        assert hours_by_day[10] == 9
