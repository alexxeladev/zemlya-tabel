"""Оргструктура: мульти-отдел менеджера и изоляция чужих отделов.

task_org_structure ч.2. Проверяем сквозной доступ по ВСЕМ отделам, которыми
руководит менеджер (табель, ЗП, ведомость, сотрудники, дашборд, workflow,
экспорт, автозаполнение) и 403/404 на отдел, которым он не руководит.

Ключевое различие, которое здесь и проверяется:
  * `department_id`         — где менеджер ЧИСЛИТСЯ (может быть вообще пусто);
  * `managed_departments`   — чем он РУКОВОДИТ.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from tests.conftest import get_token

MAY_WORKDAY = date(2026, 5, 5)
# Выходные мая 2026 — как в test_payroll, чтобы норма считалась
MAY_CALENDAR = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}


@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_CALENDAR, source="manual")
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="OC", name="Org Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def dept_a(db_session: Session) -> Department:
    d = Department(name="ИТО", code="ITO", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def dept_b(db_session: Session) -> Department:
    d = Department(name="Бухгалтерия", code="BUH", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def dept_c(db_session: Session) -> Department:
    """Отдел, которым менеджер НЕ руководит."""
    d = Department(name="Охрана", code="SEC", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _worker(
    db_session, name: str, dept: Department, company: Company, schedule: Schedule
) -> Employee:
    emp = Employee(
        full_name=name,
        department_id=dept.id,
        default_company_id=company.id,
        schedule_id=schedule.id,
        rate=50000,
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def multi_manager(db_session: Session, dept_a: Department, dept_b: Department) -> Employee:
    """Руководит двумя отделами. Сам при этом не числится ни в одном —
    руководство и принадлежность к отделу независимы."""
    emp = Employee(
        full_name="Мульти Менеджер",
        email="multimgr@example.com",
        hashed_password=hash_password("mgr12345"),
        role="manager",
        is_active=True,
        must_change_password=False,
        department_id=None,
        managed_departments=[dept_a, dept_b],
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Org Admin",
        email="orgadmin@example.com",
        hashed_password=hash_password("admin123"),
        role="admin",
        is_active=True,
        must_change_password=False,
        is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def workers(db_session, dept_a, dept_b, dept_c, company, schedule) -> dict[str, Employee]:
    return {
        "a": _worker(db_session, "Сотрудник А", dept_a, company, schedule),
        "b": _worker(db_session, "Сотрудник Б", dept_b, company, schedule),
        "c": _worker(db_session, "Сотрудник Ч", dept_c, company, schedule),
    }


def _auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'multimgr@example.com', 'mgr12345')}"}


# ── Видимость по всем управляемым отделам ─────────────────────────────────────

def test_manager_sees_employees_of_all_managed_departments(
    client: TestClient, multi_manager: Employee, workers: dict
):
    resp = client.get("/api/employees", headers=_auth(client))
    assert resp.status_code == 200
    names = {e["full_name"] for e in resp.json()}
    assert {"Сотрудник А", "Сотрудник Б"} <= names
    assert "Сотрудник Ч" not in names


def test_timesheet_covers_all_managed_departments(
    client: TestClient, multi_manager: Employee, workers: dict
):
    resp = client.get("/api/timesheet/2026/5", headers=_auth(client))
    assert resp.status_code == 200
    ids = {e["id"] for e in resp.json()["employees"]}
    assert workers["a"].id in ids and workers["b"].id in ids
    assert workers["c"].id not in ids


def test_timesheet_department_filter_within_managed(
    client: TestClient, multi_manager: Employee, workers: dict, dept_b: Department
):
    """Фильтр по своему отделу сужает выдачу, а не игнорируется."""
    resp = client.get(f"/api/timesheet/2026/5?department_id={dept_b.id}", headers=_auth(client))
    assert resp.status_code == 200
    ids = {e["id"] for e in resp.json()["employees"]}
    assert ids == {workers["b"].id}


def test_payroll_covers_all_managed_departments(
    client: TestClient, multi_manager: Employee, workers: dict
):
    resp = client.get("/api/timesheet/2026/5/payroll", headers=_auth(client))
    assert resp.status_code == 200
    ids = {e["employee_id"] for e in resp.json()["employees"]}
    assert workers["a"].id in ids and workers["b"].id in ids
    assert workers["c"].id not in ids


def test_statement_covers_all_managed_departments(
    client: TestClient, multi_manager: Employee, workers: dict
):
    resp = client.get("/api/timesheet/2026/5/statement", headers=_auth(client))
    assert resp.status_code == 200
    ids = {r["employee_id"] for r in resp.json()["rows"]}
    assert workers["a"].id in ids and workers["b"].id in ids
    assert workers["c"].id not in ids


def test_dashboard_covers_all_managed_departments(
    client: TestClient, multi_manager: Employee, workers: dict,
    dept_a: Department, dept_b: Department, dept_c: Department,
):
    resp = client.get("/api/dashboard/2026/5", headers=_auth(client))
    assert resp.status_code == 200
    dept_ids = {p["department_id"] for p in resp.json()["periods"]["rows"]}
    assert {dept_a.id, dept_b.id} <= dept_ids
    assert dept_c.id not in dept_ids


def test_manager_can_edit_cells_in_any_managed_department(
    client: TestClient, multi_manager: Employee, workers: dict, company: Company
):
    for key in ("a", "b"):
        resp = client.put(
            "/api/timesheet/cell",
            json={
                "employee_id": workers[key].id,
                "work_date": MAY_WORKDAY.isoformat(),
                "company_id": company.id,
                "hours": 8,
            },
            headers=_auth(client),
        )
        assert resp.status_code == 200, key


def test_manager_can_submit_any_managed_department(
    client: TestClient, multi_manager: Employee, workers: dict, db_session: Session
):
    # Периоды создаются лениво при GET месяца
    client.get("/api/timesheet/2026/5", headers=_auth(client))
    resp = client.get("/api/timesheet/2026/5", headers=_auth(client))
    periods = resp.json()["periods"]
    assert len(periods) == 2
    for p in periods:
        assert p["can_submit"] is True
        sub = client.post(f"/api/timesheet/periods/{p['id']}/submit", headers=_auth(client))
        assert sub.status_code == 200


# ── Изоляция чужих отделов ────────────────────────────────────────────────────

def test_foreign_department_forbidden_everywhere(
    client: TestClient, multi_manager: Employee, workers: dict, dept_c: Department
):
    headers = _auth(client)
    urls = [
        f"/api/timesheet/2026/5?department_id={dept_c.id}",
        f"/api/timesheet/2026/5/payroll?department_id={dept_c.id}",
        f"/api/timesheet/2026/5/statement?department_id={dept_c.id}",
        f"/api/timesheet/2026/5/adjustments?department_id={dept_c.id}",
        f"/api/timesheet/2026/5/export/excel?department_id={dept_c.id}",
        f"/api/timesheet/2026/5/statement/export/excel?department_id={dept_c.id}",
    ]
    for url in urls:
        assert client.get(url, headers=headers).status_code == 403, url

    for url in ("/api/timesheet/autofill/preview", "/api/timesheet/autofill/apply"):
        resp = client.post(
            url, json={"year": 2026, "month": 5, "department_id": dept_c.id}, headers=headers
        )
        assert resp.status_code == 403, url


def test_foreign_department_employee_hidden(
    client: TestClient, multi_manager: Employee, workers: dict, company: Company
):
    headers = _auth(client)
    assert client.get(f"/api/employees/{workers['c'].id}", headers=headers).status_code == 404
    resp = client.put(
        "/api/timesheet/cell",
        json={
            "employee_id": workers["c"].id,
            "work_date": MAY_WORKDAY.isoformat(),
            "company_id": company.id,
            "hours": 8,
        },
        headers=headers,
    )
    assert resp.status_code == 403


def test_foreign_period_cannot_be_submitted(
    client: TestClient, admin: Employee, multi_manager: Employee,
    workers: dict, dept_c: Department,
):
    # Период чужого отдела создаёт admin, открыв месяц
    admin_token = get_token(client, "orgadmin@example.com", "admin123")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    client.get("/api/timesheet/2026/5", headers=admin_headers)
    resp = client.get("/api/timesheet/2026/5", headers=admin_headers)
    foreign = next(p for p in resp.json()["periods"] if p["department_id"] == dept_c.id)

    sub = client.post(f"/api/timesheet/periods/{foreign['id']}/submit", headers=_auth(client))
    assert sub.status_code == 403


def test_manager_without_managed_departments_sees_nothing(
    client: TestClient, db_session: Session, workers: dict, dept_a: Department
):
    """Числится в отделе, но не назначен его руководителем → доступа нет.
    Принадлежность к отделу сама по себе прав не даёт."""
    emp = Employee(
        full_name="Менеджер без отделов",
        email="nomgr@example.com",
        hashed_password=hash_password("mgr12345"),
        role="manager",
        is_active=True,
        must_change_password=False,
        department_id=dept_a.id,
    )
    db_session.add(emp)
    db_session.commit()

    headers = {"Authorization": f"Bearer {get_token(client, 'nomgr@example.com', 'mgr12345')}"}
    assert client.get("/api/employees", headers=headers).json() == []
    assert client.get("/api/timesheet/2026/5", headers=headers).json()["employees"] == []
    assert client.get("/api/timesheet/2026/5/payroll", headers=headers).json()["employees"] == []


def test_managed_departments_exposed_in_me(client: TestClient, multi_manager: Employee,
                                            dept_a: Department, dept_b: Department):
    resp = client.get("/api/auth/me", headers=_auth(client))
    assert resp.status_code == 200
    assert resp.json()["managed_department_ids"] == sorted([dept_a.id, dept_b.id])


# ── Расчёты не изменились (acceptance 8) ──────────────────────────────────────

def test_payroll_still_splits_by_actual_companies(
    client: TestClient, admin: Employee, workers: dict, company: Company,
    dept_a: Department, calendar_2026: ProductionCalendar, db_session: Session,
):
    """Головная компания отдела на распределение по юрлицам не влияет:
    часы разносятся по компаниям из табеля, как и раньше."""
    other = Company(code="OT2", name="Другое юрлицо", is_active=True)
    db_session.add(other)
    db_session.flush()
    dept_a.head_company_id = company.id  # ярлык для дерева

    emp = workers["a"]
    for d in (5, 6):
        db_session.add(TimesheetEntry(
            employee_id=emp.id, work_date=date(2026, 5, d), company_id=company.id, hours=8
        ))
    for d in (7, 8):
        db_session.add(TimesheetEntry(
            employee_id=emp.id, work_date=date(2026, 5, d), company_id=other.id, hours=8
        ))
    db_session.commit()

    headers = {"Authorization": f"Bearer {get_token(client, 'orgadmin@example.com', 'admin123')}"}
    resp = client.get("/api/timesheet/2026/5/payroll", headers=headers)
    row = next(e for e in resp.json()["employees"] if e["employee_id"] == emp.id)
    by_company = {b["company_id"]: b for b in row["breakdown_by_company"]}
    assert set(by_company) == {company.id, other.id}
    assert Decimal(by_company[company.id]["hours"]) == Decimal("16")
    assert Decimal(by_company[other.id]["hours"]) == Decimal("16")


# ── Дерево оргструктуры (task_org_structure ч.3) ──────────────────────────────

def _admin_headers(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'orgadmin@example.com', 'admin123')}"}


def test_org_tree_groups_departments_under_head_company(
    client: TestClient, admin: Employee, workers: dict, company: Company,
    dept_a: Department, dept_b: Department, db_session: Session,
):
    dept_a.head_company_id = company.id
    dept_b.head_company_id = company.id
    db_session.commit()

    resp = client.get("/api/org/tree", headers=_admin_headers(client))
    assert resp.status_code == 200
    tree = resp.json()

    node = next(c for c in tree["companies"] if c["id"] == company.id)
    names = [d["name"] for d in node["departments"]]
    assert "ИТО" in names and "Бухгалтерия" in names

    ito = next(d for d in node["departments"] if d["name"] == "ИТО")
    assert ito["employee_count"] == 1
    assert [e["full_name"] for e in ito["employees"]] == ["Сотрудник А"]


def test_org_tree_keeps_orphans_visible(
    client: TestClient, admin: Employee, workers: dict, dept_c: Department,
    company: Company, schedule: Schedule, db_session: Session,
):
    """Отдел без головной компании и сотрудник без отдела не должны исчезать —
    иначе их нельзя будет починить из дерева."""
    orphan = Employee(full_name="Ничейный Сотрудник", is_active=True,
                      default_company_id=company.id, schedule_id=schedule.id)
    db_session.add(orphan)
    db_session.commit()

    tree = client.get("/api/org/tree", headers=_admin_headers(client)).json()
    assert dept_c.name in [d["name"] for d in tree["departments_without_company"]]
    assert "Ничейный Сотрудник" in [
        e["full_name"] for e in tree["employees_without_department"]
    ]


def test_org_tree_shows_department_managers(
    client: TestClient, admin: Employee, multi_manager: Employee,
    company: Company, dept_a: Department, db_session: Session,
):
    dept_a.head_company_id = company.id
    db_session.commit()

    tree = client.get("/api/org/tree", headers=_admin_headers(client)).json()
    node = next(c for c in tree["companies"] if c["id"] == company.id)
    ito = next(d for d in node["departments"] if d["id"] == dept_a.id)
    assert [m["full_name"] for m in ito["managers"]] == ["Мульти Менеджер"]


def test_org_tree_admin_only(client: TestClient, multi_manager: Employee):
    assert client.get("/api/org/tree", headers=_auth(client)).status_code == 403


def test_org_tree_hides_system_admin(client: TestClient, admin: Employee, workers: dict):
    tree = client.get("/api/org/tree", headers=_admin_headers(client)).json()
    everyone = [e["full_name"] for e in tree["employees_without_department"]]
    for c in tree["companies"]:
        for d in c["departments"]:
            everyone += [e["full_name"] for e in d["employees"]]
    assert "Org Admin" not in everyone
