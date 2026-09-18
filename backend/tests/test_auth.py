from fastapi.testclient import TestClient

from app.models.employees import Employee
from tests.conftest import get_token


def test_login_success(client: TestClient, admin_user: Employee):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["must_change_password"] is True


def test_login_wrong_password(client: TestClient, admin_user: Employee):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_inactive_user(client: TestClient, inactive_user: Employee):
    resp = client.post("/api/auth/login", json={"email": "inactive@example.com", "password": "pass123"})
    assert resp.status_code == 403


def test_login_no_access_employee(client: TestClient, db_session):
    # Employee without email/role cannot log in
    emp = Employee(full_name="No Access", is_active=True)
    db_session.add(emp)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "pass"})
    assert resp.status_code == 401


def test_me_requires_token(client: TestClient):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@example.com"
    assert data["has_access"] is True
    assert data["is_system_admin"] is True


def test_change_password_resets_flag(client: TestClient, admin_user: Employee):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "admin123", "new_password": "newpass456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204

    resp2 = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "newpass456"})
    assert resp2.status_code == 200
    assert resp2.json()["must_change_password"] is False


# ── Политика паролей (task_stage2_access п.2.3) ───────────────────────────────
# Было: POST /auth/change-password с new_password="" отвечал 204 — поле было
# обычной строкой. Политика одна на создание, выдачу доступа, смену, сброс и CLI.
import pytest  # noqa: E402

from app.core.security import (  # noqa: E402
    PASSWORD_MAX_BYTES,
    password_policy_error,
    verify_password,
)

BAD_PASSWORDS = [
    pytest.param("", id="empty"),
    pytest.param("        ", id="spaces-only"),
    pytest.param("short12", id="7-chars"),
    pytest.param("a" * (PASSWORD_MAX_BYTES + 1), id="73-bytes-latin"),
    # 37 кириллических букв = 74 байта: bcrypt молча обрезал бы хвост.
    pytest.param("ж" * 37, id="74-bytes-cyrillic"),
]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("bad", BAD_PASSWORDS)
def test_change_password_rejects_bad(client: TestClient, admin_user: Employee, db_session, bad):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "admin123", "new_password": bad},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    db_session.refresh(admin_user)
    assert verify_password("admin123", admin_user.hashed_password)


@pytest.mark.parametrize("bad", BAD_PASSWORDS)
def test_create_employee_with_access_rejects_bad(client: TestClient, admin_user: Employee, db_session, bad):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/employees",
        json={"full_name": "Новый", "access": {
            "email": "new@example.com", "role": "employee", "initial_password": bad,
        }},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert db_session.query(Employee).filter(Employee.email == "new@example.com").count() == 0


@pytest.mark.parametrize("bad", BAD_PASSWORDS)
def test_grant_access_rejects_bad(client: TestClient, admin_user: Employee, db_session, bad):
    emp = Employee(full_name="Без доступа", is_active=True)
    db_session.add(emp)
    db_session.commit()
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/employees/{emp.id}/access",
        json={"email": "grant@example.com", "role": "employee", "initial_password": bad},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    db_session.refresh(emp)
    assert emp.email is None


def test_seventy_two_bytes_is_accepted():
    """Граница: ровно 72 байта — годится, 73 — нет."""
    assert password_policy_error("a" * PASSWORD_MAX_BYTES) is None
    assert password_policy_error("ж" * 36) is None


def test_reset_password_temp_satisfies_policy(client: TestClient, admin_user: Employee, db_session):
    emp = Employee(full_name="Сброс", email="reset@example.com", role="employee",
                   hashed_password="x", is_active=True)
    db_session.add(emp)
    db_session.commit()
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(f"/api/employees/{emp.id}/reset-password", headers=_auth(token))
    assert resp.status_code == 200
    assert password_policy_error(resp.json()["temp_password"]) is None


@pytest.mark.parametrize("bad", BAD_PASSWORDS)
def test_cli_rejects_bad(bad, monkeypatch):
    """CLI — тоже место, где задаётся пароль. Отказ ДО обращения к базе."""
    from app import cli

    def _no_db():
        raise AssertionError("CLI полез в базу с негодным паролем")

    monkeypatch.setattr("app.database.SessionLocal", _no_db)
    with pytest.raises(SystemExit):
        cli.create_admin("cli@example.com", bad, "CLI")
    with pytest.raises(SystemExit):
        cli.reset_password("cli@example.com", bad)
