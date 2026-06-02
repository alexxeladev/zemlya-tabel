from fastapi.testclient import TestClient

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.schedules import Schedule
from tests.conftest import get_token


def _make_schedule(db_session, name="5/2", hours=8) -> Schedule:
    schedule = Schedule(name=name, hours_per_shift=hours, is_active=True)
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


def test_create_schedule_admin(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/schedules",
        json={"name": "5/2", "hours_per_shift": 8, "description": "Пятидневка"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "5/2"
    assert data["hours_per_shift"] == 8
    assert data["is_active"] is True


def test_create_schedule_manager_forbidden(client: TestClient, manager_user: Employee):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/schedules",
        json={"name": "2/2", "hours_per_shift": 12},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_list_schedules(client: TestClient, admin_user: Employee, db_session):
    _make_schedule(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/schedules", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_update_schedule(client: TestClient, admin_user: Employee, db_session):
    schedule = _make_schedule(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/schedules/{schedule.id}",
        json={"hours_per_shift": 9},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["hours_per_shift"] == 9


def test_delete_schedule_soft(client: TestClient, admin_user: Employee, db_session):
    schedule = _make_schedule(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/schedules/{schedule.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    db_session.refresh(schedule)
    assert schedule.is_active is False


def test_delete_schedule_with_employees_409(client: TestClient, admin_user: Employee, db_session):
    schedule = _make_schedule(db_session)
    dept = Department(name="ОП", code="OP", is_active=True)
    company = Company(code="A", name="ООО А", is_active=True)
    db_session.add_all([dept, company])
    db_session.flush()
    emp = Employee(
        full_name="Сидоров С.С.",
        department_id=dept.id,
        schedule_id=schedule.id,
        default_company_id=company.id,
        rate=35000,
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/schedules/{schedule.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
