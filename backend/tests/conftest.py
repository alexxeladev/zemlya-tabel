import os
import subprocess
import sys
from pathlib import Path

# Сервер не стартует с дефолтным SECRET_KEY (task_stage2_access п.2.6), а
# TestClient поднимает lifespan. Ставится ДО импорта приложения: Settings
# читает окружение при импорте app.config.
os.environ.setdefault("SECRET_KEY", "test-only-secret-" + "0" * 32)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.database import Base, get_db
from app.main import app
from app.models.employees import Employee

SQLITE_URL = "sqlite://"

engine = create_engine(
    SQLITE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed_guard_job_titles(session_factory) -> None:
    """Четыре должности охраны «из коробки» — без них вахте некого ставить на
    пост. В проде их заводит миграция, здесь схема строится `create_all`."""
    from app.services.guard_job_titles import seed_default_job_titles

    db = session_factory()
    try:
        seed_default_job_titles(db)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    _seed_guard_job_titles(TestingSessionLocal)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(setup_db):
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db_session) -> Employee:
    emp = Employee(
        full_name="Test Admin",
        email="admin@example.com",
        hashed_password=hash_password("admin123"),
        role="admin",
        is_active=True,
        # False: с обязательной сменой пароля сессия ограничена профилем и
        # сменой пароля (task_stage2_access п.2.2) — фикстура, на которой
        # держится половина тестов, иначе ни до чего бы не дошла. Сам режим
        # проверяется своими пользователями в test_auth.py.
        must_change_password=False,
        is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def manager_user(db_session) -> Employee:
    emp = Employee(
        full_name="Test Manager",
        email="manager@example.com",
        hashed_password=hash_password("manager123"),
        role="manager",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def inactive_user(db_session) -> Employee:
    emp = Employee(
        full_name="Inactive User",
        email="inactive@example.com",
        hashed_password=hash_password("pass123"),
        role="employee",
        is_active=False,
        must_change_password=False,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


# ── PostgreSQL: интеграционные тесты (маркер `postgres`) ──────────────────────
#
# Основной набор идёт на SQLite in-memory: быстро, но блокировок строк там нет
# (`SELECT … FOR UPDATE` диалект просто опускает), соединение одно, и гонку
# транзакций на нём не воспроизвести. Всё, что про конкурентность, живёт здесь.
#
#   TEST_POSTGRES_URL=postgresql+psycopg://tabel:tabel@localhost:5432/tabel_test \
#       pytest -m postgres
#
# Без переменной тесты ПРОПУСКАЮТСЯ — обычный `pytest` остаётся зелёным и без
# Postgres. База обязана называться `*_test`: фикстура чистит её целиком, и
# направить её на дев-базу нельзя даже по ошибке. Схема строится миграциями
# Alembic, а не `create_all` — заодно проверяется, что цепочка миграций
# применяется на пустую базу.

PG_URL = os.environ.get("TEST_POSTGRES_URL")
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _ensure_pg_database(url) -> None:
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("select 1 from pg_database where datname = :n"), {"n": url.database}
            ).scalar()
            if not exists:
                conn.execute(text(f'create database "{url.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def pg_engine():
    if not PG_URL:
        pytest.skip("TEST_POSTGRES_URL не задан — интеграционные тесты на PostgreSQL пропущены")
    url = make_url(PG_URL)
    assert url.get_backend_name() == "postgresql", "TEST_POSTGRES_URL должен указывать на PostgreSQL"
    assert (url.database or "").endswith("_test"), (
        f"База «{url.database}» не похожа на тестовую: фикстура чистит все таблицы, "
        "поэтому имя обязано заканчиваться на _test"
    )
    _ensure_pg_database(url)
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR, env={**os.environ, "DATABASE_URL": PG_URL},
        capture_output=True, text=True,
    )
    assert migrate.returncode == 0, "alembic upgrade head упал:\n" + migrate.stderr[-2000:]
    engine = create_engine(PG_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_sessions(pg_engine):
    """Фабрика сессий на ЧИСТОЙ базе: сессия на запрос, как в проде."""
    with pg_engine.begin() as conn:
        tables = conn.execute(text(
            "select tablename from pg_tables "
            "where schemaname = 'public' and tablename <> 'alembic_version'"
        )).scalars().all()
        if tables:
            names = ", ".join(f'"{t}"' for t in tables)
            conn.execute(text(f"truncate {names} restart identity cascade"))
    factory = sessionmaker(bind=pg_engine, autocommit=False, autoflush=False)
    _seed_guard_job_titles(factory)
    return factory


@pytest.fixture
def pg_client(pg_sessions):
    """TestClient поверх Postgres. В отличие от `client`, сессия у КАЖДОГО запроса
    своя — иначе два одновременных запроса делили бы одну транзакцию и гонки бы
    не было по построению."""
    def override_get_db():
        db = pg_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def get_token(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]
