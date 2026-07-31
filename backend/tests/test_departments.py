from fastapi.testclient import TestClient

from app.models.departments import Department
from app.models.employees import Employee
from app.models.companies import Company
from app.models.schedules import Schedule
from app.core.security import hash_password
from tests.conftest import get_token


def _make_dept(db_session, name="Дирекция", code="DIR") -> Department:
    dept = Department(name=name, code=code, is_active=True)
    db_session.add(dept)
    db_session.commit()
    db_session.refresh(dept)
    return dept


def test_create_department_admin(client: TestClient, admin_user: Employee):
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


def test_create_department_manager_forbidden(client: TestClient, manager_user: Employee):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/departments",
        json={"name": "IT", "code": "IT"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_list_departments_manager(client: TestClient, admin_user: Employee, manager_user: Employee, db_session):
    """Менеджер видит в справочнике только свои отделы — из этого списка
    строится его селектор отделов (task_org_structure ч.2)."""
    own = _make_dept(db_session)
    _make_dept(db_session, name="Чужой отдел", code="FOR")
    manager_user.managed_departments = [own]
    db_session.commit()

    token = get_token(client, "manager@example.com", "manager123")
    resp = client.get("/api/departments", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert [d["name"] for d in resp.json()] == ["Дирекция"]


def test_list_departments_manager_without_managed_is_empty(
    client: TestClient, admin_user: Employee, manager_user: Employee, db_session
):
    _make_dept(db_session)
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.get("/api/departments", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_departments_employee_forbidden(client: TestClient, db_session):
    emp_user = Employee(
        email="emp@example.com",
        full_name="Employee",
        hashed_password=hash_password("pass123"),
        role="employee",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(emp_user)
    db_session.commit()
    token = get_token(client, "emp@example.com", "pass123")
    resp = client.get("/api/departments", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_update_department_admin(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/departments/{dept.id}",
        json={"name": "Изменённый отдел"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Изменённый отдел"


def test_delete_department_soft(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/departments/{dept.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    db_session.refresh(dept)
    assert dept.is_active is False


def test_delete_department_with_employees_409(client: TestClient, admin_user: Employee, db_session):
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


# ── Головная компания отдела (task_org_structure ч.1) ─────────────────────────
# Это ярлык для дерева оргструктуры. На расчёт ЗП не влияет — сотрудник
# по-прежнему работает на любые юрлица (см. test_head_company_does_not_limit_payroll).

def test_create_department_with_head_company(client: TestClient, admin_user: Employee, db_session):
    company = Company(code="zmo", name="ООО Земля МО", is_active=True)
    db_session.add(company)
    db_session.commit()
    db_session.refresh(company)

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/departments",
        json={"name": "ИТО", "code": "ITO", "head_company_id": company.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["head_company_id"] == company.id


def test_set_and_clear_head_company(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    company = Company(code="kft", name="ООО Комфорт", is_active=True)
    db_session.add(company)
    db_session.commit()
    db_session.refresh(company)
    token = get_token(client, "admin@example.com", "admin123")

    resp = client.patch(
        f"/api/departments/{dept.id}",
        json={"head_company_id": company.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["head_company_id"] == company.id

    # Явный null снимает головную компанию — отдел уезжает в «Без компании»
    resp = client.patch(
        f"/api/departments/{dept.id}",
        json={"head_company_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["head_company_id"] is None


def test_head_company_must_exist(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/departments/{dept.id}",
        json={"head_company_id": 99999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── Менеджеры отдела: many-to-many (task_org_structure ч.2) ───────────────────

def _make_manager(db_session, name="Иванов И.И.", email="m1@example.com", role="manager") -> Employee:
    emp = Employee(
        full_name=name,
        email=email,
        hashed_password=hash_password("pass1234"),
        role=role,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def test_set_department_managers(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    m1 = _make_manager(db_session, "Иванов И.И.", "m1@example.com")
    m2 = _make_manager(db_session, "Петров П.П.", "m2@example.com")
    token = get_token(client, "admin@example.com", "admin123")

    resp = client.put(
        f"/api/departments/{dept.id}/managers",
        json={"employee_ids": [m1.id, m2.id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["managers"]] == sorted([m1.id, m2.id])

    resp = client.get(
        f"/api/departments/{dept.id}/managers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert {m["full_name"] for m in resp.json()["managers"]} == {"Иванов И.И.", "Петров П.П."}


def test_manager_can_lead_several_departments(client: TestClient, admin_user: Employee, db_session):
    """Один менеджер — несколько отделов. department_id при этом не трогается."""
    d1 = _make_dept(db_session, "ИТО", "ITO")
    d2 = _make_dept(db_session, "Бухгалтерия", "BUH")
    m = _make_manager(db_session)
    token = get_token(client, "admin@example.com", "admin123")

    for d in (d1, d2):
        resp = client.put(
            f"/api/departments/{d.id}/managers",
            json={"employee_ids": [m.id]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    db_session.refresh(m)
    assert m.managed_department_ids == sorted([d1.id, d2.id])
    assert m.department_id is None  # где числится — отдельное поле, не изменилось


def test_set_managers_replaces_previous_set(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    m1 = _make_manager(db_session, "Иванов И.И.", "m1@example.com")
    m2 = _make_manager(db_session, "Петров П.П.", "m2@example.com")
    token = get_token(client, "admin@example.com", "admin123")
    headers = {"Authorization": f"Bearer {token}"}

    client.put(f"/api/departments/{dept.id}/managers", json={"employee_ids": [m1.id]}, headers=headers)
    resp = client.put(
        f"/api/departments/{dept.id}/managers", json={"employee_ids": [m2.id]}, headers=headers
    )
    assert [m["id"] for m in resp.json()["managers"]] == [m2.id]

    # Пустой список снимает всех
    resp = client.put(
        f"/api/departments/{dept.id}/managers", json={"employee_ids": []}, headers=headers
    )
    assert resp.json()["managers"] == []


def test_set_managers_rejects_non_manager_role(client: TestClient, admin_user: Employee, db_session):
    dept = _make_dept(db_session)
    emp = _make_manager(db_session, "Сидоров С.С.", "e1@example.com", role="employee")
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.put(
        f"/api/departments/{dept.id}/managers",
        json={"employee_ids": [emp.id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_set_managers_admin_only(client: TestClient, admin_user: Employee, manager_user: Employee, db_session):
    dept = _make_dept(db_session)
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.put(
        f"/api/departments/{dept.id}/managers",
        json={"employee_ids": [manager_user.id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
