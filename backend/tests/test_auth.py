from fastapi.testclient import TestClient

from app.models.users import User
from tests.conftest import get_token


def test_login_success(client: TestClient, admin_user: User):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["must_change_password"] is True


def test_login_wrong_password(client: TestClient, admin_user: User):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_inactive_user(client: TestClient, inactive_user: User):
    resp = client.post("/api/auth/login", json={"email": "inactive@example.com", "password": "pass123"})
    assert resp.status_code == 403


def test_me_requires_token(client: TestClient):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(client: TestClient, admin_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@example.com"


def test_change_password_resets_flag(client: TestClient, admin_user: User):
    token = get_token(client, "admin@example.com", "admin123")
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "admin123", "new_password": "newpass456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204

    # Login with new password should return must_change_password=False
    resp2 = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "newpass456"})
    assert resp2.status_code == 200
    assert resp2.json()["must_change_password"] is False
