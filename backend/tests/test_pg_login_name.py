"""Логин (часть почты до «@») — на PostgreSQL (решение заказчика 19.09.2026).

Проверки, которых нет на SQLite: индекс уникальности логина (split_part —
только Postgres) и отказ миграции c7d8e9f0a1b2 на уже существующих дублях.

    TEST_POSTGRES_URL=postgresql+psycopg://tabel:tabel@localhost:5432/tabel_test \\
        pytest -m postgres
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.employees import Employee
from tests.conftest import PG_URL

pytestmark = pytest.mark.postgres

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(*args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env={**os.environ, "DATABASE_URL": PG_URL},
        capture_output=True, text=True,
    )


def test_db_refuses_second_account_with_same_login(pg_sessions):
    """Две одновременные выдачи доступа обе прошли бы проверку приложения —
    второго останавливает индекс uq_employees_login_name."""
    with pg_sessions() as db:
        db.add(Employee(full_name="Иван А", email="ivan@a.ru", is_active=True))
        db.commit()
        db.add(Employee(full_name="Иван Б", email="ivan@b.ru", is_active=True))
        with pytest.raises(IntegrityError, match="uq_employees_login_name"):
            db.commit()


def test_migration_refuses_on_existing_duplicates(pg_sessions):
    down = _alembic("downgrade", "1033950c4ced")
    assert down.returncode == 0, down.stderr[-1500:]
    try:
        with pg_sessions() as db:
            db.execute(text(
                "insert into employees (full_name, email, is_active, must_change_password, "
                "is_system_admin, token_version) values "
                "('Регистр 1', 'Petrov@x.ru', true, false, false, 0), "
                "('Регистр 2', 'petrov@x.ru', true, false, false, 0), "
                "('Логин 1', 'sidorov@a.ru', true, false, false, 0), "
                "('Логин 2', 'Sidorov@b.ru', true, false, false, 0)"
            ))
            db.commit()
        up = _alembic("upgrade", "head")
        assert up.returncode != 0
        assert "почта отличается только регистром: petrov@x.ru" in up.stderr
        assert "одинаковый логин (часть почты до @): sidorov" in up.stderr
        with pg_sessions() as db:
            # Отказ ничего не поменял: версия прежняя, регистр почт исходный.
            version = db.execute(text("select version_num from alembic_version")).scalar()
            assert version == "1033950c4ced"
            mixed = db.execute(
                text("select count(*) from employees where email <> lower(email)")
            ).scalar()
            assert mixed == 2
    finally:
        with pg_sessions() as db:
            db.execute(text(
                "delete from employees "
                "where full_name like 'Регистр %' or full_name like 'Логин %'"
            ))
            db.commit()
        assert _alembic("upgrade", "head").returncode == 0


@pytest.mark.parametrize("email", [
    pytest.param("dup@example.com", id="same-email"),
    pytest.param("DUP@Example.com", id="same-email-other-case"),
    pytest.param("dup@other.org", id="same-login-other-domain"),
])
def test_concurrent_grant_conflicts_are_409(pg_client, pg_sessions, monkeypatch, email):
    """Гонку двух выдач доступа эмулируем, выключив проверку приложения:
    второго останавливает индекс Postgres, и ответ — 409, а не 500. Имена
    ограничений в ошибке — Postgres-овские (ix_employees_email и т.д.), на
    SQLite этот путь не проверить (нашло ревью: регрессия ушла незамеченной)."""
    from app.core.security import hash_password
    from tests.conftest import get_token

    with pg_sessions() as db:
        db.add_all([
            Employee(full_name="Админ", email="root@example.com", role="admin",
                     hashed_password=hash_password("root-pass-1"), is_active=True),
            Employee(full_name="Первый", email="dup@example.com", role="employee",
                     hashed_password=hash_password("dup-pass-1"), is_active=True),
        ])
        db.commit()
    monkeypatch.setattr("app.routers.employees.account_conflict", lambda *a, **k: None)
    token = get_token(pg_client, "root@example.com", "root-pass-1")
    resp = pg_client.post(
        "/api/employees", headers={"Authorization": f"Bearer {token}"},
        json={"full_name": "Второй", "access": {
            "email": email, "role": "employee", "initial_password": "start-pass-1",
        }},
    )
    assert resp.status_code == 409, resp.text
    with pg_sessions() as db:
        assert db.query(Employee).filter(Employee.full_name == "Второй").count() == 0
