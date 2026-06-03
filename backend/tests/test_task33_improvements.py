"""Tests for task 3.3: timesheet improvements and employee lifecycle."""
from datetime import date

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
from tests.conftest import get_token

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Test Dept", code="TD", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company_a(db_session: Session) -> Company:
    c = Company(code="CA", name="Company A", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def company_b(db_session: Session) -> Company:
    c = Company(code="CB", name="Company B", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule_standard(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def schedule_shift(db_session: Session) -> Schedule:
    s = Schedule(name="2/2", hours_per_shift=12, schedule_type="shift", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def admin_emp(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Admin",
        email="admin@example.com",
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
def regular_emp(db_session: Session, dept: Department, company_a: Company, schedule_standard: Schedule) -> Employee:
    emp = Employee(
        full_name="Regular Employee",
        email="emp@example.com",
        hashed_password=hash_password("emp123"),
        role="employee",
        is_active=True,
        must_change_password=False,
        department_id=dept.id,
        default_company_id=company_a.id,
        schedule_id=schedule_standard.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def manager_emp(db_session: Session, dept: Department) -> Employee:
    emp = Employee(
        full_name="Manager",
        email="manager@example.com",
        hashed_password=hash_password("manager123"),
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
def calendar_2026(db_session: Session) -> ProductionCalendar:
    # May 2026: 19 work days, some holidays and short days
    # Using a minimal calendar with May data
    data = {
        "year": 2026,
        "months": [
            {
                "month": 5,
                "days": "1,2,3,9,10,16,17,23,24,25,30,31",  # 12 non-working days = 19 work days
            }
        ]
    }
    cal = ProductionCalendar(year=2026, data=data, source="test")
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


@pytest.fixture
def calendar_2026_with_short(db_session: Session) -> ProductionCalendar:
    # May 2026 with a short day (8* = short day)
    data = {
        "year": 2026,
        "months": [
            {
                "month": 5,
                "days": "1,2,3,9,10,16,17,23,24,25,30,31,8*",
            }
        ]
    }
    cal = ProductionCalendar(year=2026, data=data, source="test")
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


# ── Change 1: System admin hidden from timesheet ───────────────────────────────

def test_system_admin_not_visible_in_timesheet(
    client: TestClient, admin_emp: Employee, regular_emp: Employee
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    emp_ids = [e["id"] for e in resp.json()["employees"]]
    assert admin_emp.id not in emp_ids


def test_regular_employee_visible_in_timesheet(
    client: TestClient, admin_emp: Employee, regular_emp: Employee
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    emp_ids = [e["id"] for e in resp.json()["employees"]]
    assert regular_emp.id in emp_ids


# ── Change 2: extra_companies_by_employee ─────────────────────────────────────

def test_extra_companies_empty_when_only_default(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, company_a: Company,
    db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    # Add entry for company_a (which is default)
    db_session.add(TimesheetEntry(employee_id=regular_emp.id, work_date=date(2026, 5, 5),
                                   company_id=company_a.id, hours=8))
    db_session.commit()

    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    extras = resp.json()["extra_companies_by_employee"]
    emp_key = str(regular_emp.id)
    assert emp_key in extras
    assert extras[emp_key] == []


def test_extra_companies_includes_non_default(
    client: TestClient, admin_emp: Employee, regular_emp: Employee,
    company_a: Company, company_b: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    db_session.add(TimesheetEntry(employee_id=regular_emp.id, work_date=date(2026, 5, 5),
                                   company_id=company_a.id, hours=4))
    db_session.add(TimesheetEntry(employee_id=regular_emp.id, work_date=date(2026, 5, 5),
                                   company_id=company_b.id, hours=4))
    db_session.commit()

    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    extras = resp.json()["extra_companies_by_employee"]
    emp_key = str(regular_emp.id)
    assert company_b.id in extras[emp_key]
    assert company_a.id not in extras[emp_key]


def test_extra_companies_null_default_returns_all_with_hours(
    client: TestClient, admin_emp: Employee, company_a: Company, company_b: Company,
    dept: Department, db_session: Session
):
    # Employee with no default company
    emp = Employee(
        full_name="No Default Emp",
        email="nodefault@example.com",
        hashed_password=hash_password("pass123"),
        role="employee",
        is_active=True,
        must_change_password=False,
        department_id=dept.id,
        default_company_id=None,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)

    db_session.add(TimesheetEntry(employee_id=emp.id, work_date=date(2026, 5, 5),
                                   company_id=company_a.id, hours=4))
    db_session.add(TimesheetEntry(employee_id=emp.id, work_date=date(2026, 5, 5),
                                   company_id=company_b.id, hours=4))
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    extras = resp.json()["extra_companies_by_employee"]
    emp_key = str(emp.id)
    assert company_a.id in extras[emp_key]
    assert company_b.id in extras[emp_key]


# ── Change 3: Autofill ────────────────────────────────────────────────────────

def test_autofill_preview_no_db_changes(
    client: TestClient, admin_emp: Employee, regular_emp: Employee,
    calendar_2026: ProductionCalendar, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    # No entries should be in DB
    count = db_session.query(TimesheetEntry).count()
    assert count == 0


def test_autofill_preview_correct_entries(
    client: TestClient, admin_emp: Employee, regular_emp: Employee,
    calendar_2026: ProductionCalendar
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["employees_processed"] == 1
    assert len(data["entries_to_create"]) == 19  # 19 work days in may 2026


def test_autofill_apply_creates_entries(
    client: TestClient, admin_emp: Employee, regular_emp: Employee,
    calendar_2026: ProductionCalendar, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/apply",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries_created"] == 19
    count = db_session.query(TimesheetEntry).count()
    assert count == 19


def test_autofill_short_days_minus_one_hour(
    client: TestClient, admin_emp: Employee, regular_emp: Employee,
    calendar_2026_with_short: ProductionCalendar, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    entries = resp.json()["entries_to_create"]
    # Day 8 is a short day → 7 hours, rest → 8 hours
    short_entries = [e for e in entries if e["work_date"] == "2026-05-08"]
    assert len(short_entries) == 1
    assert float(short_entries[0]["hours"]) == 7.0
    normal_entries = [e for e in entries if e["work_date"] != "2026-05-08"]
    assert all(float(e["hours"]) == 8.0 for e in normal_entries)


def test_autofill_does_not_overwrite_existing_cells(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, company_a: Company,
    calendar_2026: ProductionCalendar, db_session: Session
):
    # Pre-set a cell with 10 hours
    db_session.add(TimesheetEntry(employee_id=regular_emp.id, work_date=date(2026, 5, 5),
                                   company_id=company_a.id, hours=10))
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/apply",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries_created"] == 18  # 19 - 1 existing

    # The existing cell is untouched
    entry = db_session.query(TimesheetEntry).filter_by(
        employee_id=regular_emp.id, work_date=date(2026, 5, 5)
    ).first()
    assert float(entry.hours) == 10.0


def test_autofill_skip_employee_no_default_company(
    client: TestClient, admin_emp: Employee, calendar_2026: ProductionCalendar,
    dept: Department, schedule_standard: Schedule, db_session: Session
):
    emp_no_company = Employee(
        full_name="No Company Emp",
        is_active=True, department_id=dept.id,
        schedule_id=schedule_standard.id, default_company_id=None,
    )
    db_session.add(emp_no_company)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    skipped = data["employees_skipped"]
    skipped_ids = [s["employee_id"] for s in skipped]
    assert emp_no_company.id in skipped_ids


def test_autofill_skip_employee_no_schedule(
    client: TestClient, admin_emp: Employee, calendar_2026: ProductionCalendar,
    dept: Department, company_a: Company, db_session: Session
):
    emp_no_sched = Employee(
        full_name="No Schedule Emp",
        is_active=True, department_id=dept.id,
        schedule_id=None, default_company_id=company_a.id,
    )
    db_session.add(emp_no_sched)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    skipped_ids = [s["employee_id"] for s in resp.json()["employees_skipped"]]
    assert emp_no_sched.id in skipped_ids


def test_autofill_skip_shift_schedule(
    client: TestClient, admin_emp: Employee, calendar_2026: ProductionCalendar,
    dept: Department, company_a: Company, schedule_shift: Schedule, db_session: Session
):
    emp_shift = Employee(
        full_name="Shift Emp",
        is_active=True, department_id=dept.id,
        schedule_id=schedule_shift.id, default_company_id=company_a.id,
    )
    db_session.add(emp_shift)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    skipped = resp.json()["employees_skipped"]
    shift_skipped = [s for s in skipped if s["employee_id"] == emp_shift.id]
    assert len(shift_skipped) == 1
    assert "сменной логикой" in shift_skipped[0]["reason"]


def test_autofill_no_calendar_returns_422(
    client: TestClient, admin_emp: Employee, regular_emp: Employee
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2027, "month": 5},  # no calendar for 2027
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_autofill_closed_period_422(
    client: TestClient, admin_emp: Employee, regular_emp: Employee,
    calendar_2026: ProductionCalendar, db_session: Session
):
    from app.models.timesheet_periods import TimesheetPeriod
    from datetime import datetime
    period = TimesheetPeriod(
        department_id=regular_emp.department_id,
        year=2026, month=5, status="closed",
        closed_at=datetime.utcnow(), closed_by_id=admin_emp.id,
    )
    db_session.add(period)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_autofill_manager_cannot_autofill_other_dept(
    client: TestClient, manager_emp: Employee, calendar_2026: ProductionCalendar,
    db_session: Session
):
    other_dept = Department(name="Other", code="OD", is_active=True)
    db_session.add(other_dept)
    db_session.commit()

    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5, "department_id": other_dept.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Change 4: Dismiss / Rehire ────────────────────────────────────────────────

def test_dismiss_employee(
    client: TestClient, admin_emp: Employee, regular_emp: Employee
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{regular_emp.id}/dismiss",
        json={"dismissal_date": "2026-06-15"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["dismissal_date"] == "2026-06-15"
    assert data["status"] == "dismissed"


def test_rehire_employee(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    # First dismiss
    regular_emp.is_active = False
    regular_emp.dismissal_date = date(2026, 6, 15)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{regular_emp.id}/rehire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is True
    assert data["dismissal_date"] is None
    assert data["status"] == "active"


def test_dismiss_system_admin_forbidden(
    client: TestClient, admin_emp: Employee
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{admin_emp.id}/dismiss",
        json={"dismissal_date": "2026-06-15"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_dismiss_already_dismissed_422(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    regular_emp.is_active = False
    regular_emp.dismissal_date = date(2026, 6, 15)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{regular_emp.id}/dismiss",
        json={"dismissal_date": "2026-07-01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_rehire_active_employee_422(
    client: TestClient, admin_emp: Employee, regular_emp: Employee
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{regular_emp.id}/rehire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_dismissed_employee_cannot_login(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    regular_emp.is_active = False
    regular_emp.dismissal_date = date(2026, 6, 15)
    db_session.commit()

    resp = client.post("/api/auth/login", json={"email": "emp@example.com", "password": "emp123"})
    assert resp.status_code == 403


def test_dismissed_in_timesheet_before_dismissal_date(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    # Dismiss on June 15 — should appear in May (before dismissal)
    regular_emp.is_active = False
    regular_emp.dismissal_date = date(2026, 6, 15)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    emp_ids = [e["id"] for e in resp.json()["employees"]]
    assert regular_emp.id in emp_ids


def test_dismissed_not_in_timesheet_after_dismissal_date(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    # Dismiss on June 15 — should NOT appear in July
    regular_emp.is_active = False
    regular_emp.dismissal_date = date(2026, 6, 15)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/timesheet/2026/7", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    emp_ids = [e["id"] for e in resp.json()["employees"]]
    assert regular_emp.id not in emp_ids


def test_rehired_appears_in_current_period(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    # Dismiss then rehire
    regular_emp.is_active = False
    regular_emp.dismissal_date = date(2026, 1, 1)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    # Rehire
    client.post(
        f"/api/employees/{regular_emp.id}/rehire",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Should now appear in June
    resp = client.get("/api/timesheet/2026/6", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    emp_ids = [e["id"] for e in resp.json()["employees"]]
    assert regular_emp.id in emp_ids


def test_dismiss_audit_log(
    client: TestClient, admin_emp: Employee, regular_emp: Employee, db_session: Session
):
    from app.models.audit_log import AuditLog
    token = get_token(client, "admin@example.com", "admin123")
    client.post(
        f"/api/employees/{regular_emp.id}/dismiss",
        json={"dismissal_date": "2026-06-15"},
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(
        entity_type="employee", action="employee_dismissed"
    ).first()
    assert log is not None
    assert log.after["dismissal_date"] == "2026-06-15"
