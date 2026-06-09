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
        assert p.holiday_amount == Decimal("0")
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


class TestHolidayHours:
    def test_holiday_hours_get_extra_pay(self):
        """8h on May 1 (holiday) → holiday_amount = 8 * hourly * 0.5 on top of base"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # norm in MAY_WITH_HOLIDAY = 167h; add 8h on May 1 (holiday)
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("8"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.is_calculable is True
        assert p.holiday_hours == Decimal("8")
        hourly = Decimal("80000") / Decimal("167")
        expected_holiday = (Decimal("8") * hourly * Decimal("0.5")).quantize(Decimal("1"))
        assert p.holiday_amount == expected_holiday

    def test_short_day_has_no_holiday_extra(self):
        """May 8 is a short day (not holiday) → no holiday_amount"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, 8), hours=Decimal("7"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.holiday_hours == Decimal("0")
        assert p.holiday_amount == Decimal("0")


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

    def test_shift_schedule(self):
        emp = make_employee(schedule=make_schedule(12, "shift"))
        p = calculate_employee_payroll(emp, [make_entry()], MAY_BASIC, 2026, 5)
        assert p.is_calculable is False
        assert "смен" in (p.reason_if_not_calculable or "").lower()

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

    def test_no_entries_empty_breakdown(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        p = calculate_employee_payroll(emp, [], MAY_BASIC, 2026, 5)
        assert p.total_hours == Decimal("0")
        assert p.base_amount == Decimal("0")
        assert p.breakdown_by_company == []

    def test_holiday_distributed_by_company_holiday_hours(self):
        """All holiday hours in company A → company A gets all holiday_amount"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        # May 1 is holiday, work 8h for company A on May 1
        # Also work 8h for company B on May 2 (non-holiday)
        entries = [
            make_entry(company_id=1, work_date=date(2026, 5, 1), hours=Decimal("8")),  # holiday
            make_entry(company_id=2, work_date=date(2026, 5, 2), hours=Decimal("8")),  # workday
        ]
        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)
        assert p.holiday_hours == Decimal("8")
        bd_a = next(b for b in p.breakdown_by_company if b.company_id == 1)
        bd_b = next(b for b in p.breakdown_by_company if b.company_id == 2)
        assert bd_a.holiday_amount == p.holiday_amount
        assert bd_b.holiday_amount == Decimal("0")


class TestRounding:
    def test_all_amounts_are_whole_rubles(self):
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, d)) for d in MAY_BASIC_WORKDAYS[:5]]

        p = calculate_employee_payroll(emp, entries, MAY_BASIC, 2026, 5)

        for val in [p.base_amount, p.overtime_amount, p.holiday_amount, p.total_amount]:
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
            + Decimal(data["total_holiday_amount"])
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

    def test_holiday_24h_edge_case(self):
        """Holiday with 24 hours → correct calculation without overflow"""
        schedule = make_schedule(8)
        emp = make_employee(schedule=schedule)
        entries = [make_entry(work_date=date(2026, 5, 1), hours=Decimal("24"))]

        p = calculate_employee_payroll(emp, entries, MAY_WITH_HOLIDAY, 2026, 5)

        assert p.is_calculable is True
        assert p.holiday_hours == Decimal("24")
        assert p.holiday_amount >= Decimal("0")
        # Total should be base + overtime + holiday
        assert p.total_amount == p.base_amount + p.overtime_amount + p.holiday_amount
