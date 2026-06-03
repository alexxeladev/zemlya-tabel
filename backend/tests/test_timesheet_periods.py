"""Tests for timesheet period workflow."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.timesheet_periods import TimesheetPeriod
from tests.conftest import get_token

YEAR, MONTH = 2026, 5
WORK_DATE = f"{YEAR}-{MONTH:02d}-05"


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
def company(db_session: Session) -> Company:
    c = Company(code="CA", name="Company A", is_active=True)
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
        is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def accountant_user(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Accountant User",
        email="accountant@example.com",
        hashed_password=hash_password("acc123456"),
        role="accountant",
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def manager_a(db_session: Session, dept_a: Department) -> Employee:
    emp = Employee(
        full_name="Manager A",
        email="manager_a@example.com",
        hashed_password=hash_password("mgr123456"),
        role="manager",
        is_active=True,
        department_id=dept_a.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def manager_b(db_session: Session, dept_b: Department) -> Employee:
    emp = Employee(
        full_name="Manager B",
        email="manager_b@example.com",
        hashed_password=hash_password("mgr123456"),
        role="manager",
        is_active=True,
        department_id=dept_b.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def employee_a(db_session: Session, dept_a: Department) -> Employee:
    emp = Employee(
        full_name="Employee A",
        email="emp_a@example.com",
        hashed_password=hash_password("emp123456"),
        role="employee",
        is_active=True,
        department_id=dept_a.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def employee_no_dept(db_session: Session) -> Employee:
    """Employee without a department (top-level / null department)."""
    emp = Employee(
        full_name="No Dept Employee",
        email="nodept@example.com",
        hashed_password=hash_password("nodept123"),
        role="employee",
        is_active=True,
        department_id=None,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def draft_period_a(db_session: Session, dept_a: Department) -> TimesheetPeriod:
    p = TimesheetPeriod(department_id=dept_a.id, year=YEAR, month=MONTH, status="draft")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def pending_period_a(db_session: Session, dept_a: Department, manager_a: Employee) -> TimesheetPeriod:
    from datetime import datetime
    p = TimesheetPeriod(
        department_id=dept_a.id, year=YEAR, month=MONTH, status="pending_review",
        submitted_at=datetime.utcnow(), submitted_by_id=manager_a.id,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def closed_period_a(db_session: Session, dept_a: Department, accountant_user: Employee) -> TimesheetPeriod:
    from datetime import datetime
    p = TimesheetPeriod(
        department_id=dept_a.id, year=YEAR, month=MONTH, status="closed",
        closed_at=datetime.utcnow(), closed_by_id=accountant_user.id,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def null_draft_period(db_session: Session) -> TimesheetPeriod:
    p = TimesheetPeriod(department_id=None, year=YEAR, month=MONTH, status="draft")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


# ── Auto-creation ─────────────────────────────────────────────────────────────

def test_period_auto_created_on_get_month(
    client: TestClient, admin_user: Employee, employee_a: Employee,
    company: Company, dept_a: Department, db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get(f"/api/timesheet/{YEAR}/{MONTH}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    periods = resp.json()["periods"]
    assert len(periods) >= 1
    dept_ids = {p["department_id"] for p in periods}
    assert dept_a.id in dept_ids or None in dept_ids  # employee_a has dept_a, admin has None


def test_null_dept_period_created_separately(
    client: TestClient, admin_user: Employee, db_session: Session
):
    """An employee with no department triggers creation of null-department period."""
    # System admins are hidden from timesheet; need a regular employee with null dept
    from app.core.security import hash_password as _hp
    nodept_emp = Employee(
        full_name="No Dept Employee",
        email="nodept@example.com",
        hashed_password=_hp("nodept123"),
        role="employee",
        is_active=True,
        is_system_admin=False,
        department_id=None,
    )
    db_session.add(nodept_emp)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get(f"/api/timesheet/{YEAR}/{MONTH}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    periods = resp.json()["periods"]
    null_periods = [p for p in periods if p["department_id"] is None]
    assert len(null_periods) == 1


# ── Submit workflow ───────────────────────────────────────────────────────────

def test_manager_submits_own_dept(
    client: TestClient, manager_a: Employee, draft_period_a: TimesheetPeriod
):
    token = get_token(client, "manager_a@example.com", "mgr123456")
    resp = client.post(
        f"/api/timesheet/periods/{draft_period_a.id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending_review"
    assert data["submitted_by_name"] == "Manager A"


def test_manager_cannot_submit_other_dept(
    client: TestClient, manager_b: Employee, draft_period_a: TimesheetPeriod
):
    token = get_token(client, "manager_b@example.com", "mgr123456")
    resp = client.post(
        f"/api/timesheet/periods/{draft_period_a.id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_cannot_submit_null_dept_period(
    client: TestClient, admin_user: Employee, null_draft_period: TimesheetPeriod
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/timesheet/periods/{null_draft_period.id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_submit_wrong_status(
    client: TestClient, manager_a: Employee, pending_period_a: TimesheetPeriod
):
    """Cannot submit a period that's already pending_review."""
    token = get_token(client, "manager_a@example.com", "mgr123456")
    resp = client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ── Close workflow ────────────────────────────────────────────────────────────

