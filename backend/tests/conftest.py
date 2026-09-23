import os
import shutil
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


# ── Дамп и восстановление (этап 4 п.4.3/4.5) ─────────────────────────────────
#
# Бэкап, который никто не разворачивал, — не бэкап. Поэтому проверка
# восстановления в тестах настоящая: pg_dump → отдельная база → чтение данных.
#
# Утилиты берутся с хоста, а если их там нет (обычный случай на машине, где
# Postgres живёт только в Docker) — из КОНТЕЙНЕРА, слушающего тот же порт.
# Поток идёт через stdin/stdout, поэтому оба пути работают одинаково и файл
# внутрь контейнера класть не надо.


class PgDumpTools:
    def __init__(self, url, exec_prefix: list[str] | None):
        self.url = url
        self._prefix = exec_prefix or []

    def _run(self, tool: str, args: list[str], stdin: str | None = None) -> str:
        cmd = list(self._prefix) + [tool, "-U", self.url.username or "postgres"]
        if not self._prefix:  # хостовая утилита — ей нужны адрес и порт
            cmd += ["-h", self.url.host or "localhost", "-p", str(self.url.port or 5432)]
        cmd += args
        env = {**os.environ}
        if self.url.password and not self._prefix:
            env["PGPASSWORD"] = self.url.password
        done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env)
        if done.returncode != 0:
            raise AssertionError(f"{tool} упал: {done.stderr[-2000:]}")
        return done.stdout

    def dump(self, database: str | None = None) -> str:
        return self._run("pg_dump", ["-d", database or self.url.database])

    def psql(self, database: str, sql: str) -> str:
        return self._run("psql", ["-d", database, "-v", "ON_ERROR_STOP=1", "-q"], stdin=sql)

    def create_database(self, name: str) -> None:
        self.psql("postgres", f'drop database if exists "{name}";')
        self.psql("postgres", f'create database "{name}";')

    def drop_database(self, name: str) -> None:
        self.psql("postgres", f'drop database if exists "{name}";')


def _docker_container_for_port(port: int) -> str | None:
    """Имя контейнера, опубликовавшего этот порт наружу."""
    try:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if names.returncode != 0:
        return None
    for name in names.stdout.split():
        ports = subprocess.run(
            ["docker", "port", name], capture_output=True, text=True, timeout=20
        )
        if ports.returncode == 0 and f":{port}" in ports.stdout:
            return name
    return None


@pytest.fixture(scope="session")
def pg_tools(pg_engine):
    url = make_url(PG_URL)
    if shutil.which("pg_dump") and shutil.which("psql"):
        return PgDumpTools(url, None)
    container = _docker_container_for_port(url.port or 5432)
    if container:
        return PgDumpTools(url, ["docker", "exec", "-i", container])
    pytest.skip(
        "Нет ни pg_dump/psql на хосте, ни контейнера Postgres на этом порту — "
        "проверку восстановления запустить нечем"
    )


# ── Список маршрутов приложения ──────────────────────────────────────────────
#
# Берётся из схемы OpenAPI, а НЕ из `app.routes`: начиная с FastAPI 0.138
# (starlette 1.3) `include_router` больше не раскладывает маршруты плоским
# списком — в `app.routes` остаются объекты-обёртки, и обход видит 6 маршрутов
# вместо 127. Тесты-сторожа, которые ходят по всем маршрутам (ограниченная
# сессия в test_auth, сканер утечек финансов), на таком обходе слепнут, оставаясь
# зелёными. Поймано прогоном на закреплённом составе (этап 4 п.4.1): в дев-venv
# стоял FastAPI 0.136, в прод-образе 0.138.
#
# Схема — публичный и стабильный источник: она одинакова в обеих версиях.


def app_routes() -> list[tuple[str, str]]:
    """[(МЕТОД, путь)] всех маршрутов приложения, отсортировано."""
    schema = app.openapi()
    return sorted(
        (method.upper(), path)
        for path, operations in schema.get("paths", {}).items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS", "PARAMETERS"}
    )


def get_token(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]
