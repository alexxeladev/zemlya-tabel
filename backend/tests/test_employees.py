from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.schedules import Schedule
from app.models.users import User, UserRole
from tests.conftest import get_token


def _fixtures(db_session):
    dept1 = Department(name="Отдел 1", code="D1", is_active=True)
    dept2 = Department(name="Отдел 2", code="D2", is_active=True)
    company = Company(code="A", name="ООО А", is_active=True)
    schedule = Schedule(name="5/2", hours_per_shift=8, is_active=True)
    db_session.add_all([dept1, dept2, company, schedule])
    db_session.flush()
    return dept1, dept2, company, schedule


def _make_employee(db_session, full_name, dept_id, company_id, schedule_id, tab=None, active=True):
    emp = Employee(
        tab_number=tab,
        full_name=full_name,
        department_id=dept_id,
        schedule_id=schedule_id,
        default_company_id=company_id,
        rate=50000,
        is_active=active,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def test_create_employee_admin(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    db_session.commit()
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/employees",
        json={
            "full_name": "Иванов И.И.",
            "tab_number": "T001",
            "department_id": dept1.id,
            "schedule_id": schedule.id,
            "default_company_id": company.id,
            "rate": "60000.00",
            "is_active": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "Иванов И.И."
    assert data["tab_number"] == "T001"


def test_create_employee_manager_forbidden(client: TestClient, manager_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    db_session.commit()
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/employees",
        json={
            "full_name": "Тест",
            "department_id": dept1.id,
            "schedule_id": schedule.id,
            "default_company_id": company.id,
            "rate": "10000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_manager_sees_only_own_department(client: TestClient, admin_user: User, db_session):
    dept1, dept2, company, schedule = _fixtures(db_session)
    _make_employee(db_session, "Иванов", dept1.id, company.id, schedule.id)
    _make_employee(db_session, "Петров", dept2.id, company.id, schedule.id)

    mgr = User(
        email="mgr@example.com",
        full_name="Менеджер",
        hashed_password=hash_password("pass123"),
        role=UserRole.manager,
        department_id=dept1.id,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(mgr)
    db_session.commit()

    token = get_token(client, "mgr@example.com", "pass123")
    resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    names = [e["full_name"] for e in resp.json()]
    assert "Иванов" in names
    assert "Петров" not in names


def test_manager_cannot_get_other_dept_employee(client: TestClient, admin_user: User, db_session):
    dept1, dept2, company, schedule = _fixtures(db_session)
    emp = _make_employee(db_session, "Петров", dept2.id, company.id, schedule.id)

    mgr = User(
        email="mgr2@example.com",
        full_name="Менеджер2",
        hashed_password=hash_password("pass123"),
        role=UserRole.manager,
        department_id=dept1.id,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(mgr)
    db_session.commit()

    token = get_token(client, "mgr2@example.com", "pass123")
    resp = client.get(f"/api/employees/{emp.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_search_employees(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _make_employee(db_session, "Иванов Иван Иванович", dept1.id, company.id, schedule.id, tab="T001")
    _make_employee(db_session, "Петров Пётр Петрович", dept1.id, company.id, schedule.id, tab="T002")

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/employees?search=Иванов", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["full_name"] == "Иванов Иван Иванович"


def test_search_by_tab_number(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _make_employee(db_session, "Сидоров С.С.", dept1.id, company.id, schedule.id, tab="T999")

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/employees?search=T999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_filter_by_is_active(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _make_employee(db_session, "Активный", dept1.id, company.id, schedule.id, active=True)
    _make_employee(db_session, "Неактивный", dept1.id, company.id, schedule.id, active=False)

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/employees?is_active=true", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert all(e["is_active"] for e in resp.json())
    assert len(resp.json()) == 1


def test_soft_delete_employee(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    emp = _make_employee(db_session, "Удаляемый", dept1.id, company.id, schedule.id)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/employees/{emp.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    db_session.refresh(emp)
    assert emp.is_active is False


def test_delete_dept_with_active_employee_409(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _make_employee(db_session, "Активный", dept1.id, company.id, schedule.id, active=True)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/departments/{dept1.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409


def test_employee_read_includes_nested(client: TestClient, admin_user: User, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    emp = _make_employee(db_session, "Иванов", dept1.id, company.id, schedule.id)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get(f"/api/employees/{emp.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["department"]["code"] == "D1"
    assert data["schedule"]["name"] == "5/2"
    assert data["default_company"]["code"] == "A"
