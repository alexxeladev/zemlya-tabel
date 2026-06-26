"""Tests for task 3.11b: company % distribution + payroll statement."""
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
from app.services.payroll_statement import distribute_by_percent
from tests.conftest import get_token

MAY_BASIC = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]


# ── Unit: распределение по процентам ──────────────────────────────────────────

class TestDistributeByPercent:
    def test_example_from_task(self):
        """120000 при 50/30/20 → 60000 / 36000 / 24000."""
        result = distribute_by_percent(
            Decimal("120000"), {1: Decimal("50"), 2: Decimal("30"), 3: Decimal("20")}
        )
        assert result[1] == Decimal("60000")
        assert result[2] == Decimal("36000")
        assert result[3] == Decimal("24000")
        assert sum(result.values()) == Decimal("120000")

    def test_sum_matches_total_with_rounding(self):
        """Доли, не делящиеся нацело, всё равно сходятся с итогом."""
        result = distribute_by_percent(
            Decimal("100"), {1: Decimal("33.33"), 2: Decimal("33.33"), 3: Decimal("33.34")}
        )
        assert sum(result.values()) == Decimal("100")

    def test_normalizes_non_100_sum(self):
        """Сумма процентов ≠ 100 — всё равно распределяет всю сумму (нормализация)."""
        shares = {1: Decimal("50"), 2: Decimal("50"), 3: Decimal("50")}
        result = distribute_by_percent(Decimal("100"), shares)
        assert sum(result.values()) == Decimal("100")

    def test_empty_shares(self):
        assert distribute_by_percent(Decimal("1000"), {}) == {}


