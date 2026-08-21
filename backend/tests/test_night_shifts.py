"""
Ночные смены: вычисляемая ставка, лимит фонда отдела, блокировка при
превышении, оплата и сосуществование с дневными часами (task_night_shifts_rework).

Модель: ставка = фонд_отдела / календарные_дни_месяца (вручную не задаётся),
лимит числа смен = фонд / ставка = число дней месяца, суммарно по отделу.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import DEFAULT_NIGHT_SHIFT_FUND, Department
from app.models.employees import Employee
from app.models.night_shifts import NightShift
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.services.night_shifts import (
    night_amount,
    night_rate_for_month,
    night_shift_limit,
)
from app.services.payroll import calculate_employee_payroll
from tests.conftest import get_token

# Июль 2026: 31 календарный день → ставка 100000/31 = 3225.81, лимит 31 смена.
JULY = {"year": 2026, "months": [{"month": 7, "days": "4,5,11,12,18,19,25,26"}]}
FUND = Decimal("100000")


# ── Чистая арифметика ставки и лимита ─────────────────────────────────────────

class TestRateAndLimit:
    def test_rate_is_fund_divided_by_calendar_days(self):
        """AC1: ставка = фонд / календарные дни месяца (31 в июле, не рабочие)."""
        assert night_rate_for_month(FUND, 2026, 7) == Decimal("3225.81")

    def test_rate_depends_on_month_length(self):
        """Февраль короче — смена дороже; делитель именно календарный."""
        assert night_rate_for_month(FUND, 2026, 2) == Decimal("3571.43")
        assert night_rate_for_month(FUND, 2026, 6) == Decimal("3333.33")

    def test_limit_equals_days_in_month(self):
        """AC3: лимит = фонд / ставка, что тождественно числу дней месяца.

        Через округлённую до копеек ставку было бы 100000/3225.81 = 30.99…,
        и floor молча отнял бы последнюю смену.
        """
        assert night_shift_limit(FUND, 2026, 7) == 31
        assert night_shift_limit(FUND, 2026, 2) == 28
        assert night_shift_limit(Decimal("250000"), 2026, 6) == 30

    def test_zero_fund_means_no_night_shifts(self):
        assert night_rate_for_month(Decimal("0"), 2026, 7) == Decimal("0")
        assert night_shift_limit(Decimal("0"), 2026, 7) == 0

    def test_full_limit_costs_the_fund(self):
        """Суть модели: полный лимит смен стоит ровно фонд — с точностью до
        округления ставки до копеек.

        31 × 3225.81 = 100000.11: ставка задачей округляется вверх до копейки,
        поэтому «перебор» возможен на копейки, но не больше рубля. Резать
        ставку вниз ради точного равенства не стали — цена смены должна
        совпадать с той, что видит и считает человек (100000 / 31 = 3225.81).
        """
        rate = night_rate_for_month(FUND, 2026, 7)
        limit = night_shift_limit(FUND, 2026, 7)
        assert abs(night_amount(limit, rate) - FUND) < Decimal("1")


# ── Оплата в расчёте ──────────────────────────────────────────────────────────

class TestNightPayroll:
    def _employee(self, rate: str = "50000") -> Employee:
        schedule = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday")
        schedule.id = 1
        emp = Employee(full_name="Ночной Сотрудник", rate=Decimal(rate), is_active=True)
        emp.id = 1
        emp.schedule = schedule
        return emp

    def test_night_allowance_is_shifts_times_rate(self):
        """AC8: 15 смен × (100000/31) = 48387 (пример из задачи)."""
        p = calculate_employee_payroll(
            self._employee(), [], JULY, 2026, 7,
            night_shifts=15, night_rate=night_rate_for_month(FUND, 2026, 7),
        )
        assert p.night_shifts == 15
        assert p.night_rate == Decimal("3225.81")
        assert p.night_amount == Decimal("48387")

    def test_night_is_added_on_top_of_day_pay(self):
        """AC7: надбавка входит в итог начисления, дневной расчёт не трогает."""
        emp = self._employee()
        day_only = calculate_employee_payroll(emp, [], JULY, 2026, 7)
        with_night = calculate_employee_payroll(
            emp, [], JULY, 2026, 7,
            night_shifts=3, night_rate=night_rate_for_month(FUND, 2026, 7),
        )
        assert with_night.base_amount == day_only.base_amount
        assert with_night.overtime_amount == day_only.overtime_amount
        assert with_night.total_amount == day_only.total_amount + with_night.night_amount

    def test_no_shifts_no_money(self):
        p = calculate_employee_payroll(
            self._employee(), [], JULY, 2026, 7,
            night_shifts=0, night_rate=night_rate_for_month(FUND, 2026, 7),
        )
        assert p.night_amount == Decimal("0")


# ── Фикстуры API ──────────────────────────────────────────────────────────────

@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=JULY, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="NS", name="Night Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Охрана", code="SEC", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _worker(
    db_session, name: str, tab: str, dept: Department, company: Company,
    schedule: Schedule, night: bool = True,
) -> Employee:
    emp = Employee(
        full_name=name,
        tab_number=tab,
        position="Охранник",
        department_id=dept.id,
        default_company_id=company.id,
        schedule_id=schedule.id,
        rate=50000,
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    emp.primary_position.has_night_shifts = night
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session, dept, company, schedule) -> Employee:
    return _worker(db_session, "Ночной Один", "N-001", dept, company, schedule)


@pytest.fixture
def worker2(db_session, dept, company, schedule) -> Employee:
    return _worker(db_session, "Ночной Два", "N-002", dept, company, schedule)


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="NS Admin",
        email="nsadmin@example.com",
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
def timekeeper(db_session: Session, dept: Department) -> Employee:
    emp = Employee(
        full_name="NS Табельщик",
        email="nstk@example.com",
        hashed_password=hash_password("tk123456"),
        role="timekeeper",
        is_active=True,
        must_change_password=False,
        managed_departments=[dept],
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def _auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'nsadmin@example.com', 'admin123')}"}


def _tk_auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'nstk@example.com', 'tk123456')}"}


def _mark(client: TestClient, emp: Employee, day: int, value: bool = True, headers=None):
    return client.put(
        "/api/timesheet/night-shift",
        json={
            "employee_id": emp.id,
            "position_id": emp.primary_position.id,
            "work_date": f"2026-07-{day:02d}",
            "value": value,
        },
        headers=headers or _auth(client),
    )


# ── Фонд отдела ───────────────────────────────────────────────────────────────

class TestDepartmentFund:
    def test_default_fund_is_100000(self, client: TestClient, admin, dept):
        """AC2: фонд задан на отделе, дефолт 100 000."""
        resp = client.get(f"/api/departments/{dept.id}", headers=_auth(client))
        assert resp.status_code == 200
        assert Decimal(resp.json()["night_shift_fund"]) == DEFAULT_NIGHT_SHIFT_FUND

    def test_admin_changes_fund(self, client: TestClient, admin, dept, db_session):
        resp = client.patch(
            f"/api/departments/{dept.id}",
            json={"night_shift_fund": "62000"},
            headers=_auth(client),
        )
        assert resp.status_code == 200
        db_session.refresh(dept)
        assert dept.night_shift_fund == Decimal("62000")

    def test_negative_fund_rejected(self, client: TestClient, admin, dept):
        resp = client.patch(
            f"/api/departments/{dept.id}",
            json={"night_shift_fund": "-1"},
            headers=_auth(client),
        )
        assert resp.status_code == 422


# ── Отметка смен и лимит ──────────────────────────────────────────────────────

class TestMarking:
    def test_mark_and_unmark(self, client: TestClient, admin, worker, calendar_2026, db_session):
        assert _mark(client, worker, 3).status_code == 200
        assert db_session.query(NightShift).count() == 1

        assert _mark(client, worker, 3, value=False).status_code == 200
        assert db_session.query(NightShift).count() == 0

    def test_repeat_mark_is_idempotent(self, client, admin, worker, calendar_2026, db_session):
        _mark(client, worker, 3)
        _mark(client, worker, 3)
        assert db_session.query(NightShift).count() == 1

    def test_night_coexists_with_day_hours(
        self, client: TestClient, admin, worker, company, calendar_2026, db_session
    ):
        """AC6: в один день и дневные часы, и ночная смена — не мешают друг другу."""
        hours = client.put(
            "/api/timesheet/cell",
            json={
                "employee_id": worker.id,
                "position_id": worker.primary_position.id,
                "work_date": "2026-07-03",
                "company_id": company.id,
                "hours": 8,
            },
            headers=_auth(client),
        )
        assert hours.status_code == 200
        assert _mark(client, worker, 3).status_code == 200

        month = client.get("/api/timesheet/2026/7", headers=_auth(client)).json()
        day_cells = [e for e in month["entries"] if e["work_date"] == "2026-07-03"]
        night = [n for n in month["night_shifts"] if n["work_date"] == "2026-07-03"]
        assert len(day_cells) == 1 and day_cells[0]["hours"] == 8
        assert len(night) == 1

    def test_absence_removes_night_shift(
        self, client: TestClient, admin, worker, calendar_2026, db_session
    ):
        """Код отсутствия ставится на ВЕСЬ день — ночная отметка этого дня
        снимается вместе с часами: человек либо отсутствует, либо выходит."""
        assert _mark(client, worker, 3).status_code == 200
        assert db_session.query(NightShift).count() == 1

        resp = client.put(
            "/api/timesheet/absence",
            json={"employee_id": worker.id, "work_date": "2026-07-03", "kind": "sick"},
            headers=_auth(client),
        )
        assert resp.status_code == 200
        assert db_session.query(NightShift).count() == 0

        # Освободившаяся смена вернулась в лимит отдела
        month = client.get("/api/timesheet/2026/7", headers=_auth(client)).json()
        fund = month["night_funds"][0]
        assert fund["used_shifts"] == 0

    def test_night_shift_rejected_on_absence_day(
        self, client: TestClient, admin, worker, calendar_2026, db_session
    ):
        """Обратная сторона: на день с кодом ночную не отметить.

        Код НЕ снимается молча (в отличие от часов): отсутствие отмечено на
        человеке целиком, а галочка ставится одному рабочему месту.
        """
        client.put(
            "/api/timesheet/absence",
            json={"employee_id": worker.id, "work_date": "2026-07-03", "kind": "vacation"},
            headers=_auth(client),
        )
        resp = _mark(client, worker, 3)
        assert resp.status_code == 422
        assert "ОТ" in resp.json()["detail"]
        assert db_session.query(NightShift).count() == 0

    def test_day_hours_still_coexist_with_night(
        self, client: TestClient, admin, worker, company, calendar_2026, db_session
    ):
        """Регрессия к AC6: запрет касается ТОЛЬКО отсутствий. Ввод дневных
        часов ночную отметку не трогает."""
        _mark(client, worker, 4)
        client.put(
            "/api/timesheet/cell",
            json={
                "employee_id": worker.id,
                "position_id": worker.primary_position.id,
                "work_date": "2026-07-04",
                "company_id": company.id,
                "hours": 8,
            },
            headers=_auth(client),
        )
        assert db_session.query(NightShift).count() == 1

    def test_flag_required(
        self, client: TestClient, admin, dept, company, schedule, calendar_2026, db_session
    ):
        """Ночные доступны только рабочим местам с включённым флагом."""
        plain = _worker(db_session, "Без ночных", "N-003", dept, company, schedule, night=False)
        resp = _mark(client, plain, 3)
        assert resp.status_code == 422
        assert "ночные" in resp.json()["detail"].lower()

    def test_employee_without_department_rejected(
        self, client: TestClient, admin, company, schedule, calendar_2026, db_session
    ):
        """Фонд — свойство отдела, поэтому без отдела ночных смен нет."""
        emp = Employee(
            full_name="Без отдела", tab_number="N-004", default_company_id=company.id,
            schedule_id=schedule.id, rate=50000, is_active=True,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        emp.primary_position.has_night_shifts = True
        db_session.commit()

        resp = _mark(client, emp, 3)
        assert resp.status_code == 422
        assert "отдел" in resp.json()["detail"].lower()


class TestFundLimit:
    def _fill(self, client, worker, days) -> list[int]:
        return [_mark(client, worker, d).status_code for d in days]

    def test_limit_blocks_over_fund(
        self, client: TestClient, admin, dept, worker, worker2, calendar_2026, db_session
    ):
        """AC4: 32-я смена отдела в 31-дневном месяце блокируется на бэке.

        Один человек лимит не выберет (в дне у него максимум одна ночная), а
        вдвоём — запросто: фонд общий.
        """
        assert self._fill(client, worker, range(1, 32)) == [200] * 31

        resp = _mark(client, worker2, 15)
        assert resp.status_code == 409
        assert "Лимит ночных смен отдела исчерпан" in resp.json()["detail"]
        assert "осталось 0 смен из 31" in resp.json()["detail"]
        assert db_session.query(NightShift).count() == 31

    def test_limit_is_shared_across_department(
        self, client: TestClient, admin, dept, worker, worker2, calendar_2026, db_session
    ):
        """AC3: лимит СУММАРНЫЙ по отделу — смены разных людей тратят один фонд,
        в том числе выходы в одну и ту же ночь."""
        assert _mark(client, worker, 1).status_code == 200
        assert _mark(client, worker, 2).status_code == 200
        assert _mark(client, worker2, 1).status_code == 200  # та же ночь, другой человек

        month = client.get("/api/timesheet/2026/7", headers=_auth(client)).json()
        fund = next(f for f in month["night_funds"] if f["department_id"] == dept.id)
        assert fund["used_shifts"] == 3
        assert fund["remaining_shifts"] == fund["limit_shifts"] - 3

    def test_zero_fund_blocks_everything(
        self, client: TestClient, admin, dept, worker, calendar_2026
    ):
        client.patch(
            f"/api/departments/{dept.id}",
            json={"night_shift_fund": "0"},
            headers=_auth(client),
        )
        resp = _mark(client, worker, 3)
        assert resp.status_code == 409
        assert "Лимит ночных смен отдела исчерпан" in resp.json()["detail"]
        assert "осталось 0 смен из 0" in resp.json()["detail"]

    def test_unmark_frees_the_limit(
        self, client: TestClient, admin, dept, worker, worker2, calendar_2026, db_session
    ):
        """Снял смену — лимит освободился (счётчик считается по факту)."""
        for d in range(1, 32):
            _mark(client, worker, d)
        assert _mark(client, worker2, 1).status_code == 409

        assert _mark(client, worker, 1, value=False).status_code == 200
        assert _mark(client, worker2, 1).status_code == 200

    def test_indicator_shows_remaining(
        self, client: TestClient, admin, dept, worker, calendar_2026
    ):
        """AC9: индикатор остатка — сколько смен использовано из скольких."""
        _mark(client, worker, 5)
        _mark(client, worker, 6)
        month = client.get("/api/timesheet/2026/7", headers=_auth(client)).json()
        fund = next(f for f in month["night_funds"] if f["department_id"] == dept.id)
        assert fund["limit_shifts"] == 31
        assert fund["used_shifts"] == 2
        assert fund["remaining_shifts"] == 29
        assert Decimal(fund["fund"]) == FUND
        assert Decimal(fund["rate"]) == Decimal("3225.81")


# ── Расчёт через API ──────────────────────────────────────────────────────────

class TestPayrollEndpoint:
    def test_allowance_in_payroll_and_net_payout(
        self, client: TestClient, admin, worker, calendar_2026
    ):
        """AC7/AC8: надбавка в строке расчёта и в «к выплате»."""
        for d in range(1, 16):
            _mark(client, worker, d)

        payroll = client.get("/api/timesheet/2026/7/payroll", headers=_auth(client)).json()
        row = next(r for r in payroll["employees"] if r["employee_id"] == worker.id)

        assert row["night_shifts"] == 15
        assert Decimal(row["night_rate"]) == Decimal("3225.81")
        assert Decimal(row["night_amount"]) == Decimal("48387")
        assert Decimal(payroll["total_night_amount"]) == Decimal("48387")
        # Надбавка входит в итог начисления, а он — в «к выплате».
        assert Decimal(row["total_amount"]) >= Decimal("48387")

    def test_statement_includes_night(self, client: TestClient, admin, worker, calendar_2026):
        for d in range(1, 4):
            _mark(client, worker, d)
        statement = client.get("/api/timesheet/2026/7/statement", headers=_auth(client)).json()
        row = next(r for r in statement["rows"] if r["employee_id"] == worker.id)
        assert row["night_shifts"] == 3
        assert Decimal(row["night_amount"]) == Decimal("9677")  # 3 × 3225.81 → 9677.43
        assert Decimal(statement["total_night_amount"]) == Decimal(row["night_amount"])


# ── Табельщик: факт отмечает, денег не видит ──────────────────────────────────

class TestTimekeeper:
    def test_timekeeper_marks_night_shift(
        self, client: TestClient, timekeeper, worker, calendar_2026, db_session
    ):
        resp = _mark(client, worker, 3, headers=_tk_auth(client))
        assert resp.status_code == 200
        assert db_session.query(NightShift).count() == 1

    def test_timekeeper_sees_shifts_but_not_money(
        self, client: TestClient, timekeeper, worker, dept, calendar_2026
    ):
        for d in range(1, 6):
            _mark(client, worker, d, headers=_tk_auth(client))

        month = client.get(
            "/api/timesheet/2026/7?include_payroll=true", headers=_tk_auth(client)
        ).json()
        fund = next(f for f in month["night_funds"] if f["department_id"] == dept.id)
        assert fund["used_shifts"] == 5 and fund["remaining_shifts"] == 26
        assert fund["fund"] is None and fund["rate"] is None

        row = next(
            r for r in month["payroll"]["employees"] if r["employee_id"] == worker.id
        )
        assert row["night_shifts"] == 5          # факт выхода виден
        assert row["night_rate"] is None         # цена — нет
        assert Decimal(row["night_amount"]) == Decimal("0")
        assert "3225.81" not in month.__str__()


# ── Период и права ────────────────────────────────────────────────────────────

def test_closed_period_blocks_night_shift(
    client: TestClient, admin, worker, dept, calendar_2026, db_session
):
    from app.models.timesheet_periods import TimesheetPeriod

    db_session.add(TimesheetPeriod(
        department_id=dept.id, year=2026, month=7, status="closed",
    ))
    db_session.commit()
    resp = _mark(client, worker, 3)
    assert resp.status_code == 409
    assert "Период закрыт" in resp.json()["detail"]


def test_employee_cannot_mark_night_shift(
    client: TestClient, admin, worker, calendar_2026, db_session
):
    """Ночную смену отмечает тот, кто ведёт табель, — не сам сотрудник."""
    worker.email = "nightworker@example.com"
    worker.hashed_password = hash_password("worker123")
    worker.role = "employee"
    worker.must_change_password = False
    db_session.commit()

    token = get_token(client, "nightworker@example.com", "worker123")
    resp = _mark(client, worker, 3, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
