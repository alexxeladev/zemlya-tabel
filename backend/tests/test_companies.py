from fastapi.testclient import TestClient

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.schedules import Schedule
from tests.conftest import get_token


def _make_company(db_session, code="A", name="ООО Альфа") -> Company:
    company = Company(code=code, name=name, is_active=True)
    db_session.add(company)
    db_session.commit()
    db_session.refresh(company)
    return company


def test_create_company_admin(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/companies",
        json={"code": "A", "name": "ООО Альфа", "inn": "1234567890"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "A"
    assert data["inn"] == "1234567890"
    assert data["is_active"] is True


def test_create_company_manager_forbidden(client: TestClient, manager_user: Employee):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/companies",
        json={"code": "B", "name": "ООО Бета"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_list_companies(client: TestClient, admin_user: Employee, db_session):
    _make_company(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/companies", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_update_company(client: TestClient, admin_user: Employee, db_session):
    company = _make_company(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.patch(
        f"/api/companies/{company.id}",
        json={"name": "ООО Альфа Плюс"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "ООО Альфа Плюс"


def test_delete_company_soft(client: TestClient, admin_user: Employee, db_session):
    company = _make_company(db_session)
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/companies/{company.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    db_session.refresh(company)
    assert company.is_active is False


def test_delete_company_with_employees_409(client: TestClient, admin_user: Employee, db_session):
    company = _make_company(db_session)
    dept = Department(name="ОП", code="OP", is_active=True)
    schedule = Schedule(name="5/2", hours_per_shift=8, is_active=True)
    db_session.add_all([dept, schedule])
    db_session.flush()
    emp = Employee(
        full_name="Петров П.П.",
        department_id=dept.id,
        schedule_id=schedule.id,
        default_company_id=company.id,
        rate=40000,
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()

    token = get_token(client, "admin@example.com", "admin123")
    resp = client.delete(f"/api/companies/{company.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
