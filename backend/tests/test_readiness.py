"""Проверка готовности с доступом к базе (этап 4 п.4.2).

Негативные проверки, воспроизводящие исходную проблему: `/health` возвращал
константу и отвечал «ок» при лежащей базе и при схеме без миграций.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app
from app.services import readiness


class _DeadSession:
    """База не отвечает: любой запрос — OperationalError, как при погашенном
    контейнере Postgres."""

    def execute(self, *args, **kwargs):
        raise OperationalError("select 1", {}, Exception("connection refused"))

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def dead_db_client():
    def override_get_db():
        yield _DeadSession()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _stamp(db_session, revision: str) -> None:
    """Пометить схему ревизией, как это делает `alembic upgrade`."""
    db_session.execute(text("create table if not exists alembic_version (version_num varchar(32))"))
    db_session.execute(text("delete from alembic_version"))
    db_session.execute(
        text("insert into alembic_version (version_num) values (:v)"), {"v": revision}
    )
    db_session.commit()


# ── «Жив» ────────────────────────────────────────────────────────────────────

def test_health_is_alive_even_without_database(dead_db_client):
    """Живость не должна зависеть от базы: иначе падение базы уводит живой
    процесс в бесконечный рестарт."""
    resp = dead_db_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── «Готов» ──────────────────────────────────────────────────────────────────

def test_ready_refuses_when_database_is_down(dead_db_client):
    resp = dead_db_client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["database"] == "unavailable"
    assert body["reason"] == "База данных недоступна"


def test_ready_does_not_leak_connection_string(dead_db_client):
    """Текст ошибки базы содержит строку подключения с паролем — наружу идёт
    только род поломки."""
    body = dead_db_client.get("/ready").text
    for secret in ("password", "postgresql+psycopg", "connection refused", "@localhost"):
        assert secret not in body


def test_ready_refuses_when_migrations_were_never_applied(client):
    """Схема собрана мимо Alembic (в тестах — `create_all`): таблицы
    alembic_version нет, сверять нечего, трафик пускать нельзя."""
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["database"] == "ok"
    assert body["migrations"]["state"] == "not_applied"
    assert "alembic_version" in body["reason"]


def test_ready_refuses_when_schema_is_behind_code(client, db_session):
    _stamp(db_session, "0000deadbeef")
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["migrations"]["state"] == "mismatch"
    assert body["migrations"]["applied"] == "0000deadbeef"
    assert "alembic upgrade head" in body["reason"]


def test_ready_confirms_when_schema_is_at_head(client, db_session):
    head = readiness.migration_head()
    assert head, "Каталог миграций должен читаться из тестов"
    _stamp(db_session, head)
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["migrations"] == {"state": "ok", "applied": head, "head": head}


def test_ready_refuses_when_migrations_directory_is_missing(client, db_session, monkeypatch):
    """Каталога миграций рядом нет (неправильно собранный образ) — сверять
    версию нечем, и это ОТКАЗ, а не «готов с оговоркой»: иначе healthcheck
    зеленеет над схемой, которую никто не проверял."""
    _stamp(db_session, "0000deadbeef")
    monkeypatch.setattr(readiness, "migration_head", lambda: None)
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["database"] == "ok"
    assert body["migrations"]["state"] == "head_unknown"
    assert "каталога миграций" in body["reason"]


def test_migration_head_matches_alembic_heads():
    """Версия читается из тех же файлов, что применяет alembic: разойдясь,
    проверка готовности начала бы врать в обе стороны."""
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=backend, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert readiness.migration_head() in out.stdout
