import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
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
        must_change_password=True,
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


def get_token(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]
