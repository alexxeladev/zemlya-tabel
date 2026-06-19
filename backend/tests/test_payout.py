"""Tests for task 3.11a: premiums/KPI, loan auto-repayment + override, advance, payout."""
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
from app.services.payout import compute_payout, loan_month_state
from tests.conftest import get_token

# Reuse the May calendar from the payroll test module.
from tests.test_payroll import MAY_BASIC, MAY_BASIC_WORKDAYS


# ── Unit tests: loan_month_state ───────────────────────────────────────────────

class TestLoanSchedule:
    def test_equal_shares(self):
        """12000 / 12 мес = доля 1000 каждый месяц."""
        for i, m in enumerate(range(1, 13), start=0):
            st = loan_month_state(Decimal("12000"), 12, date(2026, 1, 1), 2026, m)
            assert st is not None
            assert st.planned == Decimal("1000")
            assert st.actual == Decimal("1000")
            assert st.remaining_after == Decimal("12000") - Decimal("1000") * (i + 1)
        # после 12 месяцев займ погашен
        assert st.remaining_after == Decimal("0")

    def test_none_before_start(self):
        st = loan_month_state(Decimal("12000"), 12, date(2026, 3, 1), 2026, 2)
        assert st is None

    def test_none_without_loan(self):
        assert loan_month_state(None, 12, date(2026, 1, 1), 2026, 1) is None
        assert loan_month_state(Decimal("12000"), None, date(2026, 1, 1), 2026, 1) is None
        assert loan_month_state(Decimal("12000"), 12, None, 2026, 1) is None

    def test_last_month_remainder_extends(self):
        """1000/3 → доля 333; на 4-й месяц добивается остаток 1 (не больше)."""
        m1 = loan_month_state(Decimal("1000"), 3, date(2026, 1, 1), 2026, 1)
        m4 = loan_month_state(Decimal("1000"), 3, date(2026, 1, 1), 2026, 4)
        assert m1.actual == Decimal("333")
        assert m4.actual == Decimal("1")           # остаток меньше доли
        assert m4.remaining_after == Decimal("0")
        # за пределами — займ закрыт
        m5 = loan_month_state(Decimal("1000"), 3, date(2026, 1, 1), 2026, 5)
        assert m5.active is False
        assert m5.actual == Decimal("0")

    def test_manual_override_slows_repayment(self):
        """Ручная правка месяца 1 на 500 → остаток гасится медленнее, итог сходится."""
        overrides = {(2026, 1): Decimal("500")}
        m1 = loan_month_state(Decimal("12000"), 12, date(2026, 1, 1), 2026, 1, overrides)
        assert m1.actual == Decimal("500")
        assert m1.is_manual is True
        assert m1.remaining_after == Decimal("11500")
        m2 = loan_month_state(Decimal("12000"), 12, date(2026, 1, 1), 2026, 2, overrides)
        assert m2.actual == Decimal("1000")  # обычная доля
        assert m2.remaining_after == Decimal("10500")

    def test_override_cannot_exceed_remaining(self):
        """Досрочное погашение: override больше остатка → удержим только остаток."""
        overrides = {(2026, 1): Decimal("99999")}
        m1 = loan_month_state(Decimal("12000"), 12, date(2026, 1, 1), 2026, 1, overrides)
        assert m1.actual == Decimal("12000")
        assert m1.remaining_after == Decimal("0")
        m2 = loan_month_state(Decimal("12000"), 12, date(2026, 1, 1), 2026, 2, overrides)
        assert m2.active is False
        assert m2.actual == Decimal("0")


class TestComputePayout:
    def test_formula(self):
        r = compute_payout(
            accrued_total=Decimal("100000"),
            premium_amount=Decimal("10000"),
            kpi_amount=Decimal("5000"),
            advance_deduction=Decimal("3000"),
            loan_deduction=Decimal("1000"),
        )
        assert r.total_deductions == Decimal("4000")
        assert r.net_payout == Decimal("111000")

    def test_multiple_premiums_summed_then_applied(self):
        # суммирование делается выше; здесь проверяем что суммы складываются верно
        r = compute_payout(Decimal("50000"), Decimal("3000"), Decimal("0"),
                            Decimal("0"), Decimal("0"))
        assert r.net_payout == Decimal("53000")


