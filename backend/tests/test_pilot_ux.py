"""
Доработки по итогам пилота (task_pilot_ux).

Здесь проверяется только то, что живёт на бэке: ЛИЧНАЯ отметка «строку
проверил» — её личность (чужие не видят и не снимают), привязка к месяцу
(в новом месяце пусто), доставка одним запросом вместе с табелем и права.

Счётчик сотрудников и фильтры колонок — чисто клиентские (считаются поверх
уже загруженных данных), поэтому здесь проверяется их ОСНОВАНИЕ: что бэк
отдаёт данные, из которых счётчик считает людей, а не строки, и что
принадлежность рабочего места юрлицу берётся из карточки (отдела позиции),
а не из проставленных часов.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.row_checks import RowCheck
from app.models.schedules import Schedule
from app.services.row_checks import checked_position_ids, set_row_check
from tests.conftest import get_token

YEAR, MONTH = 2026, 7


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(name="ООО «Земля МО»", code="ZMO", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def company2(db_session: Session) -> Company:
    c = Company(name="ООО «Комфорт»", code="KFT", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(
        name="5/2", schedule_type="weekday", hours_per_shift=8, is_active=True
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def dept(db_session: Session, company: Company) -> Department:
    d = Department(name="ИТО", code="ITO", head_company_id=company.id, is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def dept2(db_session: Session, company2: Company) -> Department:
    d = Department(
        name="Бухгалтерия", code="ACC", head_company_id=company2.id, is_active=True
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _worker(db_session, name, tab, dept, company, schedule) -> Employee:
    emp = Employee(
        full_name=name,
        tab_number=tab,
        position="Инженер",
        department_id=dept.id,
        default_company_id=company.id,
        schedule_id=schedule.id,
        rate=Decimal("50000"),
        is_active=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session, dept, company, schedule) -> Employee:
    return _worker(db_session, "Иванов Иван", "T-001", dept, company, schedule)


@pytest.fixture
def worker2(db_session, dept, company, schedule) -> Employee:
    return _worker(db_session, "Петров Пётр", "T-002", dept, company, schedule)


@pytest.fixture
def combiner(db_session, dept, dept2, company, company2, schedule) -> Employee:
    """Совместитель: два рабочих места в разных отделах и юрлицах."""
    emp = _worker(db_session, "Сидоров Совместитель", "T-003", dept, company, schedule)
    extra = EmployeePosition(
        employee_id=emp.id,
        title="Электрик",
        is_primary=False,
        is_active=True,
        department_id=dept2.id,
        company_id=company2.id,
        schedule_id=schedule.id,
        rate=Decimal("30000"),
    )
    db_session.add(extra)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def _user(db_session, name, email, role, depts=()) -> Employee:
    emp = Employee(
        full_name=name,
        email=email,
        hashed_password=hash_password("pass1234"),
        role=role,
        is_active=True,
        must_change_password=False,
        is_system_admin=(role == "admin"),
        managed_departments=list(depts),
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def admin(db_session) -> Employee:
    return _user(db_session, "PX Admin", "pxadmin@example.com", "admin")


@pytest.fixture
def timekeeper(db_session, dept) -> Employee:
    return _user(db_session, "PX Табельщик", "pxtk@example.com", "timekeeper", [dept])


@pytest.fixture
def timekeeper2(db_session, dept) -> Employee:
    """Второй табельщик ТОГО ЖЕ отдела — на нём видно, что отметка личная."""
    return _user(db_session, "PX Табельщик 2", "pxtk2@example.com", "timekeeper", [dept])


@pytest.fixture
def foreign_manager(db_session, dept2) -> Employee:
    return _user(db_session, "PX Чужой", "pxmgr@example.com", "manager", [dept2])


def _auth(client: TestClient, email: str) -> dict:
    return {"Authorization": f"Bearer {get_token(client, email, 'pass1234')}"}


def _check(client, headers, position_id, value=True, year=YEAR, month=MONTH):
    return client.put(
        "/api/timesheet/row-check",
        json={
            "position_id": position_id,
            "year": year,
            "month": month,
            "value": value,
        },
        headers=headers,
    )


def _month(client, headers, year=YEAR, month=MONTH):
    return client.get(f"/api/timesheet/{year}/{month}", headers=headers)


# ── Отметка: постановка и снятие ──────────────────────────────────────────────

class TestRowCheckBasics:
    def test_mark_and_unmark(self, client, admin, worker, db_session):
        h = _auth(client, "pxadmin@example.com")
        pid = worker.primary_position.id

        resp = _check(client, h, pid, True)
        assert resp.status_code == 200
        assert resp.json() == {
            "position_id": pid, "year": YEAR, "month": MONTH, "checked": True
        }
        assert db_session.query(RowCheck).count() == 1

        resp = _check(client, h, pid, False)
        assert resp.status_code == 200
        assert resp.json()["checked"] is False
        assert db_session.query(RowCheck).count() == 0

    def test_mark_is_idempotent(self, client, admin, worker, db_session):
        """Двойной клик (или две вкладки) не должен ронять unique-констрейнт."""
        h = _auth(client, "pxadmin@example.com")
        pid = worker.primary_position.id
        assert _check(client, h, pid, True).status_code == 200
        assert _check(client, h, pid, True).status_code == 200
        assert db_session.query(RowCheck).count() == 1

    def test_unmark_missing_is_noop(self, client, admin, worker, db_session):
        h = _auth(client, "pxadmin@example.com")
        resp = _check(client, h, worker.primary_position.id, False)
        assert resp.status_code == 200
        assert db_session.query(RowCheck).count() == 0

    def test_unknown_position_404(self, client, admin):
        h = _auth(client, "pxadmin@example.com")
        assert _check(client, h, 999999, True).status_code == 404

    def test_mark_does_not_touch_hours_or_periods(
        self, client, admin, worker, db_session
    ):
        """Отметка — закладка, а не данные табеля: ни ячеек, ни периодов."""
        from app.models.timesheet_entries import TimesheetEntry
        from app.models.timesheet_periods import TimesheetPeriod

        h = _auth(client, "pxadmin@example.com")
        _check(client, h, worker.primary_position.id, True)
        assert db_session.query(TimesheetEntry).count() == 0
        assert db_session.query(TimesheetPeriod).count() == 0


# ── Личность отметки ──────────────────────────────────────────────────────────

class TestRowCheckIsPersonal:
    def test_other_user_does_not_see_the_mark(
        self, client, timekeeper, timekeeper2, worker
    ):
        """AC9: отметка личная — второй табельщик ТОГО ЖЕ отдела её не видит."""
        pid = worker.primary_position.id
        _check(client, _auth(client, "pxtk@example.com"), pid, True)

        mine = _month(client, _auth(client, "pxtk@example.com")).json()
        assert mine["checked_positions"] == [pid]

        theirs = _month(client, _auth(client, "pxtk2@example.com")).json()
        assert theirs["checked_positions"] == []

    def test_other_user_cannot_unmark(
        self, client, timekeeper, timekeeper2, worker, db_session
    ):
        """Снятие чужим пользователем не трогает чужую отметку, а заводит свою."""
        pid = worker.primary_position.id
        _check(client, _auth(client, "pxtk@example.com"), pid, True)
        _check(client, _auth(client, "pxtk2@example.com"), pid, False)

        rows = db_session.query(RowCheck).all()
        assert [r.user_id for r in rows] == [timekeeper.id]

    def test_two_users_mark_the_same_row_independently(
        self, client, timekeeper, timekeeper2, worker, db_session
    ):
        pid = worker.primary_position.id
        _check(client, _auth(client, "pxtk@example.com"), pid, True)
        _check(client, _auth(client, "pxtk2@example.com"), pid, True)
        assert db_session.query(RowCheck).count() == 2

        for email in ("pxtk@example.com", "pxtk2@example.com"):
            assert _month(client, _auth(client, email)).json()["checked_positions"] == [pid]

    def test_service_filters_by_actor(self, db_session, timekeeper, timekeeper2, worker):
        """Сервис — единственное место чтения: сужение по актору обязано быть там."""
        pid = worker.primary_position.id
        set_row_check(db_session, timekeeper, pid, YEAR, MONTH, True)
        assert checked_position_ids(db_session, timekeeper, YEAR, MONTH) == [pid]
        assert checked_position_ids(db_session, timekeeper2, YEAR, MONTH) == []


# ── Привязка к месяцу ─────────────────────────────────────────────────────────

class TestRowCheckIsMonthly:
    def test_next_month_is_empty(self, client, timekeeper, worker):
        """AC9: в новом месяце все строки не отмечены — ничего не переносится."""
        pid = worker.primary_position.id
        h = _auth(client, "pxtk@example.com")
        _check(client, h, pid, True)

        assert _month(client, h).json()["checked_positions"] == [pid]
        assert _month(client, h, YEAR, MONTH + 1).json()["checked_positions"] == []

    def test_next_year_is_empty(self, client, timekeeper, worker):
        pid = worker.primary_position.id
        h = _auth(client, "pxtk@example.com")
        _check(client, h, pid, True)
        assert _month(client, h, YEAR + 1, MONTH).json()["checked_positions"] == []

    def test_months_are_independent(self, client, timekeeper, worker, db_session):
        pid = worker.primary_position.id
        h = _auth(client, "pxtk@example.com")
        _check(client, h, pid, True, month=MONTH)
        _check(client, h, pid, True, month=MONTH + 1)
        assert db_session.query(RowCheck).count() == 2

        _check(client, h, pid, False, month=MONTH)
        assert _month(client, h, YEAR, MONTH).json()["checked_positions"] == []
        assert _month(client, h, YEAR, MONTH + 1).json()["checked_positions"] == [pid]


# ── Отметка адресована СТРОКЕ (рабочему месту), а не человеку ─────────────────

class TestRowCheckIsPerPosition:
    def test_combiner_positions_are_marked_separately(
        self, client, admin, combiner, db_session
    ):
        """У совместителя две строки — они проверяются порознь."""
        h = _auth(client, "pxadmin@example.com")
        positions = sorted(p.id for p in combiner.active_positions)
        assert len(positions) == 2

        _check(client, h, positions[0], True)
        assert _month(client, h).json()["checked_positions"] == [positions[0]]

        _check(client, h, positions[1], True)
        assert _month(client, h).json()["checked_positions"] == positions


# ── Доставка одним запросом вместе с табелем ──────────────────────────────────

class TestRowCheckDelivery:
    def test_month_response_carries_marks(self, client, admin, worker, worker2):
        h = _auth(client, "pxadmin@example.com")
        _check(client, h, worker.primary_position.id, True)
        body = _month(client, h).json()
        assert body["checked_positions"] == [worker.primary_position.id]

    def test_empty_by_default(self, client, admin, worker):
        body = _month(client, _auth(client, "pxadmin@example.com")).json()
        assert body["checked_positions"] == []

    def test_only_visible_positions_are_listed(
        self, client, admin, timekeeper, combiner, dept
    ):
        """В табеле отдела видна только позиция ЭТОГО отдела — и отметка тоже."""
        h_admin = _auth(client, "pxadmin@example.com")
        for p in combiner.active_positions:
            _check(client, h_admin, p.id, True)

        # Табельщик ведёт только dept: вторая позиция (dept2) ему не видна.
        # Отметки чужого пользователя он и так не видит, поэтому ставит свои.
        h_tk = _auth(client, "pxtk@example.com")
        primary = combiner.primary_position.id
        _check(client, h_tk, primary, True)
        body = _month(client, h_tk).json()
        assert body["checked_positions"] == [primary]


# ── Права ─────────────────────────────────────────────────────────────────────

class TestRowCheckAccess:
    def test_timekeeper_can_mark_own_department(self, client, timekeeper, worker):
        h = _auth(client, "pxtk@example.com")
        assert _check(client, h, worker.primary_position.id, True).status_code == 200

    def test_foreign_manager_denied(self, client, foreign_manager, worker):
        """Чужой отдел — 403, а не молча принятая отметка."""
        h = _auth(client, "pxmgr@example.com")
        assert _check(client, h, worker.primary_position.id, True).status_code == 403

    def test_anonymous_denied(self, client, worker):
        resp = client.put(
            "/api/timesheet/row-check",
            json={
                "position_id": worker.primary_position.id,
                "year": YEAR, "month": MONTH, "value": True,
            },
        )
        assert resp.status_code in (401, 403)

    def test_closed_period_does_not_block_marking(
        self, client, admin, timekeeper, worker, db_session
    ):
        """Отметка — не данные табеля: закрытый месяц сверяют так же."""
        from app.models.timesheet_periods import TimesheetPeriod

        db_session.add(
            TimesheetPeriod(
                department_id=worker.primary_position.department_id,
                year=YEAR, month=MONTH, status="closed",
            )
        )
        db_session.commit()
        h = _auth(client, "pxtk@example.com")
        assert _check(client, h, worker.primary_position.id, True).status_code == 200


# ── Основание клиентских частей: люди vs строки, компания из карточки ─────────

class TestCounterAndCompanyFilterData:
    def test_combiner_is_one_employee_with_two_rows(self, client, admin, combiner):
        """AC2: счётчик считает людей — сотрудник один, строк (позиций) две."""
        body = _month(client, _auth(client, "pxadmin@example.com")).json()
        people = [e for e in body["employees"] if not e.get("is_system_admin")]
        assert len(people) == 1
        rows = sum(len(v) for v in body["positions_by_employee"].values())
        assert rows == 2

    def test_position_company_comes_from_card_not_hours(
        self, client, admin, worker, company, company2, dept, db_session
    ):
        """AC6: юрлицо строки — из карточки (отдел позиции), а не из часов.

        Ставим сотруднику отдела «ИТО» (головная компания ЗМО) час на
        «Комфорт» — принадлежность строки от этого не меняется.
        """
        h = _auth(client, "pxadmin@example.com")
        resp = client.put(
            "/api/timesheet/cell",
            json={
                "employee_id": worker.id,
                "position_id": worker.primary_position.id,
                "work_date": f"{YEAR}-{MONTH:02d}-06",
                "company_id": company2.id,
                "hours": 8,
            },
            headers=h,
        )
        assert resp.status_code == 200

        body = _month(client, h).json()
        position = body["positions_by_employee"][str(worker.id)][0]
        # Отдел позиции — ИТО, его головная компания — ЗМО: по ней и отбирает
        # фильтр компании во фронте (positionInCompany).
        assert position["department"]["head_company_id"] == company.id
        assert any(
            e["company_id"] == company2.id for e in body["entries"]
        ), "час на другом юрлице проставлен и принадлежность строки не изменил"
