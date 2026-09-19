"""Логин — часть почты до «@», регистр не важен (решение заказчика 19.09.2026).

Было: вход только по полной почте и с учётом регистра — «Victim@…» и «victim@…»
считались разными, и можно было завести две такие учётки.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.security import hash_password
from app.models.employees import Employee
from app.models.login_failures import LoginFailure
from app.services.accounts import account_conflict, find_account, login_name
from tests.conftest import get_token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, ident, password):
    return client.post("/api/auth/login", json={"email": ident, "password": password})


@pytest.fixture
def victim(db_session) -> Employee:
    emp = Employee(full_name="Бухгалтер", email="victim@example.com", role="accountant",
                   hashed_password=hash_password("right-pass-1"), is_active=True)
    db_session.add(emp)
    db_session.commit()
    return emp


@pytest.mark.parametrize("ident", [
    "victim", "Victim", "VICTIM", " victim ", "victim@example.com", "Victim@Example.COM",
])
def test_login_by_name_or_email_any_case(client: TestClient, victim: Employee, ident):
    assert _login(client, ident, "right-pass-1").status_code == 200


@pytest.mark.parametrize("ident", ["victim@other.org", "victi", "victimx", "example.com"])
def test_other_identifiers_do_not_match(client: TestClient, victim: Employee, ident):
    assert _login(client, ident, "right-pass-1").status_code == 401


def test_wildcards_in_login_are_literal(db_session, victim: Employee):
    """«_» и «%» в LIKE экранируются: «victi_» не находит «victim»."""
    assert find_account(db_session, "victi_") is None
    assert find_account(db_session, "%") is None


def test_all_spellings_share_one_counter(client: TestClient, victim: Employee, db_session):
    spellings = ["victim", "VICTIM", "victim@example.com", "Victim@Example.com", "Victim"]
    for s in spellings[: settings.LOGIN_MAX_FAILURES]:
        assert _login(client, s, "wrong-pass").status_code == 401
    assert _login(client, "victim", "right-pass-1").status_code == 429
    assert {r.email for r in db_session.query(LoginFailure)} == {"victim@example.com"}


def test_create_access_stores_lowercase(client: TestClient, admin_user: Employee, db_session):
    tok = get_token(client, "admin@example.com", "admin123")
    resp = client.post("/api/employees", headers=_auth(tok), json={
        "full_name": "Новый", "access": {
            "email": "Novy.Ivan@Example.com", "role": "employee",
            "initial_password": "start-pass-1",
        },
    })
    assert resp.status_code == 201
    assert resp.json()["email"] == "novy.ivan@example.com"
    assert _login(client, "NOVY.IVAN", "start-pass-1").status_code == 200


@pytest.mark.parametrize("email, expected", [
    ("VICTIM@example.com", "Email already registered"),
    ("victim@other.org", "Логин «victim» уже занят"),
])
def test_create_access_rejects_taken_login(
    client: TestClient, admin_user: Employee, victim: Employee, db_session, email, expected,
):
    tok = get_token(client, "admin@example.com", "admin123")
    resp = client.post("/api/employees", headers=_auth(tok), json={
        "full_name": "Двойник", "access": {
            "email": email, "role": "employee", "initial_password": "start-pass-1",
        },
    })
    assert resp.status_code == 409
    assert expected in resp.json()["detail"]
    assert db_session.query(Employee).filter(Employee.full_name == "Двойник").count() == 0


def test_grant_access_rejects_taken_login(
    client: TestClient, admin_user: Employee, victim: Employee, db_session,
):
    emp = Employee(full_name="Без доступа", is_active=True)
    db_session.add(emp)
    db_session.commit()
    tok = get_token(client, "admin@example.com", "admin123")
    resp = client.post(f"/api/employees/{emp.id}/access", headers=_auth(tok), json={
        "email": "Victim@Another.ru", "role": "employee", "initial_password": "start-pass-1",
    })
    assert resp.status_code == 409
    db_session.refresh(emp)
    assert emp.email is None


def test_revoked_access_frees_login(client: TestClient, admin_user: Employee, victim: Employee):
    tok = get_token(client, "admin@example.com", "admin123")
    client.delete(f"/api/employees/{victim.id}/access", headers=_auth(tok))
    resp = client.post("/api/employees", headers=_auth(tok), json={
        "full_name": "Преемник", "access": {
            "email": "victim@other.org", "role": "employee", "initial_password": "start-pass-1",
        },
    })
    assert resp.status_code == 201


def test_ambiguous_login_refused_and_journaled(client: TestClient, db_session):
    """Две учётки с одной частью до «@» (заведены в обход приложения) — по
    короткому логину не пускаем никуда; по полной почте — каждая своя."""
    db_session.add_all([
        Employee(full_name="Дубль 1", email="dup@a.ru", role="employee",
                 hashed_password=hash_password("dup-pass-11"), is_active=True),
        Employee(full_name="Дубль 2", email="dup@b.ru", role="employee",
                 hashed_password=hash_password("dup-pass-22"), is_active=True),
    ])
    db_session.commit()
    assert _login(client, "dup", "dup-pass-11").status_code == 401
    assert db_session.query(LoginFailure).one().reason == "ambiguous"
    assert _login(client, "dup@b.ru", "dup-pass-22").status_code == 200


def test_cli_reset_by_login(client: TestClient, victim: Employee, db_session, monkeypatch):
    from app import cli

    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    cli.reset_password("VICTIM", "cli-reset-123")
    assert _login(client, "victim", "cli-reset-123").status_code == 200


def test_cli_create_admin_rejects_taken_login(victim: Employee, db_session, monkeypatch, capsys):
    from app import cli

    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    with pytest.raises(SystemExit):
        cli.create_admin("Victim@root.ru", "admin-pass-1", "Root")
    assert "Логин «victim» уже занят" in capsys.readouterr().err


def test_helpers():
    assert login_name(" Ivan.Petrov@Zemlya-MO.ru ") == "ivan.petrov"


def test_conflict_excludes_self(db_session, victim: Employee):
    assert account_conflict(db_session, "VICTIM@example.com", exclude_id=victim.id) is None


def test_concurrent_grant_is_409_not_500(
    client: TestClient, admin_user: Employee, victim: Employee, db_session, monkeypatch,
):
    """Гонку двух выдач доступа эмулируем, выключив проверку приложения: второй
    запрос останавливает уникальный индекс — ответ 409, а не 500."""
    monkeypatch.setattr("app.routers.employees.account_conflict", lambda *a, **k: None)
    tok = get_token(client, "admin@example.com", "admin123")
    resp = client.post("/api/employees", headers=_auth(tok), json={
        "full_name": "Гонщик", "access": {
            "email": "VICTIM@example.com", "role": "employee",
            "initial_password": "start-pass-1",
        },
    })
    assert resp.status_code == 409
    db_session.rollback()
    assert db_session.query(Employee).filter(Employee.full_name == "Гонщик").count() == 0


def test_cli_create_admin_rejects_non_email(db_session, monkeypatch, capsys):
    """Без «@» в учётку не войти ни по логину, ни по почте."""
    from app import cli

    monkeypatch.setattr("app.database.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    with pytest.raises(SystemExit):
        cli.create_admin("admin", "admin-pass-1", "Root")
    assert "не адрес почты" in capsys.readouterr().err
    assert db_session.query(Employee).count() == 0


def test_other_integrity_errors_are_not_reported_as_taken_login(
    client: TestClient, admin_user: Employee, db_session, monkeypatch,
):
    """Гонка по табельному номеру — не «почта или логин заняты»: иначе админ
    искал бы несуществующий конфликт почты (нашло ревью)."""
    from sqlalchemy.exc import IntegrityError

    db_session.add(Employee(full_name="Старый", tab_number="T-9001", is_active=True))
    db_session.commit()
    monkeypatch.setattr("app.routers.employees._ensure_tab_number_free", lambda *a, **k: None)
    tok = get_token(client, "admin@example.com", "admin123")
    with pytest.raises(IntegrityError, match="tab_number"):
        client.post("/api/employees", headers=_auth(tok), json={
            "full_name": "Новый", "tab_number": "T-9001", "access": {
                "email": "fresh@example.com", "role": "employee",
                "initial_password": "start-pass-1",
            },
        })
