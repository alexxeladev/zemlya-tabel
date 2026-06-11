"""Tests for timesheet endpoints."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from tests.conftest import get_token

WORK_DATE = "2026-05-05"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def dept_a(db_session: Session) -> Department:
    d = Department(name="Dept A", code="DA", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def dept_b(db_session: Session) -> Department:
    d = Department(name="Dept B", code="DB", is_active=True)
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
def admin_user(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Admin User",
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
def manager_user(db_session: Session, dept_a: Department) -> Employee:
    emp = Employee(
        full_name="Manager User",
        email="manager@example.com",
        hashed_password=hash_password("manager123"),
        role="manager",
        is_active=True,
        must_change_password=False,
        department_id=dept_a.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def employee_user(db_session: Session, dept_a: Department) -> Employee:
    emp = Employee(
        full_name="Employee User",
        email="employee@example.com",
        hashed_password=hash_password("employee123"),
        role="employee",
        is_active=True,
        must_change_password=False,
        department_id=dept_a.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def employee_other_dept(db_session: Session, dept_b: Department) -> Employee:
    emp = Employee(
        full_name="Other Dept Employee",
        email="other@example.com",
        hashed_password=hash_password("other123"),
        role="employee",
        is_active=True,
        must_change_password=False,
        department_id=dept_b.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def _cell(employee_id: int, company_id: int, hours: float = 8.0, work_date: str = WORK_DATE) -> dict:
    return {
        "employee_id": employee_id,
        "work_date": work_date,
        "company_id": company_id,
        "hours": hours,
    }


# ── Access tests ──────────────────────────────────────────────────────────────

def test_admin_saves_any_employee(
    client: TestClient, admin_user: Employee, employee_user: Employee, company_a: Company
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hours"] == 8


def test_fractional_hours_rejected(
    client: TestClient, admin_user: Employee, employee_user: Employee, company_a: Company
):
    """Часы только целые — дробное значение отвергается валидатором (422)."""
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, hours=4.5),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_manager_saves_own_dept(
    client: TestClient, manager_user: Employee, employee_user: Employee, company_a: Company
):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_manager_cannot_save_other_dept(
    client: TestClient, manager_user: Employee, employee_other_dept: Employee, company_a: Company
):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_other_dept.id, company_a.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_employee_saves_self(
    client: TestClient, employee_user: Employee, company_a: Company
):
    token = get_token(client, "employee@example.com", "employee123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_employee_cannot_save_other(
    client: TestClient, employee_user: Employee, employee_other_dept: Employee, company_a: Company
):
    token = get_token(client, "employee@example.com", "employee123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_other_dept.id, company_a.id),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_unauthenticated_returns_401(client: TestClient, employee_user: Employee, company_a: Company):
    resp = client.put("/api/timesheet/cell", json=_cell(employee_user.id, company_a.id))
    assert resp.status_code == 401


# ── Storage logic ─────────────────────────────────────────────────────────────

def test_create_cell(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    entry = db_session.query(TimesheetEntry).filter_by(
        employee_id=employee_user.id, company_id=company_a.id
    ).first()
    assert entry is not None
    assert float(entry.hours) == 8.0


def test_update_cell(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 4.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    entries = db_session.query(TimesheetEntry).filter_by(
        employee_id=employee_user.id, company_id=company_a.id
    ).all()
    assert len(entries) == 1
    assert float(entries[0].hours) == 4.0


def test_delete_cell_with_zero(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 0.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() is None
    entry = db_session.query(TimesheetEntry).filter_by(
        employee_id=employee_user.id, company_id=company_a.id
    ).first()
    assert entry is None


def test_multiple_companies_same_day(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, company_b: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 4.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_b.id, 4.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    entries = db_session.query(TimesheetEntry).filter_by(employee_id=employee_user.id).all()
    assert len(entries) == 2
    total = sum(float(e.hours) for e in entries)
    assert total == 8.0


# ── Validation ────────────────────────────────────────────────────────────────

def test_hours_above_24_rejected(client: TestClient, admin_user: Employee, employee_user: Employee, company_a: Company):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 25.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_negative_hours_rejected(client: TestClient, admin_user: Employee, employee_user: Employee, company_a: Company):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, -1.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_nonexistent_employee_returns_404(client: TestClient, admin_user: Employee, company_a: Company):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(99999, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_nonexistent_company_returns_404(client: TestClient, admin_user: Employee, employee_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, 99999, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── GET month ─────────────────────────────────────────────────────────────────

def test_get_month_returns_entries(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company
):
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0, "2026-05-05"),
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] == 2026
    assert data["month"] == 5
    assert len(data["entries"]) == 1
    assert data["entries"][0]["hours"] == 8


def test_manager_sees_only_own_dept(
    client: TestClient, manager_user: Employee, employee_user: Employee,
    employee_other_dept: Employee, company_a: Company, admin_user: Employee
):
    admin_token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_other_dept.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    manager_token = get_token(client, "manager@example.com", "manager123")
    resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200
    data = resp.json()
    emp_ids = [e["id"] for e in data["employees"]]
    assert employee_other_dept.id not in emp_ids


def test_admin_department_filter(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    employee_other_dept: Employee, dept_a: Department, company_a: Company
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get(
        f"/api/timesheet/2026/5?department_id={dept_a.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    emp_ids = [e["id"] for e in resp.json()["employees"]]
    assert employee_user.id in emp_ids
    assert employee_other_dept.id not in emp_ids


# ── Audit log ─────────────────────────────────────────────────────────────────

def test_audit_log_on_create(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    from app.models.audit_log import AuditLog
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(entity_type="timesheet_entry", action="create").first()
    assert log is not None
    assert float(log.after["hours"]) == 8.0


def test_audit_log_on_update(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    from app.models.audit_log import AuditLog
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 4.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(entity_type="timesheet_entry", action="update").first()
    assert log is not None
    assert float(log.before["hours"]) == 8.0
    assert float(log.after["hours"]) == 4.0


def test_audit_log_on_delete(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    from app.models.audit_log import AuditLog
    token = get_token(client, "admin@example.com", "admin123")
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 8.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    client.put(
        "/api/timesheet/cell",
        json=_cell(employee_user.id, company_a.id, 0.0),
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(entity_type="timesheet_entry", action="delete").first()
    assert log is not None


# ── Batch ─────────────────────────────────────────────────────────────────────

def test_batch_saves_all(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, company_b: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/cells/batch",
        json={"entries": [
            _cell(employee_user.id, company_a.id, 4.0, "2026-05-05"),
            _cell(employee_user.id, company_b.id, 4.0, "2026-05-05"),
            _cell(employee_user.id, company_a.id, 8.0, "2026-05-06"),
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["entries"]) == 3
    total = db_session.query(TimesheetEntry).filter_by(employee_id=employee_user.id).count()
    assert total == 3


def test_batch_rollback_on_invalid(
    client: TestClient, admin_user: Employee, employee_user: Employee,
    company_a: Company, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/timesheet/cells/batch",
        json={"entries": [
            _cell(employee_user.id, company_a.id, 4.0),
            _cell(99999, company_a.id, 4.0),  # non-existent employee
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    total = db_session.query(TimesheetEntry).count()
    assert total == 0
