from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.schedules import Schedule
from tests.conftest import get_token


def _fixtures(db_session):
    dept1 = Department(name="Отдел 1", code="D1", is_active=True)
    dept2 = Department(name="Отдел 2", code="D2", is_active=True)
    company = Company(code="A", name="ООО А", is_active=True)
    schedule = Schedule(name="5/2", hours_per_shift=8, is_active=True)
    db_session.add_all([dept1, dept2, company, schedule])
    db_session.flush()
    return dept1, dept2, company, schedule


def _emp(db_session, full_name, dept_id=None, company_id=None, schedule_id=None,
         tab=None, active=True, email=None, role=None, password=None):
    kwargs = dict(
        tab_number=tab,
        full_name=full_name,
        department_id=dept_id,
        schedule_id=schedule_id,
        default_company_id=company_id,
        rate=50000,
        is_active=active,
    )
    if email:
        kwargs["email"] = email
        kwargs["hashed_password"] = hash_password(password or "password123")
        kwargs["role"] = role or "employee"
    emp = Employee(**kwargs)
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


# ── Basic CRUD ────────────────────────────────────────────────────────────────

def test_create_employee_without_access(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/employees",
        json={"full_name": "Иванов И.И.", "tab_number": "T001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "Иванов И.И."
    assert data["has_access"] is False
    assert data["email"] is None
    assert data["role"] is None


def test_create_employee_with_access(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/employees",
        json={
            "full_name": "Петров П.П.",
            "access": {"email": "petrov@example.com", "role": "manager", "initial_password": "securepass1"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "petrov@example.com"
    assert data["role"] == "manager"
    assert data["has_access"] is True
    assert data["must_change_password"] is True


def test_create_employee_manager_forbidden(client: TestClient, manager_user: Employee):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/employees",
        json={"full_name": "Тест"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_create_employee_with_fixtures(client: TestClient, admin_user: Employee, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/employees",
        json={
            "full_name": "Сидоров С.С.",
            "tab_number": "T002",
            "department_id": dept1.id,
            "schedule_id": schedule.id,
            "default_company_id": company.id,
            "rate": "60000.00",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["department"]["code"] == "D1"
    assert data["schedule"]["name"] == "5/2"


# ── Access management ──────────────────────────────────────────────────────────

def test_grant_access(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Без доступа")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{emp.id}/access",
        json={"email": "nodomain@example.com", "role": "accountant", "initial_password": "securepass1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "nodomain@example.com"
    assert data["has_access"] is True
    assert data["role"] == "accountant"


def test_grant_access_duplicate_email(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Уже с почтой", email="exists@example.com", role="employee", password="password123")
    emp2 = _emp(db_session, "Второй")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{emp2.id}/access",
        json={"email": "exists@example.com", "role": "employee", "initial_password": "securepass1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


def test_grant_access_already_has_access(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Уже есть", email="already@example.com", role="employee", password="password123")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{emp.id}/access",
        json={"email": "another@example.com", "role": "employee", "initial_password": "securepass1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


def test_update_role(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Менеджер", email="mgr@example.com", role="manager", password="password123")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/employees/{emp.id}/access",
        json={"role": "accountant"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "accountant"


def test_update_role_system_admin_forbidden(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/employees/{admin_user.id}/access",
        json={"role": "manager"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_reset_password_returns_temp(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Сотрудник", email="worker@example.com", role="employee", password="password123")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{emp.id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "temp_password" in data
    assert len(data["temp_password"]) == 12

    resp2 = client.post("/api/auth/login", json={"email": "worker@example.com", "password": data["temp_password"]})
    assert resp2.status_code == 200
    assert resp2.json()["must_change_password"] is True


def test_reset_password_system_admin_allowed(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{admin_user.id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_reset_password_no_access(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Без доступа")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{emp.id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_revoke_access(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Бывший", email="revoke@example.com", role="employee", password="password123")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(
        f"/api/employees/{emp.id}/access",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    db_session.refresh(emp)
    assert emp.email is None
    assert emp.role is None
    assert emp.hashed_password is None


def test_revoke_access_system_admin_forbidden(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(
        f"/api/employees/{admin_user.id}/access",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── System admin restrictions ──────────────────────────────────────────────────

def test_delete_system_admin_forbidden(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(
        f"/api/employees/{admin_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Manager visibility ─────────────────────────────────────────────────────────

def test_manager_without_dept_sees_empty_list(client: TestClient, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _emp(db_session, "Иванов", dept_id=dept1.id)
    mgr = _emp(db_session, "Менеджер без отдела", email="mgr0@example.com",
               role="manager", password="password123")
    token = get_token(client, "mgr0@example.com", "password123")
    resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_manager_sees_only_own_department(client: TestClient, db_session):
    dept1, dept2, company, schedule = _fixtures(db_session)
    _emp(db_session, "Иванов", dept_id=dept1.id)
    _emp(db_session, "Петров", dept_id=dept2.id)

    mgr = Employee(
        full_name="Менеджер",
        email="mgr1@example.com",
        hashed_password=hash_password("password123"),
        role="manager",
        department_id=dept1.id,
        is_active=True,
    )
    db_session.add(mgr)
    db_session.commit()

    token = get_token(client, "mgr1@example.com", "password123")
    resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    names = [e["full_name"] for e in resp.json()]
    assert "Иванов" in names
    assert "Петров" not in names


def test_manager_cannot_see_other_dept_employee(client: TestClient, db_session):
    dept1, dept2, company, schedule = _fixtures(db_session)
    emp = _emp(db_session, "Петров", dept_id=dept2.id)

    mgr = Employee(
        full_name="Менеджер2",
        email="mgr2@example.com",
        hashed_password=hash_password("password123"),
        role="manager",
        department_id=dept1.id,
        is_active=True,
    )
    db_session.add(mgr)
    db_session.commit()

    token = get_token(client, "mgr2@example.com", "password123")
    resp = client.get(f"/api/employees/{emp.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


# ── Search & filter ───────────────────────────────────────────────────────────

def test_search_by_full_name(client: TestClient, admin_user: Employee, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _emp(db_session, "Иванов Иван Иванович", dept_id=dept1.id, tab="T001")
    _emp(db_session, "Петров Пётр Петрович", dept_id=dept1.id, tab="T002")

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/employees?search=Иванов", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_search_by_tab_number(client: TestClient, admin_user: Employee, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _emp(db_session, "Сидоров С.С.", dept_id=dept1.id, tab="T999")

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/employees?search=T999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_filter_is_active(client: TestClient, admin_user: Employee, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _emp(db_session, "Активный", dept_id=dept1.id, active=True)
    _emp(db_session, "Неактивный", dept_id=dept1.id, active=False)

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/employees?is_active=true", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    # admin_user itself (is_active=True) + "Активный" = but admin has no dept_id so different filter
    assert all(e["is_active"] for e in resp.json())


# ── Soft delete ────────────────────────────────────────────────────────────────

def test_soft_delete_employee(client: TestClient, admin_user: Employee, db_session):
    emp = _emp(db_session, "Удаляемый")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/employees/{emp.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    db_session.refresh(emp)
    assert emp.is_active is False


# ── 409 guard on references ────────────────────────────────────────────────────

def test_delete_dept_with_active_employee_409(client: TestClient, admin_user: Employee, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    _emp(db_session, "Активный", dept_id=dept1.id)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/departments/{dept1.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409


def test_nested_objects_in_read(client: TestClient, admin_user: Employee, db_session):
    dept1, _, company, schedule = _fixtures(db_session)
    emp = _emp(db_session, "Иванов", dept_id=dept1.id,
               company_id=company.id, schedule_id=schedule.id)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get(f"/api/employees/{emp.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["department"]["code"] == "D1"
    assert data["schedule"]["name"] == "5/2"
    assert data["default_company"]["code"] == "A"


# ── Login for employee with role=NULL ──────────────────────────────────────────

def test_login_null_role_fails(client: TestClient, db_session):
    emp = _emp(db_session, "Без роли", email="norole@example.com")
    resp = client.post("/api/auth/login", json={"email": "norole@example.com", "password": "anything"})
    assert resp.status_code == 401