# ── Integration fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Stmt Dept", code="SD", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def companies(db_session: Session) -> list[Company]:
    cs = [
        Company(code="KMF", name="Комфорт", is_active=True),
        Company(code="ZMO", name="ЗМО", is_active=True),
        Company(code="GHS", name="ГХС", is_active=True),
    ]
    db_session.add_all(cs)
    db_session.commit()
    for c in cs:
        db_session.refresh(c)
    return cs


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(full_name="Stmt Admin", email="stmtadmin@example.com",
                   hashed_password=hash_password("admin123"), role="admin",
                   is_active=True, must_change_password=False, is_system_admin=True)
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session: Session, dept, companies, schedule) -> Employee:
    emp = Employee(full_name="Кладовщик", tab_number="K-1", is_active=True,
                   rate=Decimal("80000"), schedule_id=schedule.id,
                   default_company_id=companies[0].id, department_id=dept.id)
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def calendar(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_BASIC, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


def _full_norm_entries(db: Session, emp_id: int, company_id: int):
    for d in MAY_WORKDAYS:
        db.add(TimesheetEntry(employee_id=emp_id, work_date=date(2026, 5, d),
                              company_id=company_id, hours=8))
    db.commit()


def _h(client, token):
    return {"Authorization": f"Bearer {token}"}


# ── Default shares in employee card ───────────────────────────────────────────

class TestDefaultShares:
    def test_set_and_get_shares(self, client: TestClient, admin, worker, companies):
        token = get_token(client, "stmtadmin@example.com", "admin123")
        hdr = _h(client, token)
        payload = {"shares": [
            {"company_id": companies[0].id, "percent": "50"},
            {"company_id": companies[1].id, "percent": "30"},
            {"company_id": companies[2].id, "percent": "20"},
        ]}
        r = client.put(f"/api/employees/{worker.id}/company-shares", json=payload, headers=hdr)
        assert r.status_code == 200
        data = r.json()
        assert Decimal(data["percent_sum"]) == Decimal("100")
        assert len(data["shares"]) == 3

        g = client.get(f"/api/employees/{worker.id}/company-shares", headers=hdr)
        assert g.status_code == 200
        assert len(g.json()["shares"]) == 3

    def test_reject_sum_not_100(self, client: TestClient, admin, worker, companies):
        token = get_token(client, "stmtadmin@example.com", "admin123")
        payload = {"shares": [
            {"company_id": companies[0].id, "percent": "50"},
            {"company_id": companies[1].id, "percent": "30"},
        ]}
        r = client.put(f"/api/employees/{worker.id}/company-shares", json=payload,
                       headers=_h(client, token))
        assert r.status_code == 422


# ── Statement endpoint ────────────────────────────────────────────────────────

class TestStatement:
    def test_example_distribution(self, client: TestClient, admin, worker, companies,
                                   schedule, calendar, db_session):
        """Итого начислено 80000, 50/30/20 → 40000 / 24000 / 16000."""
        _full_norm_entries(db_session, worker.id, companies[0].id)
        token = get_token(client, "stmtadmin@example.com", "admin123")
        client.put(f"/api/employees/{worker.id}/company-shares", json={"shares": [
            {"company_id": companies[0].id, "percent": "50"},
            {"company_id": companies[1].id, "percent": "30"},
            {"company_id": companies[2].id, "percent": "20"},
        ]}, headers=_h(client, token))

        r = client.get("/api/timesheet/2026/5/statement", headers=_h(client, token))
        assert r.status_code == 200
        row = next(x for x in r.json()["rows"] if x["employee_id"] == worker.id)
        assert Decimal(row["accrued_total"]) == Decimal("80000")
        amounts = {d["company_id"]: Decimal(d["amount"]) for d in row["distribution"]}
        assert amounts[companies[0].id] == Decimal("40000")
        assert amounts[companies[1].id] == Decimal("24000")
        assert amounts[companies[2].id] == Decimal("16000")
        assert Decimal(row["distribution_total"]) == Decimal("80000")

    def test_monthly_override(self, client: TestClient, admin, worker, companies,
                              schedule, calendar, db_session):
        """Переопределение на месяц меняет распределение, не трогая карточку."""
        _full_norm_entries(db_session, worker.id, companies[0].id)
        token = get_token(client, "stmtadmin@example.com", "admin123")
        client.put(f"/api/employees/{worker.id}/company-shares", json={"shares": [
            {"company_id": companies[0].id, "percent": "50"},
            {"company_id": companies[1].id, "percent": "50"},
        ]}, headers=_h(client, token))

        # override: всё на одну компанию
        ov = client.put("/api/timesheet/distribution", json={
            "employee_id": worker.id, "year": 2026, "month": 5,
            "shares": [{"company_id": companies[2].id, "percent": "100"}],
        }, headers=_h(client, token))
        assert ov.status_code == 200

        r = client.get("/api/timesheet/2026/5/statement", headers=_h(client, token))
        row = next(x for x in r.json()["rows"] if x["employee_id"] == worker.id)
        assert row["is_overridden"] is True
        amounts = {d["company_id"]: Decimal(d["amount"]) for d in row["distribution"]}
        assert amounts[companies[2].id] == Decimal("80000")

        # карточка не изменилась
        g = client.get(f"/api/employees/{worker.id}/company-shares", headers=_h(client, token))
        assert len(g.json()["shares"]) == 2

        # удалить override → вернётся дефолт
        d = client.delete(f"/api/timesheet/distribution/{worker.id}/2026/5",
                          headers=_h(client, token))
        assert d.status_code == 204
        r2 = client.get("/api/timesheet/2026/5/statement", headers=_h(client, token))
        row2 = next(x for x in r2.json()["rows"] if x["employee_id"] == worker.id)
        assert row2["is_overridden"] is False

    def test_employee_forbidden(self, client: TestClient, db_session):
        emp = Employee(full_name="E", email="stmtemp@example.com",
                       hashed_password=hash_password("emp12345"), role="employee",
                       is_active=True, must_change_password=False)
        db_session.add(emp)
        db_session.commit()
        token = get_token(client, "stmtemp@example.com", "emp12345")
        r = client.get("/api/timesheet/2026/5/statement", headers=_h(client, token))
        assert r.status_code == 403

    def test_excel_export(self, client: TestClient, admin, worker, companies,
                          schedule, calendar, db_session):
        _full_norm_entries(db_session, worker.id, companies[0].id)
        token = get_token(client, "stmtadmin@example.com", "admin123")
        client.put(f"/api/employees/{worker.id}/company-shares", json={"shares": [
            {"company_id": companies[0].id, "percent": "100"},
        ]}, headers=_h(client, token))
        r = client.get("/api/timesheet/2026/5/statement/export/excel", headers=_h(client, token))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert len(r.content) > 0
