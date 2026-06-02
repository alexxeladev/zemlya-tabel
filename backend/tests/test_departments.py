from fastapi.testclient import TestClient

from app.models.departments import Department
from app.models.employees import Employee
from app.models.companies import Company
from app.models.schedules import Schedule
from app.models.users import User, UserRole
from app.core.security import hash_password
from tests.conftest import get_token


def _make_dept(db_session, name="Дирекция", code="DIR") -> Department:
    dept = Department(name=name, code=code, is_active=True)
    db_session.add(dept)
    db_session.commit()
    db_session.refresh(dept)
    return dept


def test_create_department_admin(client: TestClient, admin_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/departments",
        json={"name": "Дирекция", "code": "DIR"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Дирекция"
    assert data["is_active"] is True


def test_create_department_manager_forbidden(client: TestClient, manager_user: User):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/departments",
        json={"name": "IT", "code": "IT"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_list_departments_manager(client: TestClient, admin_user: User, manager_user: User, db_session):
    _make_dept(db_session)
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.get("/api/departments", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_departments_employee_forbidden(client: TestClient, db_session):
    emp_user = User(
        email="emp@example.com",
        full_name="Employee",
        hashed_password=hash_password("pass123"),
        role=UserRole.employee,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(emp_user)
    db_session.commit()
    token = get_token(client, "emp@example.com", "pass123")
    resp = client.get("/api/departments", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_update_department_admin(client: TestClient, admin_user: User, db_session):
    dept = _make_dept(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/departments/{dept.id}",
        json={"name": "Изменённый отдел"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Изменённый отдел"


def test_delete_department_soft(client: TestClient, admin_user: User, db_session):
    dept = _make_dept(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/departments/{dept.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    db_session.refresh(dept)
    assert dept.is_active is False


def test_delete_department_with_employees_409(client: TestClient, admin_user: User, db_session):
    dept = _make_dept(db_session)
    company = Company(code="A", name="ООО А", is_active=True)
    schedule = Schedule(name="5/2", hours_per_shift=8, is_active=True)
    db_session.add_all([company, schedule])
    db_session.flush()
    emp = Employee(
        full_name="Иванов И.И.",
        department_id=dept.id,
        schedule_id=schedule.id,
        default_company_id=company.id,
        rate=50000,
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/departments/{dept.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
    assert "сотрудников" in resp.json()["detail"]
