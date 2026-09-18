"""Небезопасные значения по умолчанию и перебор паролей (task_stage2_access п.2.6).

Было: сервер стартовал с SECRET_KEY="change-me" (подпись токенов
предсказуема), попытки входа не ограничивались и нигде не записывались.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import secret_key_problem, settings
from app.core.security import hash_password
from app.main import app
from app.models.audit_log import AuditLog
from app.models.employees import Employee
from app.models.login_failures import LoginFailure
from tests.conftest import get_token

LIMIT = settings.LOGIN_MAX_FAILURES


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, email, password, ip="10.0.0.1"):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password},
        headers={"X-Real-IP": ip},
    )


@pytest.fixture
def victim(db_session) -> Employee:
    emp = Employee(full_name="Бухгалтер", email="victim@example.com", role="accountant",
                   hashed_password=hash_password("right-pass-1"), is_active=True)
    db_session.add(emp)
    db_session.commit()
    return emp


# ── SECRET_KEY ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", ["", "   ", "change-me", "CHANGE-ME", "secret", "x" * 31])
def test_insecure_secret_detected(secret):
    assert secret_key_problem(secret) is not None


def test_generated_secret_accepted():
    assert secret_key_problem("a1" * 32) is None


@pytest.mark.parametrize("secret", ["change-me", "", "short-secret"])
def test_server_refuses_to_start_with_insecure_secret(monkeypatch, secret):
    monkeypatch.setattr(settings, "SECRET_KEY", secret)
    with pytest.raises(RuntimeError, match="Запуск отклонён"):
        with TestClient(app):
            pass


# ── Лимит попыток входа ───────────────────────────────────────────────────────

def test_account_locked_after_limit(client: TestClient, victim: Employee):
    for _ in range(LIMIT):
        assert _login(client, "victim@example.com", "wrong-pass").status_code == 401
    resp = _login(client, "victim@example.com", "wrong-pass")
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


def test_correct_password_rejected_while_locked(client: TestClient, victim: Employee):
    for _ in range(LIMIT):
        _login(client, "victim@example.com", "wrong-pass")
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 429


def test_lock_is_per_account_from_any_ip(client: TestClient, victim: Employee):
    """Решение заказчика: лимит на учётку, с любых адресов."""
    for i in range(LIMIT):
        _login(client, "victim@example.com", "wrong-pass", ip=f"10.0.0.{i + 10}")
    assert _login(client, "victim@example.com", "right-pass-1", ip="10.9.9.9").status_code == 429


def test_email_case_does_not_bypass_limit(client: TestClient, victim: Employee):
    for i in range(LIMIT):
        _login(client, "VICTIM@example.com" if i % 2 else "victim@example.com", "wrong-pass")
    assert _login(client, "Victim@Example.com", "wrong-pass").status_code == 429


def test_other_account_not_affected(client: TestClient, victim: Employee, admin_user: Employee):
    for _ in range(LIMIT):
        _login(client, "victim@example.com", "wrong-pass")
    assert _login(client, "admin@example.com", "admin123").status_code == 200


def test_success_resets_counter(client: TestClient, victim: Employee):
    for _ in range(LIMIT - 1):
        _login(client, "victim@example.com", "wrong-pass")
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 200
    for _ in range(LIMIT - 1):
        _login(client, "victim@example.com", "wrong-pass")
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 200


def test_lock_expires_after_window(client: TestClient, victim: Employee, db_session):
    for _ in range(LIMIT):
        _login(client, "victim@example.com", "wrong-pass")
    # Сдвигаем неудачи за окно — как будто прошло 15 минут.
    past = datetime.datetime.utcnow() - datetime.timedelta(minutes=settings.LOGIN_LOCK_MINUTES + 1)
    db_session.query(LoginFailure).update({LoginFailure.created_at: past})
    db_session.commit()
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 200


def test_attempts_during_lock_do_not_extend_it(client: TestClient, victim: Employee, db_session):
    for _ in range(LIMIT):
        _login(client, "victim@example.com", "wrong-pass")
    past = datetime.datetime.utcnow() - datetime.timedelta(minutes=settings.LOGIN_LOCK_MINUTES + 1)
    db_session.query(LoginFailure).update({LoginFailure.created_at: past})
    db_session.commit()
    # Стук во время блокировки (reason=locked) в порог не идёт.
    db_session.add_all([
        LoginFailure(email="victim@example.com", reason="locked", created_at=datetime.datetime.utcnow())
        for _ in range(LIMIT * 2)
    ])
    db_session.commit()
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 200


# ── Журнал неудачных входов ───────────────────────────────────────────────────

def test_failures_are_journaled(client: TestClient, victim: Employee, db_session):
    _login(client, "victim@example.com", "wrong-pass", ip="192.168.5.7")
    _login(client, "nobody@example.com", "whatever1", ip="192.168.5.8")
    rows = {r.email: r for r in db_session.query(LoginFailure).all()}
    assert rows["victim@example.com"].reason == "wrong_password"
    assert rows["victim@example.com"].ip == "192.168.5.7"
    assert rows["victim@example.com"].employee_id == victim.id
    assert rows["nobody@example.com"].reason == "unknown_email"
    assert rows["nobody@example.com"].employee_id is None


def test_locked_attempts_are_journaled(client: TestClient, victim: Employee, db_session):
    for _ in range(LIMIT + 1):
        _login(client, "victim@example.com", "wrong-pass")
    reasons = [r.reason for r in db_session.query(LoginFailure).order_by(LoginFailure.id)]
    assert reasons == ["wrong_password"] * LIMIT + ["locked"]


def test_success_is_not_journaled(client: TestClient, victim: Employee, db_session):
    _login(client, "victim@example.com", "right-pass-1")
    assert db_session.query(LoginFailure).count() == 0


# ── Разблокировка администратором ─────────────────────────────────────────────

def _lock(client):
    for _ in range(LIMIT):
        _login(client, "victim@example.com", "wrong-pass")


def test_admin_sees_lock_and_unlocks(client: TestClient, victim: Employee, admin_user: Employee, db_session):
    _lock(client)
    adm = get_token(client, "admin@example.com", "admin123")
    listed = {e["id"]: e for e in client.get("/api/employees", headers=_auth(adm)).json()}
    assert listed[victim.id]["login_locked_until"] is not None
    assert listed[admin_user.id]["login_locked_until"] is None

    resp = client.post(f"/api/employees/{victim.id}/unlock-login", headers=_auth(adm))
    assert resp.status_code == 200
    assert resp.json()["login_locked_until"] is None
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 200
    assert db_session.query(AuditLog).filter_by(action="login_unlocked", entity_id=victim.id).count() == 1
    # Журнал попыток не стёрт.
    assert db_session.query(LoginFailure).count() == LIMIT


def test_admin_reset_password_unlocks(client: TestClient, victim: Employee, admin_user: Employee):
    _lock(client)
    adm = get_token(client, "admin@example.com", "admin123")
    temp = client.post(f"/api/employees/{victim.id}/reset-password", headers=_auth(adm)).json()["temp_password"]
    assert _login(client, "victim@example.com", temp).status_code == 200


@pytest.mark.parametrize("role", ["manager", "accountant", "timekeeper", "employee"])
def test_non_admin_cannot_unlock(client: TestClient, victim: Employee, db_session, role):
    other = Employee(full_name=role, email=f"{role}-x@example.com", role=role,
                     hashed_password=hash_password("other-pass-1"), is_active=True)
    db_session.add(other)
    db_session.commit()
    tok = get_token(client, f"{role}-x@example.com", "other-pass-1")
    _lock(client)
    assert client.post(f"/api/employees/{victim.id}/unlock-login", headers=_auth(tok)).status_code == 403
    assert _login(client, "victim@example.com", "right-pass-1").status_code == 429


def test_non_admin_does_not_see_lock(client: TestClient, victim: Employee, db_session):
    acc = Employee(full_name="Бух 2", email="acc2@example.com", role="accountant",
                   hashed_password=hash_password("acc2-pass-1"), is_active=True)
    db_session.add(acc)
    db_session.commit()
    tok = get_token(client, "acc2@example.com", "acc2-pass-1")
    _lock(client)
    listed = {e["id"]: e for e in client.get("/api/employees", headers=_auth(tok)).json()}
    assert listed[victim.id]["login_locked_until"] is None
