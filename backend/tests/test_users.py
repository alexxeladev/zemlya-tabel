from fastapi.testclient import TestClient

from app.models.users import User
from tests.conftest import get_token


def test_create_user_requires_admin(client: TestClient, manager_user: User):
    token = get_token(client, "manager@example.com", "manager123")
    resp = client.post(
        "/api/users",
        json={"email": "new@example.com", "full_name": "New", "role": "employee", "password": "pass"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_create_user_as_admin(client: TestClient, admin_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/users",
        json={"email": "new@example.com", "full_name": "New User", "role": "employee", "password": "pass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "new@example.com"
    assert data["must_change_password"] is True


def test_list_users_filter_by_role(client: TestClient, admin_user: User, manager_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/users?role=manager", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    users = resp.json()
    assert all(u["role"] == "manager" for u in users)
    assert len(users) == 1


def test_list_users_filter_by_department(client: TestClient, admin_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/users?department_id=999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_reset_password_returns_temp(client: TestClient, admin_user: User, manager_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        f"/api/users/{manager_user.id}/reset-password",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "temp_password" in data
    assert len(data["temp_password"]) == 12

    # New temp password should work
    resp2 = client.post(
        "/api/auth/login",
        json={"email": "manager@example.com", "password": data["temp_password"]},
    )
    assert resp2.status_code == 200
    assert resp2.json()["must_change_password"] is True
