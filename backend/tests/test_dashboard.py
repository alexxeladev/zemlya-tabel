"""Tests for task 4.1: dashboard aggregation endpoint and role visibility."""
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
from app.models.timesheet_periods import TimesheetPeriod
from tests.conftest import get_token

# Май 2026, выходные: 3,4,10,11,17,18,24,25,31 → 22 рабочих дня, норма 8h = 176
MAY_BASIC = {
    "year": 2026,
    "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}],
}


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def dept1(db_session: Session) -> Department:
    d = Department(name="Dash Dept One", code="DD1", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def dept2(db_session: Session) -> Department:
    d = Department(name="Dash Dept Two", code="DD2", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company1(db_session: Session) -> Company:
    c = Company(code="DC1", name="Dash Co 1", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def company2(db_session: Session) -> Company:
    c = Company(code="DC2", name="Dash Co 2", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule8(db_session: Session) -> Schedule:
    s = Schedule(name="5/2-dash", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def dash_admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Dash Admin", email="dashadmin@example.com",
        hashed_password=hash_password("admin123"), role="admin",
        is_active=True, must_change_password=False, is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def dash_accountant(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Dash Accountant", email="dashacct@example.com",
        hashed_password=hash_password("acct123"), role="accountant",
        is_active=True, must_change_password=False,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def dash_manager(db_session: Session, dept1: Department) -> Employee:
    emp = Employee(
        full_name="Dash Manager", email="dashmgr@example.com",
        hashed_password=hash_password("mgr123"), role="manager",
        is_active=True, must_change_password=False, department_id=dept1.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker1(db_session: Session, dept1: Department, company1: Company,
            schedule8: Schedule) -> Employee:
    """Сотрудник отдела 1 с доступом employee (для теста employee-виджета)."""
    emp = Employee(
        full_name="Dash Worker One", email="dashworker@example.com",
        hashed_password=hash_password("work123"), role="employee",
        is_active=True, must_change_password=False,
        department_id=dept1.id, default_company_id=company1.id,
        schedule_id=schedule8.id, rate=Decimal("80000"),
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker2(db_session: Session, dept2: Department, company2: Company,
            schedule8: Schedule) -> Employee:
    emp = Employee(
        full_name="Dash Worker Two", is_active=True,
        department_id=dept2.id, default_company_id=company2.id,
        schedule_id=schedule8.id, rate=Decimal("44000"),
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


@pytest.fixture
def may_entries(db_session: Session, worker1: Employee, worker2: Employee,
                company1: Company, company2: Company) -> None:
    """worker1: 8h×2 дня (5, 6 мая). worker2: 10h 6 мая → 2h переработки."""
    for day, hours, emp, comp in [
        (5, 8, worker1, company1),
        (6, 8, worker1, company1),
        (6, 10, worker2, company2),
    ]:
        db_session.add(TimesheetEntry(
            employee_id=emp.id, work_date=date(2026, 5, day),
            company_id=comp.id, hours=hours,
        ))
    db_session.commit()


def _get(client: TestClient, token: str, url: str = "/api/dashboard/2026/5"):
    return client.get(url, headers={"Authorization": f"Bearer {token}"})


# ── Aggregation correctness ────────────────────────────────────────────────────

class TestDashboardAggregation:
    def test_admin_hours_and_overtime(self, client, dash_admin, worker1, worker2,
                                      calendar_2026, may_entries):
        token = get_token(client, "dashadmin@example.com", "admin123")
        resp = _get(client, token)
        assert resp.status_code == 200
        data = resp.json()

        assert Decimal(data["hours"]["total_hours"]) == Decimal("26")  # 16 + 10
        assert Decimal(data["hours"]["overtime_hours"]) == Decimal("2")
        # Норма: worker1 + worker2 со standard-графиком → 176 × 2
        assert Decimal(data["hours"]["norm_hours"]) == Decimal("352")
        assert data["hours"]["percent_of_norm"] is not None

        by_dept = {d["department_name"]: d for d in data["hours_by_department"]}
        assert Decimal(by_dept["Dash Dept One"]["total_hours"]) == Decimal("16")
        assert Decimal(by_dept["Dash Dept Two"]["total_hours"]) == Decimal("10")
        assert Decimal(by_dept["Dash Dept Two"]["overtime_hours"]) == Decimal("2")

    def test_dashboard_payroll_matches_payroll_endpoint(self, client, dash_admin, worker1,
                                                        worker2, calendar_2026, may_entries):
        """Дашборд обязан показывать те же деньги, что страница расчёта ЗП."""
        token = get_token(client, "dashadmin@example.com", "admin123")
        dash = _get(client, token).json()
        payroll = client.get("/api/timesheet/2026/5/payroll",
                             headers={"Authorization": f"Bearer {token}"}).json()

        assert Decimal(dash["payroll"]["total"]) == Decimal(payroll["grand_total"])
        assert Decimal(dash["payroll"]["base"]) == Decimal(payroll["total_base_amount"])
        assert Decimal(dash["payroll"]["overtime"]) == Decimal(payroll["total_overtime_amount"])
        assert Decimal(dash["payroll"]["holiday"]) == Decimal(payroll["total_holiday_amount"])

    def test_payroll_by_company_sums_to_total(self, client, dash_admin, worker1, worker2,
                                              calendar_2026, may_entries):
        token = get_token(client, "dashadmin@example.com", "admin123")
        data = _get(client, token).json()
        by_company = sum(Decimal(c["total"]) for c in data["payroll_by_company"])
        assert by_company == Decimal(data["payroll"]["total"])

    def test_trend_last_point_is_current_month(self, client, dash_admin, worker1, worker2,
                                               calendar_2026, may_entries):
        token = get_token(client, "dashadmin@example.com", "admin123")
        data = _get(client, token).json()
        assert len(data["trend"]) >= 1
        last = data["trend"][-1]
        assert (last["year"], last["month"]) == (2026, 5)
        assert Decimal(last["total_hours"]) == Decimal("26")

    def test_empty_month_returns_zeroes(self, client, dash_admin, calendar_2026):
        token = get_token(client, "dashadmin@example.com", "admin123")
        resp = _get(client, token, "/api/dashboard/2026/1")
        assert resp.status_code == 200
        data = resp.json()
        assert Decimal(data["hours"]["total_hours"]) == Decimal("0")
        assert len(data["trend"]) >= 1  # не падает без истории


# ── Periods block ──────────────────────────────────────────────────────────────

class TestDashboardPeriods:
    def test_status_counts(self, client, db_session, dash_admin, dash_accountant,
                           dept1, dept2, calendar_2026):
        db_session.add(TimesheetPeriod(
            department_id=dept1.id, year=2026, month=5, status="closed",
            closed_by_id=dash_admin.id,
        ))
        db_session.commit()

        token = get_token(client, "dashadmin@example.com", "admin123")
        data = _get(client, token).json()
        counts = data["periods"]["counts"]
        # dept1 closed; dept2 без периода → draft; accountant без отдела → группа «Без отдела»
        assert counts["closed"] == 1
        assert counts["draft"] == 2
        assert counts["pending_review"] == 0

        rows = {r["department_name"]: r for r in data["periods"]["rows"]}
        assert rows["Dash Dept One"]["status"] == "closed"
        assert rows["Dash Dept Two"]["status"] == "draft"
        assert rows["Dash Dept Two"]["period_id"] is None  # lazy — ещё не создан
        assert "Без отдела" in rows

    def test_overdue_detection(self, client, db_session, dash_admin, dept1, calendar_2026):
        db_session.add(TimesheetPeriod(
            department_id=dept1.id, year=2026, month=4, status="draft",
        ))
        db_session.commit()

        token = get_token(client, "dashadmin@example.com", "admin123")
        data = _get(client, token).json()
        assert data["periods"]["counts"]["overdue"] == 1
        row = data["periods"]["overdue_rows"][0]
        assert row["department_id"] == dept1.id
        assert (row["year"], row["month"]) == (2026, 4)
        assert row["is_overdue"] is True

    def test_closed_past_period_not_overdue(self, client, db_session, dash_admin,
                                            dept1, calendar_2026):
        db_session.add(TimesheetPeriod(
            department_id=dept1.id, year=2026, month=4, status="closed",
        ))
        db_session.commit()
        token = get_token(client, "dashadmin@example.com", "admin123")
        data = _get(client, token).json()
        assert data["periods"]["counts"]["overdue"] == 0


# ── Role visibility ────────────────────────────────────────────────────────────

class TestDashboardRoles:
    def test_manager_sees_only_own_department(self, client, dash_manager, worker1, worker2,
                                              calendar_2026, may_entries):
        token = get_token(client, "dashmgr@example.com", "mgr123")
        data = _get(client, token).json()

        dept_names = [d["department_name"] for d in data["hours_by_department"]]
        assert dept_names == ["Dash Dept One"]
        assert Decimal(data["hours"]["total_hours"]) == Decimal("16")  # без worker2

        pay_depts = [d["department_name"] for d in data["payroll_by_department"]]
        assert pay_depts == ["Dash Dept One"]

        period_depts = {r["department_name"] for r in data["periods"]["rows"]}
        assert period_depts == {"Dash Dept One"}

    def test_manager_overdue_only_own_department(self, client, db_session, dash_manager,
                                                 dept1, dept2, calendar_2026):
        db_session.add(TimesheetPeriod(department_id=dept1.id, year=2026, month=4,
                                       status="draft"))
        db_session.add(TimesheetPeriod(department_id=dept2.id, year=2026, month=4,
                                       status="pending_review"))
        db_session.commit()

        token = get_token(client, "dashmgr@example.com", "mgr123")
        data = _get(client, token).json()
        assert data["periods"]["counts"]["overdue"] == 1
        assert data["periods"]["overdue_rows"][0]["department_id"] == dept1.id

    def test_employee_gets_own_hours_and_no_finance(self, client, worker1, worker2,
                                                    calendar_2026, may_entries):
        token = get_token(client, "dashworker@example.com", "work123")
        data = _get(client, token).json()

        assert Decimal(data["hours"]["total_hours"]) == Decimal("16")  # только свои
        assert Decimal(data["hours"]["norm_hours"]) == Decimal("176")
        assert data["payroll"] is None
        assert data["payroll_by_department"] == []
        assert data["payroll_by_company"] == []
        assert data["periods"] is None
        assert data["hours_by_department"] == []
        for point in data["trend"]:
            assert point["payroll_total"] is None

    def test_accountant_sees_all(self, client, dash_accountant, worker1, worker2,
                                 calendar_2026, may_entries):
        token = get_token(client, "dashacct@example.com", "acct123")
        data = _get(client, token).json()
        assert data["payroll"] is not None
        names = {d["department_name"] for d in data["hours_by_department"]}
        assert {"Dash Dept One", "Dash Dept Two"} <= names

    def test_unauthenticated_401(self, client):
        resp = client.get("/api/dashboard/2026/5")
        assert resp.status_code == 401

    def test_invalid_month_422(self, client, dash_admin):
        token = get_token(client, "dashadmin@example.com", "admin123")
        resp = _get(client, token, "/api/dashboard/2026/13")
        assert resp.status_code == 422