# ── Integration fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Pay Dept", code="PD", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="PC", name="Payout Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Admin", email="admin@example.com",
        hashed_password=hash_password("admin123"), role="admin",
        is_active=True, must_change_password=False, is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session: Session, company: Company, schedule: Schedule, dept: Department) -> Employee:
    emp = Employee(
        full_name="Worker", is_active=True, rate=Decimal("100000"),
        schedule_id=schedule.id, default_company_id=company.id, department_id=dept.id,
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


def _fill_full_norm(db: Session, worker: Employee, company: Company) -> None:
    for d in MAY_BASIC_WORKDAYS:
        db.add(TimesheetEntry(employee_id=worker.id, work_date=date(2026, 5, d),
                              company_id=company.id, hours=8))
    db.commit()


def _auth(client, email, pwd):
    return {"Authorization": f"Bearer {get_token(client, email, pwd)}"}


# ── Integration: adjustments ────────────────────────────────────────────────────

class TestAdjustmentsEndpoint:
    def test_create_requires_reason(self, client, admin, worker):
        h = _auth(client, "admin@example.com", "admin123")
        resp = client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "10000", "reason": "  ",
        })
        assert resp.status_code == 422

    def test_create_and_list(self, client, admin, worker):
        h = _auth(client, "admin@example.com", "admin123")
        resp = client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "10000", "reason": "Хорошая работа",
        })
        assert resp.status_code == 201
        lst = client.get("/api/timesheet/2026/5/adjustments", headers=h)
        assert lst.status_code == 200
        assert len(lst.json()) == 1
        assert lst.json()[0]["kind"] == "premium"

    def test_delete(self, client, admin, worker):
        h = _auth(client, "admin@example.com", "admin123")
        adj = client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "kpi", "amount": "5000", "reason": "KPI Q2",
        }).json()
        d = client.delete(f"/api/timesheet/adjustments/{adj['id']}", headers=h)
        assert d.status_code == 204
        lst = client.get("/api/timesheet/2026/5/adjustments", headers=h)
        assert lst.json() == []

    def test_employee_forbidden(self, client, db_session, worker):
        emp = Employee(full_name="Emp", email="emp@example.com",
                       hashed_password=hash_password("emp12345"), role="employee",
                       is_active=True, must_change_password=False)
        db_session.add(emp)
        db_session.commit()
        h = _auth(client, "emp@example.com", "emp12345")
        resp = client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "1000", "reason": "nope",
        })
        assert resp.status_code == 403


# ── Integration: payout end to end (task example) ───────────────────────────────

class TestPayoutEndToEnd:
    def test_task_example(self, client, admin, worker, company, calendar_2026, db_session):
        """Оклад 100000 + премия 10000 + KPI 5000 − (займ 1000 + аванс 3000) = 111000."""
        _fill_full_norm(db_session, worker, company)
        # займ 12000 на 12 мес с мая 2026 → доля 1000
        worker.loan_amount = Decimal("12000")
        worker.loan_term_months = 12
        worker.loan_start_date = date(2026, 5, 1)
        db_session.commit()

        h = _auth(client, "admin@example.com", "admin123")
        client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "10000", "reason": "премия"})
        client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "kpi", "amount": "5000", "reason": "kpi"})
        client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "advance", "amount": "3000", "reason": "аванс"})

        resp = client.get("/api/timesheet/2026/5/payroll", headers=h)
        assert resp.status_code == 200
        emp = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert Decimal(emp["total_amount"]) == Decimal("100000")
        assert Decimal(emp["premium_amount"]) == Decimal("10000")
        assert Decimal(emp["kpi_amount"]) == Decimal("5000")
        assert Decimal(emp["advance_deduction"]) == Decimal("3000")
        assert Decimal(emp["loan_deduction"]) == Decimal("1000")
        assert Decimal(emp["total_deductions"]) == Decimal("4000")
        assert Decimal(emp["net_payout"]) == Decimal("111000")
        assert Decimal(emp["loan_remaining"]) == Decimal("11000")

    def test_loan_override_changes_deduction(self, client, admin, worker, company,
                                             calendar_2026, db_session):
        _fill_full_norm(db_session, worker, company)
        worker.loan_amount = Decimal("12000")
        worker.loan_term_months = 12
        worker.loan_start_date = date(2026, 5, 1)
        db_session.commit()
        h = _auth(client, "admin@example.com", "admin123")

        ovr = client.post("/api/timesheet/loan-override", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5, "actual_amount": "300"})
        assert ovr.status_code == 200

        resp = client.get("/api/timesheet/2026/5/payroll", headers=h)
        emp = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert Decimal(emp["loan_deduction"]) == Decimal("300")
        assert emp["loan_is_manual"] is True
        # остаток гасится медленнее: 12000 − 300 = 11700
        assert Decimal(emp["loan_remaining"]) == Decimal("11700")
        # к выплате = 100000 − 300
        assert Decimal(emp["net_payout"]) == Decimal("99700")

        # снять правку — вернётся плановая доля 1000
        client.delete(f"/api/timesheet/loan-override/{worker.id}/2026/5", headers=h)
        resp = client.get("/api/timesheet/2026/5/payroll", headers=h)
        emp = next(e for e in resp.json()["employees"] if e["employee_id"] == worker.id)
        assert Decimal(emp["loan_deduction"]) == Decimal("1000")

    def test_loan_override_without_loan_422(self, client, admin, worker, calendar_2026):
        h = _auth(client, "admin@example.com", "admin123")
        resp = client.post("/api/timesheet/loan-override", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5, "actual_amount": "100"})
        assert resp.status_code == 422

    def test_summary_net_payout_aggregates(self, client, admin, worker, company,
                                           calendar_2026, db_session):
        _fill_full_norm(db_session, worker, company)
        h = _auth(client, "admin@example.com", "admin123")
        client.post("/api/timesheet/adjustments", headers=h, json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "10000", "reason": "премия"})
        data = client.get("/api/timesheet/2026/5/payroll", headers=h).json()
        assert Decimal(data["total_premium"]) == Decimal("10000")
        assert Decimal(data["total_net_payout"]) == Decimal("110000")
