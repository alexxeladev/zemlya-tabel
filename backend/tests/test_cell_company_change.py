"""Смена компании в ячейке табеля — одна транзакция (task_stage1 п.1.1).

Раньше фронт слал два отдельных сохранения: обнулить часы старой компании,
затем записать новой. Каждое коммитилось само, и сбой второго оставлял день без
часов. Теперь это один запрос: либо перенос выполнен целиком, либо не изменилось
ничего.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from tests.conftest import get_token

WORK_DATE = "2026-05-05"
URL = "/api/timesheet/cell/company"


def _add(db: Session, obj):
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@pytest.fixture
def dept(db_session: Session) -> Department:
    return _add(db_session, Department(name="Dept A", code="DA", is_active=True))


@pytest.fixture
def other_dept(db_session: Session) -> Department:
    return _add(db_session, Department(name="Dept B", code="DB", is_active=True))


@pytest.fixture
def company_a(db_session: Session) -> Company:
    return _add(db_session, Company(code="CA", name="Company A", is_active=True))


@pytest.fixture
def company_b(db_session: Session) -> Company:
    return _add(db_session, Company(code="CB", name="Company B", is_active=True))


def _user(db: Session, email: str, role: str, **kw) -> Employee:
    return _add(db, Employee(
        full_name=email.split("@")[0], email=email,
        hashed_password=hash_password("pass12345"), role=role,
        is_active=True, must_change_password=False, **kw,
    ))


@pytest.fixture
def admin(db_session: Session) -> Employee:
    return _user(db_session, "admin@example.com", "admin", is_system_admin=True)


@pytest.fixture
def worker(db_session: Session, dept: Department) -> Employee:
    return _user(db_session, "worker@example.com", "employee", department_id=dept.id)


@pytest.fixture
def headers(client: TestClient, admin: Employee) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'admin@example.com', 'pass12345')}"}


def _hours(db: Session, employee: Employee, company: Company, hours: int) -> TimesheetEntry:
    return _add(db, TimesheetEntry(
        employee_id=employee.id, position_id=employee.primary_position.id,
        work_date=date.fromisoformat(WORK_DATE), company_id=company.id, hours=hours,
    ))


def _body(employee: Employee, old: Company, new: Company) -> dict:
    return {
        "employee_id": employee.id, "work_date": WORK_DATE,
        "old_company_id": old.id, "new_company_id": new.id,
    }


def _day(db: Session, employee: Employee) -> dict[int, Decimal]:
    db.expire_all()
    rows = db.query(TimesheetEntry).filter_by(employee_id=employee.id).all()
    return {r.company_id: Decimal(r.hours) for r in rows}


# ── Сам перенос ───────────────────────────────────────────────────────────────

def test_hours_move_to_new_company(client, headers, db_session, worker, company_a, company_b):
    _hours(db_session, worker, company_a, 8)

    resp = client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["company_id"] == company_b.id
    assert Decimal(str(resp.json()["hours"])) == 8
    assert _day(db_session, worker) == {company_b.id: Decimal(8)}


def test_hours_come_from_the_stored_cell_not_from_the_client(
    client, headers, db_session, worker, company_a, company_b,
):
    """Переносится то, что лежит в базе: устаревший экран не подменит часы."""
    _hours(db_session, worker, company_a, 11)

    body = {**_body(worker, company_a, company_b), "hours": 3}
    resp = client.put(URL, json=body, headers=headers)

    assert resp.status_code == 200, resp.text
    assert _day(db_session, worker) == {company_b.id: Decimal(11)}


def test_existing_hours_of_new_company_are_overwritten(
    client, headers, db_session, worker, company_a, company_b,
):
    """Решение заказчика: часы новой компании ЗАМЕНЯЮТСЯ переносимыми, не складываются."""
    _hours(db_session, worker, company_a, 8)
    _hours(db_session, worker, company_b, 4)

    resp = client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    assert resp.status_code == 200, resp.text
    assert _day(db_session, worker) == {company_b.id: Decimal(8)}


def test_move_is_written_to_audit_log(client, headers, db_session, worker, company_a, company_b):
    _hours(db_session, worker, company_a, 8)

    client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    # По id записи не различить: SQLite отдаёт новой ячейке id только что удалённой.
    logs = db_session.query(AuditLog).filter_by(entity_type="timesheet_entry").order_by(AuditLog.id)
    assert [(log.action, log.before, log.after) for log in logs] == [
        ("delete", {"hours": "8"}, None),
        ("create", None, {"hours": "8"}),
    ]


# ── Атомарность: сбой любой части не меняет исходные данные ───────────────────

def test_failure_of_second_half_keeps_original_hours(
    client, headers, db_session, worker, company_a, company_b, monkeypatch,
):
    """Приёмка задачи: ломаем ВТОРУЮ часть операции — исходные часы на месте."""
    from app.services import timesheet as svc

    _hours(db_session, worker, company_a, 8)
    real = svc._upsert_cell_no_commit
    calls = []

    def broken(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("вторая половина переноса упала")
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "_upsert_cell_no_commit", broken)

    with pytest.raises(RuntimeError):
        client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    assert len(calls) == 2, "сбой должен случиться ПОСЛЕ удаления исходной ячейки"
    assert _day(db_session, worker) == {company_a.id: Decimal(8)}
    assert db_session.query(AuditLog).filter_by(entity_type="timesheet_entry").count() == 0


def test_unknown_new_company_keeps_original_hours(client, headers, db_session, worker, company_a):
    _hours(db_session, worker, company_a, 8)

    body = {**_body(worker, company_a, company_a), "new_company_id": 99999}
    resp = client.put(URL, json=body, headers=headers)

    assert resp.status_code == 404
    assert _day(db_session, worker) == {company_a.id: Decimal(8)}


def test_closed_period_rejects_move_and_keeps_hours(
    client, headers, db_session, worker, dept, company_a, company_b,
):
    _hours(db_session, worker, company_a, 8)
    _add(db_session, TimesheetPeriod(department_id=dept.id, year=2026, month=5, status="closed"))

    resp = client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    assert resp.status_code == 409
    assert _day(db_session, worker) == {company_a.id: Decimal(8)}


def test_day_outside_employment_period_rejects_move(
    client, headers, db_session, worker, company_a, company_b,
):
    _hours(db_session, worker, company_a, 8)
    worker.dismissal_date = date(2026, 5, 1)
    db_session.commit()

    resp = client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    assert resp.status_code == 422
    assert _day(db_session, worker) == {company_a.id: Decimal(8)}


# ── Отказы без изменений ──────────────────────────────────────────────────────

def test_no_source_hours_is_404(client, headers, db_session, worker, company_a, company_b):
    resp = client.put(URL, json=_body(worker, company_a, company_b), headers=headers)

    assert resp.status_code == 404
    assert _day(db_session, worker) == {}


def test_same_company_is_422(client, headers, db_session, worker, company_a):
    _hours(db_session, worker, company_a, 8)

    resp = client.put(URL, json=_body(worker, company_a, company_a), headers=headers)

    assert resp.status_code == 422
    assert _day(db_session, worker) == {company_a.id: Decimal(8)}


def test_manager_of_other_department_is_403(
    client, db_session, worker, other_dept, company_a, company_b,
):
    manager = _user(db_session, "boss@example.com", "manager", department_id=other_dept.id)
    manager.managed_departments = [other_dept]
    db_session.commit()
    _hours(db_session, worker, company_a, 8)
    token = get_token(client, "boss@example.com", "pass12345")

    resp = client.put(
        URL, json=_body(worker, company_a, company_b),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403
    assert _day(db_session, worker) == {company_a.id: Decimal(8)}


def test_anonymous_is_401(client, worker, company_a, company_b):
    assert client.put(URL, json=_body(worker, company_a, company_b)).status_code == 401
