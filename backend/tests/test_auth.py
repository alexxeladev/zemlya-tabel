from fastapi.testclient import TestClient

from app.models.employees import Employee
from tests.conftest import get_token


def test_login_success(client: TestClient, admin_user: Employee):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["must_change_password"] is False


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


# ── Обязательная смена пароля — на сервере (task_stage2_access п.2.2) ─────────
# Было: требование держал только React; токен пользователя с
# must_change_password работал со всем API.
from app.core.deps import PASSWORD_CHANGE_REQUIRED  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

# Всё, что ограниченной сессии разрешено: свой профиль и смена пароля.
ALLOWED_WHILE_PENDING = {("GET", "/api/auth/me"), ("POST", "/api/auth/change-password")}


@pytest.fixture
def pending_admin(db_session) -> Employee:
    """Админ — чтобы отказ нельзя было списать на нехватку роли."""
    emp = Employee(
        full_name="Pending Admin", email="pending@example.com",
        hashed_password=hash_password("pending123"), role="admin",
        is_active=True, must_change_password=True,
    )
    db_session.add(emp)
    db_session.commit()
    return emp


def _all_routes():
    for route in fastapi_app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def _concrete(path: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", "1", path)


def test_pending_session_rejected_on_every_protected_route(
    client: TestClient, pending_admin: Employee
):
    """Любой маршрут, требующий входа, кроме профиля и смены пароля, отвечает
    403 «Требуется сменить пароль». Список берётся из app.routes — новый
    эндпойнт попадает под проверку сам."""
    token = get_token(client, "pending@example.com", "pending123")
    checked, leaks = 0, []
    for method, path in _all_routes():
        if (method, path) in ALLOWED_WHILE_PENDING:
            continue
        url = _concrete(path)
        anon = client.request(method, url)
        if anon.status_code != 401:
            continue  # маршрут без входа (логин, документация) — не наш случай
        resp = client.request(method, url, headers=_auth(token))
        checked += 1
        if resp.status_code != 403 or resp.json().get("detail") != PASSWORD_CHANGE_REQUIRED:
            leaks.append(f"{method} {path} → {resp.status_code}")
    assert checked > 100, f"обошли подозрительно мало маршрутов: {checked}"
    assert not leaks, "ограниченная сессия прошла:\n" + "\n".join(leaks)


def test_pending_session_can_read_profile_and_change_password(
    client: TestClient, pending_admin: Employee
):
    token = get_token(client, "pending@example.com", "pending123")
    me = client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "pending123", "new_password": "brandnew123"},
        headers=_auth(token),
    )
    assert resp.status_code in (200, 204)


def test_session_unlocked_after_password_change(client: TestClient, pending_admin: Employee):
    token = get_token(client, "pending@example.com", "pending123")
    client.post(
        "/api/auth/change-password",
        json={"current_password": "pending123", "new_password": "brandnew123"},
        headers=_auth(token),
    )
    fresh = get_token(client, "pending@example.com", "brandnew123")
    assert client.get("/api/employees", headers=_auth(fresh)).status_code == 200


def test_admin_reset_puts_user_into_restricted_session(
    client: TestClient, admin_user: Employee, db_session
):
    """Сброс админом ставит требование — и с новым паролем до смены работать нельзя."""
    emp = Employee(full_name="Рядовой", email="plain@example.com", role="accountant",
                   hashed_password=hash_password("plain1234"), is_active=True)
    db_session.add(emp)
    db_session.commit()
    adm = get_token(client, "admin@example.com", "admin123")
    temp = client.post(f"/api/employees/{emp.id}/reset-password", headers=_auth(adm)).json()["temp_password"]
    tok = get_token(client, "plain@example.com", temp)
    resp = client.get("/api/timesheet/2026/5", headers=_auth(tok))
    assert resp.status_code == 403
    assert resp.json()["detail"] == PASSWORD_CHANGE_REQUIRED