def test_accountant_closes_pending_period(
    client: TestClient, accountant_user: Employee, pending_period_a: TimesheetPeriod
):
    token = get_token(client, "accountant@example.com", "acc123456")
    resp = client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def test_accountant_cannot_close_draft_period(
    client: TestClient, accountant_user: Employee, draft_period_a: TimesheetPeriod
):
    token = get_token(client, "accountant@example.com", "acc123456")
    resp = client.post(
        f"/api/timesheet/periods/{draft_period_a.id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_null_dept_closes_from_draft(
    client: TestClient, accountant_user: Employee, null_draft_period: TimesheetPeriod
):
    """NULL-department period can be closed directly from draft."""
    token = get_token(client, "accountant@example.com", "acc123456")
    resp = client.post(
        f"/api/timesheet/periods/{null_draft_period.id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def test_manager_cannot_close(
    client: TestClient, manager_a: Employee, pending_period_a: TimesheetPeriod
):
    token = get_token(client, "manager_a@example.com", "mgr123456")
    resp = client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Return to draft ───────────────────────────────────────────────────────────

def test_accountant_returns_to_draft(
    client: TestClient, accountant_user: Employee, pending_period_a: TimesheetPeriod
):
    token = get_token(client, "accountant@example.com", "acc123456")
    resp = client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/return",
        json={"reason": "Не сошёлся итог"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"


def test_return_requires_reason(
    client: TestClient, accountant_user: Employee, pending_period_a: TimesheetPeriod
):
    token = get_token(client, "accountant@example.com", "acc123456")
    resp = client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/return",
        json={"reason": "ab"},  # too short
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_manager_cannot_return(
    client: TestClient, manager_a: Employee, pending_period_a: TimesheetPeriod
):
    token = get_token(client, "manager_a@example.com", "mgr123456")
    resp = client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/return",
        json={"reason": "Возвращаю доработку"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Reopen ────────────────────────────────────────────────────────────────────

def test_admin_reopens_closed(
    client: TestClient, admin_user: Employee, closed_period_a: TimesheetPeriod
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/timesheet/periods/{closed_period_a.id}/reopen",
        json={"reason": "Обнаружена ошибка"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"


def test_accountant_cannot_reopen(
    client: TestClient, accountant_user: Employee, closed_period_a: TimesheetPeriod
):
    token = get_token(client, "accountant@example.com", "acc123456")
    resp = client.post(
        f"/api/timesheet/periods/{closed_period_a.id}/reopen",
        json={"reason": "Хочу переоткрыть"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_reopen_requires_reason(
    client: TestClient, admin_user: Employee, closed_period_a: TimesheetPeriod
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/timesheet/periods/{closed_period_a.id}/reopen",
        json={"reason": "ab"},  # too short
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ── Cell protection ───────────────────────────────────────────────────────────

def test_cannot_edit_cell_in_pending_review(
    client: TestClient, admin_user: Employee, employee_a: Employee,
    company: Company, pending_period_a: TimesheetPeriod
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json={"employee_id": employee_a.id, "work_date": WORK_DATE, "company_id": company.id, "hours": 8},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert "pending_review" in resp.json()["detail"]


def test_cannot_edit_cell_in_closed_even_admin(
    client: TestClient, admin_user: Employee, employee_a: Employee,
    company: Company, closed_period_a: TimesheetPeriod
):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        "/api/timesheet/cell",
        json={"employee_id": employee_a.id, "work_date": WORK_DATE, "company_id": company.id, "hours": 8},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"]


def test_can_edit_after_reopen(
    client: TestClient, admin_user: Employee, employee_a: Employee,
    company: Company, closed_period_a: TimesheetPeriod
):
    token = get_token(client, "admin@example.com", "admin123")
    # Reopen first
    client.post(
        f"/api/timesheet/periods/{closed_period_a.id}/reopen",
        json={"reason": "Открываем для правки"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Now cell edit should work
    resp = client.put(
        "/api/timesheet/cell",
        json={"employee_id": employee_a.id, "work_date": WORK_DATE, "company_id": company.id, "hours": 8},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ── Audit log ─────────────────────────────────────────────────────────────────

def test_audit_on_submit(
    client: TestClient, manager_a: Employee, draft_period_a: TimesheetPeriod,
    db_session: Session
):
    token = get_token(client, "manager_a@example.com", "mgr123456")
    client.post(
        f"/api/timesheet/periods/{draft_period_a.id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(
        entity_type="timesheet_period", action="period_submitted"
    ).first()
    assert log is not None
    assert log.after["status"] == "pending_review"


def test_audit_on_return(
    client: TestClient, accountant_user: Employee, pending_period_a: TimesheetPeriod,
    db_session: Session
):
    token = get_token(client, "accountant@example.com", "acc123456")
    client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/return",
        json={"reason": "Нашёл ошибку"},
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(
        entity_type="timesheet_period", action="period_returned"
    ).first()
    assert log is not None
    assert log.reason == "Нашёл ошибку"


def test_audit_on_close(
    client: TestClient, accountant_user: Employee, pending_period_a: TimesheetPeriod,
    db_session: Session
):
    token = get_token(client, "accountant@example.com", "acc123456")
    client.post(
        f"/api/timesheet/periods/{pending_period_a.id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(
        entity_type="timesheet_period", action="period_closed"
    ).first()
    assert log is not None
    assert log.after["status"] == "closed"


def test_audit_on_reopen(
    client: TestClient, admin_user: Employee, closed_period_a: TimesheetPeriod,
    db_session: Session
):
    token = get_token(client, "admin@example.com", "admin123")
    client.post(
        f"/api/timesheet/periods/{closed_period_a.id}/reopen",
        json={"reason": "Переоткрываю для правок"},
        headers={"Authorization": f"Bearer {token}"},
    )
    log = db_session.query(AuditLog).filter_by(
        entity_type="timesheet_period", action="period_reopened"
    ).first()
    assert log is not None
    assert log.reason == "Переоткрываю для правок"


# ── History endpoint ──────────────────────────────────────────────────────────

def test_period_history(
    client: TestClient, manager_a: Employee, accountant_user: Employee,
    admin_user: Employee, draft_period_a: TimesheetPeriod
):
    mgr_tok = get_token(client, "manager_a@example.com", "mgr123456")
    acc_tok = get_token(client, "accountant@example.com", "acc123456")
    adm_tok = get_token(client, "admin@example.com", "admin123")

    period_id = draft_period_a.id

    # Submit
    resp = client.post(
        f"/api/timesheet/periods/{period_id}/submit",
        headers={"Authorization": f"Bearer {mgr_tok}"},
    )
    assert resp.status_code == 200

    # Return
    resp = client.post(
        f"/api/timesheet/periods/{period_id}/return",
        json={"reason": "Нужна правка"},
        headers={"Authorization": f"Bearer {acc_tok}"},
    )
    assert resp.status_code == 200

    # Submit again
    resp = client.post(
        f"/api/timesheet/periods/{period_id}/submit",
        headers={"Authorization": f"Bearer {mgr_tok}"},
    )
    assert resp.status_code == 200

    # Close
    resp = client.post(
        f"/api/timesheet/periods/{period_id}/close",
        headers={"Authorization": f"Bearer {acc_tok}"},
    )
    assert resp.status_code == 200

    # History
    resp = client.get(
        f"/api/timesheet/periods/{period_id}/history",
        headers={"Authorization": f"Bearer {adm_tok}"},
    )
    assert resp.status_code == 200
    history = resp.json()
    actions = [h["action"] for h in history]
    assert actions == [
        "period_submitted", "period_returned", "period_submitted", "period_closed"
    ]
