"""Отдел без сотрудников не участвует в workflow периодов + сводка «Всего отделов».

Баг: дашборд рисовал «Черновик · просрочен» у отделов, где никого нет — в том
числе у псевдо-отдела «Без отдела». Причина: список периодов строился из таблиц
`departments` / `timesheet_periods`, а есть ли в отделе сотрудники, никто не
спрашивал (список просрочки не спрашивал даже про «Без отдела»). Закрывать в
таком отделе нечего, это шум.

Правило: отдел участвует в workflow месяца, только если в этом месяце в нём есть
сотрудники — те же, что видит табель (`visible_employees_for_actor`): несистемные,
активные или уволенные не раньше начала месяца, с АКТИВНЫМ рабочим местом в отделе.
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
from app.models.timesheet_periods import TimesheetPeriod
from tests.conftest import get_token

MAY_BASIC = {
    "year": 2026,
    "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}],
}


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_BASIC, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="EC1", name="Empty Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule8(db_session: Session) -> Schedule:
    s = Schedule(name="5/2-empty", hours_per_shift=8, schedule_type="weekday",
                 is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def staffed_dept(db_session: Session) -> Department:
    d = Department(name="A Staffed", code="STF", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def empty_dept(db_session: Session) -> Department:
    """Отдел без единого сотрудника — «Менеджмент производства» из бага."""
    d = Department(name="B Empty", code="EMP", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def sysadmin(db_session: Session) -> Employee:
    """Системный админ: сотрудником отдела не считается (скрыт из табеля)."""
    emp = Employee(
        full_name="Empty Admin", email="emptyadmin@example.com",
        hashed_password=hash_password("admin123"), role="admin",
        is_active=True, must_change_password=False, is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session: Session, staffed_dept: Department, company: Company,
           schedule8: Schedule) -> Employee:
    emp = Employee(
        full_name="Staffed Worker", is_active=True,
        department_id=staffed_dept.id, default_company_id=company.id,
        schedule_id=schedule8.id, rate=Decimal("50000"),
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def _token(client: TestClient) -> str:
    return get_token(client, "emptyadmin@example.com", "admin123")


def _get(client: TestClient, token: str, url: str = "/api/dashboard/2026/5"):
    return client.get(url, headers={"Authorization": f"Bearer {token}"})


def _dept_names(rows) -> set[str]:
    return {r["department_name"] for r in rows}


# ── Баг 1: пустой отдел не участвует в workflow ───────────────────────────────

class TestEmptyDepartmentExcluded:
    def test_empty_department_not_in_rows(self, client, sysadmin, worker,
                                          empty_dept, calendar_2026):
        data = _get(client, _token(client)).json()
        names = _dept_names(data["periods"]["rows"])
        assert "A Staffed" in names          # отдел с сотрудником остался
        assert "B Empty" not in names        # пустой ушёл

    def test_empty_department_not_overdue(self, client, db_session, sysadmin, worker,
                                          empty_dept, calendar_2026):
        """Незакрытый период прошлого месяца у пустого отдела — не просрочка."""
        db_session.add(TimesheetPeriod(department_id=empty_dept.id, year=2026,
                                       month=4, status="draft"))
        db_session.commit()
        data = _get(client, _token(client)).json()
        assert data["periods"]["overdue_rows"] == []
        assert data["periods"]["counts"]["overdue"] == 0

    def test_staffed_department_still_overdue(self, client, db_session, sysadmin,
                                              worker, staffed_dept, calendar_2026):
        """Регрессия: у отдела с сотрудниками просрочка работает как раньше."""
        db_session.add(TimesheetPeriod(department_id=staffed_dept.id, year=2026,
                                       month=4, status="draft"))
        db_session.commit()
        data = _get(client, _token(client)).json()
        assert data["periods"]["counts"]["overdue"] == 1
        assert data["periods"]["overdue_rows"][0]["department_id"] == staffed_dept.id

    def test_empty_department_not_in_counts(self, client, db_session, sysadmin,
                                            worker, empty_dept, calendar_2026):
        """Счётчики считают только отделы с сотрудниками."""
        db_session.add(TimesheetPeriod(department_id=empty_dept.id, year=2026,
                                       month=5, status="pending_review"))
        db_session.commit()
        counts = _get(client, _token(client)).json()["periods"]["counts"]
        assert counts["pending_review"] == 0
        assert counts["draft"] == 1  # только staffed_dept

    def test_department_emptied_by_dismissal(self, client, db_session, sysadmin,
                                             worker, staffed_dept, calendar_2026):
        """Уволен до начала месяца → в этом месяце отдела в workflow нет,
        а в месяце увольнения он ещё есть."""
        worker.is_active = False
        worker.dismissal_date = date(2026, 4, 20)
        db_session.commit()
        token = _token(client)
        may = _get(client, token, "/api/dashboard/2026/5").json()
        apr = _get(client, token, "/api/dashboard/2026/4").json()
        assert "A Staffed" not in _dept_names(may["periods"]["rows"])
        assert "A Staffed" in _dept_names(apr["periods"]["rows"])

    def test_deactivated_position_empties_department(self, client, db_session, sysadmin,
                                                     worker, calendar_2026):
        """Рабочее место деактивировано → строк в табеле отдела нет,
        значит и закрывать нечего."""
        worker.primary_position.is_active = False
        db_session.commit()
        data = _get(client, _token(client)).json()
        assert "A Staffed" not in _dept_names(data["periods"]["rows"])


# ── Баг 1б: псевдо-отдел «Без отдела» ─────────────────────────────────────────

class TestNullDepartmentGroup:
    def test_hidden_without_employees(self, client, db_session, sysadmin, worker,
                                      calendar_2026):
        """Никого без отдела нет → строки «Без отдела» нет ни в списке,
        ни в просрочке (даже когда период в базе остался)."""
        db_session.add(TimesheetPeriod(department_id=None, year=2026, month=4,
                                       status="draft"))
        db_session.add(TimesheetPeriod(department_id=None, year=2026, month=5,
                                       status="draft"))
        db_session.commit()
        data = _get(client, _token(client)).json()
        assert "Без отдела" not in _dept_names(data["periods"]["rows"])
        assert "Без отдела" not in _dept_names(data["periods"]["overdue_rows"])
        assert data["periods"]["counts"]["overdue"] == 0

    def test_shown_when_employee_has_no_department(self, client, db_session, sysadmin,
                                                   worker, company, calendar_2026):
        emp = Employee(full_name="Ничей", is_active=True,
                       default_company_id=company.id)
        db_session.add(emp)
        db_session.commit()
        data = _get(client, _token(client)).json()
        assert "Без отдела" in _dept_names(data["periods"]["rows"])


# ── Периоды пустым отделам не создаются ───────────────────────────────────────

class TestNoPeriodCreatedForEmptyDepartment:
    def test_month_request_creates_periods_only_for_staffed(
        self, client, db_session, sysadmin, worker, empty_dept, calendar_2026
    ):
        resp = client.get("/api/timesheet/2026/5",
                          headers={"Authorization": f"Bearer {_token(client)}"})
        assert resp.status_code == 200
        created = {
            p.department_id
            for p in db_session.query(TimesheetPeriod).filter_by(year=2026, month=5).all()
        }
        assert empty_dept.id not in created
        assert None not in created


# ── Сводка: «Всего отделов» ───────────────────────────────────────────────────

class TestDepartmentsCounter:
    def test_counts_only_staffed_departments(self, client, sysadmin, worker,
                                             empty_dept, calendar_2026):
        counts = _get(client, _token(client)).json()["periods"]["counts"]
        assert counts["departments"] == 1

    def test_parts_sum_to_total_for_single_month(self, client, db_session, sysadmin,
                                                 worker, staffed_dept, company,
                                                 schedule8, calendar_2026):
        other = Department(name="C Other", code="OTH", is_active=True)
        db_session.add(other)
        db_session.commit()
        db_session.add(Employee(full_name="Второй", is_active=True,
                                department_id=other.id, default_company_id=company.id,
                                schedule_id=schedule8.id, rate=Decimal("40000")))
        db_session.add(TimesheetPeriod(department_id=staffed_dept.id, year=2026,
                                       month=5, status="closed"))
        db_session.commit()
        counts = _get(client, _token(client)).json()["periods"]["counts"]
        assert counts["departments"] == 2
        assert counts["closed"] == 1
        assert counts["draft"] == 1
        assert (counts["closed"] + counts["pending_review"] + counts["draft"]
                == counts["departments"])

    def test_manager_counts_only_own_departments(self, client, db_session, worker,
                                                 staffed_dept, empty_dept, company,
                                                 schedule8, calendar_2026):
        """Manager/timekeeper — только свои отделы."""
        other = Department(name="C Other", code="OTH", is_active=True)
        db_session.add(other)
        db_session.commit()
        db_session.add(Employee(full_name="Чужой", is_active=True,
                                department_id=other.id, default_company_id=company.id,
                                schedule_id=schedule8.id, rate=Decimal("40000")))
        mgr = Employee(
            full_name="Мгр", email="emptymgr@example.com",
            hashed_password=hash_password("mgr123"), role="manager",
            is_active=True, must_change_password=False,
            department_id=staffed_dept.id,
            managed_departments=[staffed_dept, empty_dept],
        )
        db_session.add(mgr)
        db_session.commit()
        token = get_token(client, "emptymgr@example.com", "mgr123")
        counts = _get(client, token).json()["periods"]["counts"]
        # своих отделов два, но empty_dept пуст → в сводке только один
        assert counts["departments"] == 1

    def test_range_counts_distinct_departments(self, client, db_session, sysadmin,
                                               worker, calendar_2026):
        """Диапазон: строк на отдел несколько (по месяцам), отдел один."""
        data = _get(client, _token(client),
                    "/api/dashboard/2026/4?to_year=2026&to_month=5").json()
        assert len(data["periods"]["rows"]) == 2
        assert data["periods"]["counts"]["departments"] == 1
