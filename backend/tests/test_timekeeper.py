"""Роль «Табельщик» (task_timekeeper_role).

Табельщик ведёт время своих отделов и НЕ видит финансов. Здесь проверяется
именно граница между этим: что он может писать часы и отсутствия, но зарплата
не приходит ему даже прямым запросом к API — скрыть колонки в UI недостаточно.

Отделы у него те же `managed_departments`, что у менеджера, поэтому отдельно
проверяется изоляция чужого отдела.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from tests.conftest import get_token

WORKDAY = date(2026, 5, 5)
MAY_CALENDAR = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}

# Денежные поля, которых табельщик не должен увидеть. Список зеркалит
# app.services.finance_masking — если там появится новое поле, а здесь нет, тест
# перестанет ловить утечку. Ставки ночной смены на позиции нет: она вычисляется
# из фонда отдела и гасится в строке расчёта (task_night_shifts_rework).
POSITION_MONEY_FIELDS = (
    "rate",
    "shift_rate",
    "hour_rate",
    "weekend_coefficient",
    "weekend_fixed_rate",
    "holiday_coefficient",
    "holiday_fixed_rate",
    "overtime_coefficient",
)
EMPLOYEE_MONEY_FIELDS = POSITION_MONEY_FIELDS + (
    "loan_amount", "loan_term_months", "loan_start_date",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_CALENDAR, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="TK", name="Timekeeper Co", is_active=True)
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
def own_dept(db_session: Session) -> Department:
    d = Department(name="ИТО", code="ITO", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def other_dept(db_session: Session) -> Department:
    """Отдел, к которому табельщик НЕ привязан."""
    d = Department(name="Охрана", code="SEC", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _worker(
    db_session, name: str, tab_number: str, dept: Department, company: Company,
    schedule: Schedule,
):
    emp = Employee(
        full_name=name,
        tab_number=tab_number,
        position="Инженер",
        department_id=dept.id,
        default_company_id=company.id,
        schedule_id=schedule.id,
        rate=50000,
        loan_amount=12000,
        loan_term_months=6,
        loan_start_date=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def workers(db_session, own_dept, other_dept, company, schedule) -> dict[str, Employee]:
    return {
        "own": _worker(db_session, "Сотрудник Свой", "T-001", own_dept, company, schedule),
        "other": _worker(db_session, "Сотрудник Чужой", "T-002", other_dept, company, schedule),
    }


@pytest.fixture
def timekeeper(db_session: Session, own_dept: Department) -> Employee:
    """Табельщик своего отдела. Как и менеджер, сам в нём может не числиться."""
    emp = Employee(
        full_name="Тест Табельщик",
        email="timekeeper@example.com",
        hashed_password=hash_password("tk123456"),
        role="timekeeper",
        is_active=True,
        must_change_password=False,
        department_id=None,
        managed_departments=[own_dept],
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="TK Admin",
        email="tkadmin@example.com",
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


def _auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'timekeeper@example.com', 'tk123456')}"}


def _admin_auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {get_token(client, 'tkadmin@example.com', 'admin123')}"}


# ── Роль существует и назначается ──────────────────────────────────────────────

def test_timekeeper_can_login(client: TestClient, timekeeper: Employee):
    resp = client.post(
        "/api/auth/login", json={"email": "timekeeper@example.com", "password": "tk123456"}
    )
    assert resp.status_code == 200
    me = client.get("/api/auth/me", headers=_auth(client))
    assert me.json()["role"] == "timekeeper"


def test_admin_assigns_timekeeper_role_and_departments(
    client: TestClient, admin: Employee, own_dept: Department, db_session: Session
):
    """Admin выдаёт роль табельщика и привязывает отдел через список менеджеров
    отдела — связь одна и та же (managed_departments)."""
    created = client.post(
        "/api/employees",
        json={
            "full_name": "Новый Табельщик",
            "access": {
                "email": "newtk@example.com",
                "role": "timekeeper",
                "initial_password": "tkpass12",
            },
        },
        headers=_admin_auth(client),
    )
    assert created.status_code == 201, created.text
    emp_id = created.json()["id"]

    resp = client.put(
        f"/api/departments/{own_dept.id}/managers",
        json={"employee_ids": [emp_id]},
        headers=_admin_auth(client),
    )
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["managers"]] == [emp_id]
    assert resp.json()["managers"][0]["role"] == "timekeeper"

    db_session.expire_all()
    assert db_session.get(Employee, emp_id).managed_department_ids == [own_dept.id]


def test_role_change_from_timekeeper_drops_departments(
    client: TestClient, admin: Employee, timekeeper: Employee, own_dept: Department,
    db_session: Session,
):
    """Сменили роль на «сотрудник» — привязка к отделам снимается, иначе он
    остался бы в списке отдела, уже не будучи табельщиком."""
    resp = client.patch(
        f"/api/employees/{timekeeper.id}/access",
        json={"role": "employee"},
        headers=_admin_auth(client),
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(Employee, timekeeper.id).managed_department_ids == []


# ── Что видит: время без денег ─────────────────────────────────────────────────

def test_timesheet_shows_own_department_without_money(
    client: TestClient, timekeeper: Employee, workers: dict, calendar_2026,
):
    resp = client.get("/api/timesheet/2026/5?include_payroll=true", headers=_auth(client))
    assert resp.status_code == 200
    data = resp.json()

    # Время видно: сотрудник своего отдела, его должность, отдел, график
    ids = {e["id"] for e in data["employees"]}
    assert ids == {workers["own"].id}
    emp = data["employees"][0]
    assert emp["full_name"] == "Сотрудник Свой"
    assert emp["tab_number"] == "T-001"
    assert emp["schedule"]["name"] == "5/2"
    assert emp["department"]["code"] == "ITO"
    assert data["positions_by_employee"][str(workers["own"].id)][0]["display_title"]

    # Расчёт приходит РАДИ ЧАСОВ (норма, переработка, дни отсутствий), но без
    # денег — суммы обнулены, ставки сняты. Премии/KPI не приходят вовсе.
    assert data["payroll"] is not None
    assert data["adjustments"] == []
    for row in data["payroll"]["employees"]:
        for field in ("base_amount", "overtime_amount", "off_schedule_amount",
                      "holiday_amount", "total_amount", "vacation_amount", "sick_amount",
                      "premium_amount", "kpi_amount", "total_deductions", "net_payout"):
            assert Decimal(row[field]) == 0, f"утекла сумма {field}"
        for field in ("rate", "hourly_rate", "shift_rate", "hour_rate",
                      "weekend_coefficient", "weekend_fixed_rate",
                      "holiday_coefficient", "holiday_fixed_rate"):
            assert row[field] is None, f"утекла ставка {field}"
    totals = data["payroll"]
    for field in ("total_base_amount", "total_overtime_amount", "total_holiday_amount",
                  "total_vacation_amount", "total_sick_amount", "grand_total",
                  "total_premium", "total_kpi", "total_deductions", "total_net_payout"):
        assert Decimal(totals[field]) == 0, f"утёк итог {field}"
    for field in EMPLOYEE_MONEY_FIELDS:
        assert emp[field] is None, f"утекло поле {field}"
    for position in data["positions_by_employee"][str(workers["own"].id)]:
        for field in POSITION_MONEY_FIELDS:
            assert position[field] is None, f"утекло поле позиции {field}"


def test_timesheet_response_has_no_money_anywhere(
    client: TestClient, timekeeper: Employee, workers: dict, calendar_2026,
):
    """Никакого «50000» в сыром ответе: оклад не должен просочиться ни одним полем."""
    resp = client.get("/api/timesheet/2026/5?include_payroll=true", headers=_auth(client))
    assert resp.status_code == 200
    assert "50000" not in resp.text
    assert "12000" not in resp.text  # займ


def test_timekeeper_sees_all_hour_categories(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
    calendar_2026, db_session: Session,
):
    """Табельщик ведёт время, поэтому ЧАСЫ он должен видеть все: норму, Δ,
    переработку, часы вне графика, дни отпуска и больничного. Без этого он не
    поймёт, правильно ли заполнил табель."""
    emp_id = workers["own"].id
    db_session.add_all([
        # плановый день 5/2 с переработкой: 12 ч при дневной норме 8
        TimesheetEntry(employee_id=emp_id, work_date=date(2026, 5, 5),
                       company_id=company.id, hours=12),
        # 10 мая — нерабочий по тестовому календарю (MAY_CALENDAR), то есть
        # выход в свой выходной: все часы дня идут в категорию «вне графика»
        TimesheetEntry(employee_id=emp_id, work_date=date(2026, 5, 10),
                       company_id=company.id, hours=6),
    ])
    db_session.commit()
    client.put(
        "/api/timesheet/absence",
        json={"employee_id": emp_id, "work_date": "2026-05-06", "kind": "vacation"},
        headers=_auth(client),
    )
    client.put(
        "/api/timesheet/absence",
        json={"employee_id": emp_id, "work_date": "2026-05-07", "kind": "sick"},
        headers=_auth(client),
    )

    data = client.get(
        "/api/timesheet/2026/5?include_payroll=true", headers=_auth(client)
    ).json()
    row = next(r for r in data["payroll"]["employees"] if r["employee_id"] == emp_id)

    assert Decimal(row["total_hours"]) == 18          # 12 + 6
    assert Decimal(row["norm_hours"]) > 0             # норма по графику 5/2
    assert row["delta_hours"] is not None
    assert Decimal(row["overtime_hours"]) == 4        # 12 − 8 в плановый день
    assert Decimal(row["off_schedule_hours"]) == 6    # суббота целиком
    assert row["vacation_days"] == 1
    assert row["sick_days"] == 1
    assert row["sick_limit_remaining"] >= 0           # остаток лимита виден
    assert row["norm_days"] and row["fact_days"]
    # …и при этом ни рубля
    assert Decimal(row["total_amount"]) == 0
    assert row["rate"] is None


def test_timesheet_period_status_visible(
    client: TestClient, timekeeper: Employee, workers: dict,
):
    resp = client.get("/api/timesheet/2026/5", headers=_auth(client))
    assert resp.status_code == 200
    statuses = {p["status"] for p in resp.json()["periods"]}
    assert statuses == {"draft"}


def test_employee_card_and_positions_without_money(
    client: TestClient, timekeeper: Employee, workers: dict,
):
    """Прямой запрос карточки и позиций сотрудника — тоже без ставок."""
    emp_id = workers["own"].id
    card = client.get(f"/api/employees/{emp_id}", headers=_auth(client))
    assert card.status_code == 200
    for field in EMPLOYEE_MONEY_FIELDS:
        assert card.json()[field] is None, f"утекло поле {field}"

    positions = client.get(f"/api/employees/{emp_id}/positions", headers=_auth(client))
    assert positions.status_code == 200
    assert positions.json()  # позиция есть — скрыты только деньги
    for position in positions.json():
        for field in POSITION_MONEY_FIELDS:
            assert position[field] is None, f"утекло поле позиции {field}"


def test_employees_list_without_money(
    client: TestClient, timekeeper: Employee, workers: dict,
):
    resp = client.get("/api/employees", headers=_auth(client))
    assert resp.status_code == 200
    assert {e["full_name"] for e in resp.json()} == {"Сотрудник Свой"}
    assert "50000" not in resp.text


# ── Что может: часы, отсутствия, автозаполнение ────────────────────────────────

def test_timekeeper_writes_hours(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
    db_session: Session,
):
    resp = client.put(
        "/api/timesheet/cell",
        json={
            "employee_id": workers["own"].id,
            "work_date": WORKDAY.isoformat(),
            "company_id": company.id,
            "hours": 8,
        },
        headers=_auth(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hours"] == 8
    assert db_session.query(TimesheetEntry).filter_by(employee_id=workers["own"].id).count() == 1


def test_timekeeper_sets_absence(
    client: TestClient, timekeeper: Employee, workers: dict,
):
    resp = client.put(
        "/api/timesheet/absence",
        json={
            "employee_id": workers["own"].id,
            "work_date": WORKDAY.isoformat(),
            "kind": "vacation",
        },
        headers=_auth(client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == "ОТ"


def test_timekeeper_autofill(
    client: TestClient, timekeeper: Employee, workers: dict, calendar_2026,
):
    preview = client.post(
        "/api/timesheet/autofill/preview",
        json={"year": 2026, "month": 5},
        headers=_auth(client),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["entries_to_create"]

    applied = client.post(
        "/api/timesheet/autofill/apply",
        json={"year": 2026, "month": 5},
        headers=_auth(client),
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["entries_created"] > 0


def test_timekeeper_exports_t13(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
    calendar_2026, db_session: Session,
):
    """Т-13 — только часы, рублей в файле нет, поэтому выгрузка разрешена."""
    db_session.add(TimesheetEntry(
        employee_id=workers["own"].id, work_date=WORKDAY,
        company_id=company.id, hours=8,
    ))
    db_session.commit()
    resp = client.get("/api/timesheet/2026/5/export/excel", headers=_auth(client))
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # xlsx


# ── Чего не может: финансы (403 на уровне API) ─────────────────────────────────

def test_payroll_endpoint_forbidden(client: TestClient, timekeeper: Employee, workers: dict):
    assert client.get("/api/timesheet/2026/5/payroll", headers=_auth(client)).status_code == 403


def test_statement_endpoints_forbidden(client: TestClient, timekeeper: Employee, workers: dict):
    headers = _auth(client)
    assert client.get("/api/timesheet/2026/5/statement", headers=headers).status_code == 403
    assert client.get(
        "/api/timesheet/2026/5/statement/export/excel", headers=headers
    ).status_code == 403


def test_adjustments_forbidden(
    client: TestClient, timekeeper: Employee, workers: dict, db_session: Session,
):
    headers = _auth(client)
    assert client.get("/api/timesheet/2026/5/adjustments", headers=headers).status_code == 403
    assert client.post(
        "/api/timesheet/adjustments",
        json={
            "employee_id": workers["own"].id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "5000", "reason": "проверка",
        },
        headers=headers,
    ).status_code == 403
    # И ничего не записалось
    assert db_session.query(EmployeeAdjustment).count() == 0


def test_loan_override_forbidden(client: TestClient, timekeeper: Employee, workers: dict):
    resp = client.post(
        "/api/timesheet/loan-override",
        json={
            "employee_id": workers["own"].id, "year": 2026, "month": 5,
            "actual_amount": "1000",
        },
        headers=_auth(client),
    )
    assert resp.status_code == 403


def test_distribution_endpoints_forbidden(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
):
    headers = _auth(client)
    assert client.put(
        "/api/timesheet/distribution",
        json={
            "employee_id": workers["own"].id, "year": 2026, "month": 5,
            "shares": [{"company_id": company.id, "percent": "100"}],
        },
        headers=headers,
    ).status_code == 403
    assert client.delete(
        f"/api/timesheet/distribution/{workers['own'].id}/2026/5", headers=headers
    ).status_code == 403
    assert client.get(
        f"/api/employees/{workers['own'].id}/company-shares", headers=headers
    ).status_code == 403


def test_dashboard_hours_without_payroll(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
    calendar_2026, db_session: Session,
):
    """Дашборд: часы и статусы периодов — да, ФОТ — нет."""
    db_session.add(TimesheetEntry(
        employee_id=workers["own"].id, work_date=WORKDAY,
        company_id=company.id, hours=8,
    ))
    db_session.commit()

    resp = client.get("/api/dashboard/2026/5", headers=_auth(client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["hours"]["total_hours"] == "8"
    assert data["periods"] is not None
    assert data["payroll"] is None
    assert data["payroll_by_department"] == []
    assert data["payroll_by_company"] == []
    assert all(point["payroll_total"] is None for point in data["trend"])


def test_dashboard_shows_only_own_departments(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
    calendar_2026, db_session: Session,
):
    db_session.add_all([
        TimesheetEntry(employee_id=workers["own"].id, work_date=WORKDAY,
                       company_id=company.id, hours=8),
        TimesheetEntry(employee_id=workers["other"].id, work_date=WORKDAY,
                       company_id=company.id, hours=7),
    ])
    db_session.commit()

    data = client.get("/api/dashboard/2026/5", headers=_auth(client)).json()
    assert data["hours"]["total_hours"] == "8"  # чужие 7 ч не попали
    assert {r["department_name"] for r in data["periods"]["rows"]} == {"ИТО"}


# ── Чего не может: отправить период на проверку ────────────────────────────────

def test_cannot_submit_period(
    client: TestClient, timekeeper: Employee, workers: dict, own_dept: Department,
    db_session: Session,
):
    """Заполнение — его, workflow-переход — руководителя."""
    month = client.get("/api/timesheet/2026/5", headers=_auth(client)).json()
    period = next(p for p in month["periods"] if p["department_id"] == own_dept.id)
    assert period["can_submit"] is False  # кнопки в UI тоже не будет

    resp = client.post(f"/api/timesheet/periods/{period['id']}/submit", headers=_auth(client))
    assert resp.status_code == 403
    assert month["periods"][0]["status"] == "draft"


def test_cannot_close_or_reopen_period(
    client: TestClient, timekeeper: Employee, workers: dict, own_dept: Department,
    db_session: Session,
):
    month = client.get("/api/timesheet/2026/5", headers=_auth(client)).json()
    period = next(p for p in month["periods"] if p["department_id"] == own_dept.id)
    assert period["can_close"] is False
    assert period["can_reopen"] is False
    assert client.post(
        f"/api/timesheet/periods/{period['id']}/close", headers=_auth(client)
    ).status_code == 403

    # Переоткрытие проверяется на закрытом периоде: в draft любая роль получила бы
    # 422 по статусу, и 403 по роли остался бы непроверенным.
    row = db_session.get(TimesheetPeriod, period["id"])
    row.status = "closed"
    db_session.commit()
    assert client.post(
        f"/api/timesheet/periods/{period['id']}/reopen",
        json={"reason": "нужно"}, headers=_auth(client),
    ).status_code == 403
    db_session.expire_all()
    assert db_session.get(TimesheetPeriod, period["id"]).status == "closed"


def test_tasks_inbox_forbidden(client: TestClient, timekeeper: Employee):
    assert client.get("/api/timesheet/tasks", headers=_auth(client)).status_code == 403


# ── Чего не может: управлять сотрудниками и оргструктурой ──────────────────────

def test_cannot_manage_employees_and_org(
    client: TestClient, timekeeper: Employee, workers: dict, own_dept: Department,
):
    headers = _auth(client)
    assert client.post(
        "/api/employees", json={"full_name": "Кто-то"}, headers=headers
    ).status_code == 403
    assert client.patch(
        f"/api/employees/{workers['own'].id}", json={"rate": "99999"}, headers=headers
    ).status_code == 403
    assert client.put(
        f"/api/departments/{own_dept.id}/managers",
        json={"employee_ids": []}, headers=headers,
    ).status_code == 403
    assert client.get("/api/org/tree", headers=headers).status_code == 403


# ── Изоляция чужого отдела ─────────────────────────────────────────────────────

def test_other_department_is_invisible(
    client: TestClient, timekeeper: Employee, workers: dict, other_dept: Department,
):
    headers = _auth(client)
    # В своём списке отделов чужого нет — из него строится селектор
    depts = client.get("/api/departments", headers=headers)
    assert [d["code"] for d in depts.json()] == ["ITO"]

    # Явный запрос чужого отдела — 403, а не молча пустая выдача
    assert client.get(
        f"/api/timesheet/2026/5?department_id={other_dept.id}", headers=headers
    ).status_code == 403
    assert client.get(
        f"/api/employees/{workers['other'].id}", headers=headers
    ).status_code == 404


def test_cannot_write_hours_to_other_department(
    client: TestClient, timekeeper: Employee, workers: dict, company: Company,
    db_session: Session,
):
    resp = client.put(
        "/api/timesheet/cell",
        json={
            "employee_id": workers["other"].id,
            "work_date": WORKDAY.isoformat(),
            "company_id": company.id,
            "hours": 8,
        },
        headers=_auth(client),
    )
    assert resp.status_code == 403
    assert db_session.query(TimesheetEntry).count() == 0


def test_cannot_set_absence_in_other_department(
    client: TestClient, timekeeper: Employee, workers: dict,
):
    resp = client.put(
        "/api/timesheet/absence",
        json={
            "employee_id": workers["other"].id,
            "work_date": WORKDAY.isoformat(),
            "kind": "sick",
        },
        headers=_auth(client),
    )
    assert resp.status_code == 403


def test_timekeeper_without_departments_sees_nothing(
    client: TestClient, db_session: Session, workers: dict,
):
    """Роль без привязки к отделам не даёт доступа ни к кому."""
    emp = Employee(
        full_name="Ничей Табельщик",
        email="lonetk@example.com",
        hashed_password=hash_password("tk123456"),
        role="timekeeper",
        is_active=True,
        must_change_password=False,
    )
    db_session.add(emp)
    db_session.commit()
    headers = {"Authorization": f"Bearer {get_token(client, 'lonetk@example.com', 'tk123456')}"}
    assert client.get("/api/timesheet/2026/5", headers=headers).json()["employees"] == []
    assert client.get("/api/employees", headers=headers).json() == []


# ── Другие роли не задеты ──────────────────────────────────────────────────────

def test_manager_still_sees_money(
    client: TestClient, db_session: Session, own_dept: Department, workers: dict,
    calendar_2026,
):
    """Появление табельщика не должно урезать менеджера того же отдела —
    табельщик и руководитель могут быть разными людьми на одном отделе."""
    mgr = Employee(
        full_name="Тест Руководитель",
        email="tkmgr@example.com",
        hashed_password=hash_password("mgr12345"),
        role="manager",
        is_active=True,
        must_change_password=False,
        managed_departments=[own_dept],
    )
    db_session.add(mgr)
    db_session.commit()
    headers = {"Authorization": f"Bearer {get_token(client, 'tkmgr@example.com', 'mgr12345')}"}

    month = client.get("/api/timesheet/2026/5?include_payroll=true", headers=headers).json()
    assert month["payroll"] is not None
    assert month["employees"][0]["rate"] == "50000.00"
    assert client.get("/api/timesheet/2026/5/payroll", headers=headers).status_code == 200

    period = next(p for p in month["periods"] if p["department_id"] == own_dept.id)
    assert period["can_submit"] is True
