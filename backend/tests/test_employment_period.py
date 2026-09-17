"""
Период работы рабочего места — границы заполнения табеля (task_employment_period).

Проверка построена по ТРЕБОВАНИЮ, а не по диффу: сначала выписаны места, которых
требование касается (автозаполнение, ручной ввод часов, отсутствия, ночные
смены, прямой запрос к API, совместитель, очистка при смене дат, закрытый
период, сотрудники без дат, норма), и тесты идут по этому списку.

Ключевое, что здесь держится:

- границы ВКЛЮЧИТЕЛЬНЫЕ — уволен пятнадцатого, пятнадцатое ещё рабочий день;
- даты берутся с ПОЗИЦИИ и пересекаются с датами человека;
- пустая дата не ограничивает ничего (таких сотрудников большинство);
- запрет живёт на БЭКЕ: прямой запрос отклоняется, а не прячется в интерфейсе;
- снятие отметки не блокируется никогда — иначе часы за границей нечем убрать;
- норма часов и дней остаётся МЕСЯЧНОЙ и от дат не зависит.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.employment_period import (
    OutsideEmploymentPeriod,
    employment_bounds,
    is_within_employment,
)
from tests.conftest import get_token

YEAR, MONTH = 2026, 6

# Июнь 2026 по xmlcalendar: 6,7 — выходные; 11 — сокращённый; 12 — День России;
# 13,14,20,21,27,28 — выходные.
CAL_JUNE = {"year": 2026, "months": [{"month": 6, "days": "6,7,11*,12,13,14,20,21,27,28"}]}

D = datetime.date


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
def calendar(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=YEAR, data=CAL_JUNE, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8, is_active=True)
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
    d = Department(name="Охрана", code="SEC", head_company_id=company2.id, is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def make_employee(
    db: Session,
    company: Company,
    dept: Department,
    schedule: Schedule,
    *,
    name="Иванов Иван",
    tab="T-001",
    hire_date=None,
    dismissal_date=None,
    pos_hire=None,
    pos_dismissal=None,
) -> Employee:
    emp = Employee(
        full_name=name,
        tab_number=tab,
        is_active=True,
        hire_date=hire_date,
        dismissal_date=dismissal_date,
    )
    db.add(emp)
    db.flush()
    # `Employee.__init__` уже завёл ОСНОВНУЮ позицию — настраиваем её, а не
    # добавляем вторую (иначе у сотрудника окажется два рабочих места).
    pos = emp.primary_position
    pos.title = "Инженер"
    pos.is_active = True
    pos.department_id = dept.id
    pos.company_id = company.id
    pos.schedule_id = schedule.id
    pos.pay_type = "salary"
    pos.rate = Decimal("50000")
    pos.hire_date = pos_hire
    pos.dismissal_date = pos_dismissal
    db.commit()
    db.refresh(emp)
    return emp


@pytest.fixture
def employee(db_session, company, dept, schedule) -> Employee:
    return make_employee(db_session, company, dept, schedule)


@pytest.fixture
def admin_token(client: TestClient, admin_user) -> str:
    return get_token(client, "admin@example.com", "admin123")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Само правило: границы и пересечение ───────────────────────────────────────

class TestBounds:
    """Чистая функция границ — пересечение дат человека и позиции."""

    def test_no_dates_means_no_bounds(self, db_session, company, dept, schedule):
        emp = make_employee(db_session, company, dept, schedule)
        assert employment_bounds(emp, emp.primary_position) == (None, None)
        # Сотрудников без дат большинство — им не должно ограничиваться ничего.
        for day in (D(2020, 1, 1), D(YEAR, MONTH, 15), D(2099, 12, 31)):
            assert is_within_employment(emp, emp.primary_position, day)

    def test_dismissal_day_itself_is_a_working_day(self, db_session, company, dept, schedule):
        """Границы ВКЛЮЧИТЕЛЬНЫЕ: уволен пятнадцатого — пятнадцатое рабочее."""
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        pos = emp.primary_position
        assert is_within_employment(emp, pos, D(YEAR, MONTH, 15)) is True
        assert is_within_employment(emp, pos, D(YEAR, MONTH, 16)) is False

    def test_hire_day_itself_is_a_working_day(self, db_session, company, dept, schedule):
        emp = make_employee(
            db_session, company, dept, schedule, hire_date=D(YEAR, MONTH, 10)
        )
        pos = emp.primary_position
        assert is_within_employment(emp, pos, D(YEAR, MONTH, 10)) is True
        assert is_within_employment(emp, pos, D(YEAR, MONTH, 9)) is False

    def test_intersection_person_narrows_position(self, db_session, company, dept, schedule):
        """Даты человека — ВНЕШНЯЯ граница: увольнение кадровиком закрывает
        рабочее место, даже если у позиции своя дата стоит позже."""
        emp = make_employee(
            db_session, company, dept, schedule,
            dismissal_date=D(YEAR, MONTH, 10),
            pos_dismissal=D(YEAR, MONTH, 25),
        )
        start, end = employment_bounds(emp, emp.primary_position)
        assert end == D(YEAR, MONTH, 10)
        assert is_within_employment(emp, emp.primary_position, D(YEAR, MONTH, 20)) is False

    def test_intersection_position_narrows_person(self, db_session, company, dept, schedule):
        """И наоборот: позиция может закрыться раньше, чем человек уволится."""
        emp = make_employee(
            db_session, company, dept, schedule,
            hire_date=D(YEAR, 1, 1),
            pos_dismissal=D(YEAR, MONTH, 10),
        )
        start, end = employment_bounds(emp, emp.primary_position)
        assert (start, end) == (D(YEAR, 1, 1), D(YEAR, MONTH, 10))

    def test_latest_hire_date_wins(self, db_session, company, dept, schedule):
        emp = make_employee(
            db_session, company, dept, schedule,
            hire_date=D(YEAR, 1, 1), pos_hire=D(YEAR, MONTH, 5),
        )
        start, _ = employment_bounds(emp, emp.primary_position)
        assert start == D(YEAR, MONTH, 5)


# ── Автозаполнение ────────────────────────────────────────────────────────────

class TestAutofill:
    """Главный симптом задачи: уволенному посреди месяца автозаполнение
    проставляло смены до конца месяца."""

    def _preview(self, db, actor):
        from app.services.timesheet import build_autofill_preview
        return build_autofill_preview(db, actor, YEAR, MONTH)

    def test_no_dates_fills_whole_month(
        self, db_session, admin_user, company, dept, schedule, calendar
    ):
        """Опорная точка: без дат заполняется весь месяц, как раньше."""
        make_employee(db_session, company, dept, schedule)
        preview = self._preview(db_session, admin_user)
        days = sorted(e.work_date.day for e in preview.entries_to_create)
        assert days[0] == 1 and days[-1] == 30
        assert len(days) == 21  # рабочих дней июня 2026 по календарю

    def test_dismissed_mid_month_is_not_filled_after(
        self, db_session, admin_user, company, dept, schedule, calendar
    ):
        make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        preview = self._preview(db_session, admin_user)
        days = sorted(e.work_date.day for e in preview.entries_to_create)
        assert days, "до увольнения дни заполняться обязаны"
        assert max(days) == 15, f"после увольнения дней быть не должно: {days}"
        # 15 июня 2026 — понедельник, рабочий: день увольнения ВКЛЮЧИТЕЛЬНО.
        assert 15 in days

    def test_hired_mid_month_is_not_filled_before(
        self, db_session, admin_user, company, dept, schedule, calendar
    ):
        make_employee(db_session, company, dept, schedule, hire_date=D(YEAR, MONTH, 10))
        preview = self._preview(db_session, admin_user)
        days = sorted(e.work_date.day for e in preview.entries_to_create)
        assert min(days) == 10
        assert max(days) == 30

    def test_position_dates_apply_independently(
        self, db_session, admin_user, company, dept, schedule, calendar
    ):
        """Дата стоит на ПОЗИЦИИ, человек не уволен — автозаполнение всё равно
        останавливается."""
        make_employee(
            db_session, company, dept, schedule, pos_dismissal=D(YEAR, MONTH, 15)
        )
        preview = self._preview(db_session, admin_user)
        assert max(e.work_date.day for e in preview.entries_to_create) == 15

    def test_month_entirely_outside_is_reported_not_silent(
        self, db_session, admin_user, company, dept, schedule, calendar
    ):
        """Закрытое на весь месяц место даёт ноль ячеек — но с причиной, а не
        молча: иначе это выглядит как сбой автозаполнения."""
        make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, 5, 20)
        )
        preview = self._preview(db_session, admin_user)
        assert preview.entries_to_create == []
        reasons = [s.reason for s in preview.employees_skipped]
        assert any("вне периода работы" in r for r in reasons), reasons


# ── Ручной ввод: проверка на БЭКЕ ─────────────────────────────────────────────

class TestBackendRejection:
    """Скрыть кнопку в интерфейсе недостаточно — прямой запрос тоже отклоняется."""

    def test_api_rejects_hours_after_dismissal(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id,
            "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-20",
            "company_id": company.id,
            "hours": 8,
        })
        assert r.status_code == 422, r.text
        assert "вне периода работы" in r.json()["detail"]

    def test_api_accepts_the_dismissal_day_itself(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id,
            "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-15",
            "company_id": company.id,
            "hours": 8,
        })
        assert r.status_code == 200, r.text

    def test_api_rejects_hours_before_hire(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        emp = make_employee(
            db_session, company, dept, schedule, hire_date=D(YEAR, MONTH, 10)
        )
        r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id,
            "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-05",
            "company_id": company.id,
            "hours": 8,
        })
        assert r.status_code == 422, r.text

    def test_api_rejects_absence_outside_period(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        """Ни часов, ни ОТСУТСТВИЙ."""
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        r = client.put("/api/timesheet/absence", headers=auth(admin_token), json={
            "employee_id": emp.id,
            "work_date": f"{YEAR}-06-20",
            "kind": "vacation",
        })
        assert r.status_code == 422, r.text
        assert "вне периода работы" in r.json()["detail"]

    def test_api_rejects_night_shift_outside_period(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        """Ни часов, ни отсутствий, ни НОЧНЫХ СМЕН."""
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        emp.primary_position.has_night_shifts = True
        db_session.commit()
        r = client.put("/api/timesheet/night-shift", headers=auth(admin_token), json={
            "employee_id": emp.id,
            "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-20",
            "value": True,
        })
        assert r.status_code == 422, r.text
        assert "вне периода работы" in r.json()["detail"]

    def test_batch_is_rejected_too(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        """Батч — отдельная точка входа, через неё запрет обходиться не должен."""
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        r = client.post("/api/timesheet/cells/batch", headers=auth(admin_token), json={
            "entries": [{
                "employee_id": emp.id,
                "position_id": emp.primary_position.id,
                "work_date": f"{YEAR}-06-20",
                "company_id": company.id,
                "hours": 8,
            }],
        })
        assert r.status_code == 422, r.text

    def test_employee_without_dates_is_not_restricted(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        """Пустые даты не ограничивают ничего — поведение как раньше."""
        emp = make_employee(db_session, company, dept, schedule)
        r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id,
            "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-30",
            "company_id": company.id,
            "hours": 8,
        })
        assert r.status_code == 200, r.text


class TestClearingIsAlwaysAllowed:
    """Снятие отметки не блокируется никогда — иначе часы, оставшиеся за новой
    границей, было бы нечем убрать ни руками, ни автоматической очисткой."""

    def test_zero_hours_deletes_entry_outside_period(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        emp = make_employee(db_session, company, dept, schedule)
        # Часы поставлены, ПОКА дат не было.
        client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id, "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-20", "company_id": company.id, "hours": 8,
        })
        emp.dismissal_date = D(YEAR, MONTH, 15)
        db_session.commit()

        r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id, "position_id": emp.primary_position.id,
            "work_date": f"{YEAR}-06-20", "company_id": company.id, "hours": 0,
        })
        assert r.status_code == 200, r.text
        left = db_session.query(TimesheetEntry).filter_by(employee_id=emp.id).count()
        assert left == 0


# ── Совместитель ──────────────────────────────────────────────────────────────

class TestMoonlighter:
    """Одна работа закрыта, вторая продолжается — ограничение своё у каждой."""

    @pytest.fixture
    def moonlighter(self, db_session, company, company2, dept, dept2, schedule):
        emp = make_employee(db_session, company, dept, schedule, name="Петров Пётр")
        extra = EmployeePosition(
            employee_id=emp.id,
            title="Электрик",
            is_primary=False,
            is_active=True,
            department_id=dept2.id,
            company_id=company2.id,
            schedule_id=schedule.id,
            pay_type="salary",
            rate=Decimal("30000"),
            dismissal_date=D(YEAR, MONTH, 15),  # подработка закрыта
        )
        db_session.add(extra)
        db_session.commit()
        db_session.refresh(emp)
        return emp

    def test_closed_position_blocked_open_one_allowed(
        self, client, moonlighter, admin_token, company, company2, calendar
    ):
        emp = moonlighter
        main = emp.primary_position
        extra = [p for p in emp.positions if not p.is_primary][0]

        blocked = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id, "position_id": extra.id,
            "work_date": f"{YEAR}-06-20", "company_id": company2.id, "hours": 8,
        })
        assert blocked.status_code == 422, blocked.text

        allowed = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": emp.id, "position_id": main.id,
            "work_date": f"{YEAR}-06-20", "company_id": company.id, "hours": 8,
        })
        assert allowed.status_code == 200, allowed.text

    def test_autofill_stops_only_the_closed_position(
        self, db_session, admin_user, moonlighter, calendar
    ):
        from app.services.timesheet import build_autofill_preview

        emp = moonlighter
        extra = [p for p in emp.positions if not p.is_primary][0]
        preview = build_autofill_preview(db_session, admin_user, YEAR, MONTH)

        main_days = [e.work_date.day for e in preview.entries_to_create
                     if e.position_id == emp.primary_position.id]
        extra_days = [e.work_date.day for e in preview.entries_to_create
                      if e.position_id == extra.id]
        assert max(main_days) == 30, "основная работа продолжается"
        assert max(extra_days) == 15, "подработка закрыта пятнадцатым"

    def test_absence_still_allowed_while_one_position_open(
        self, client, moonlighter, admin_token, calendar
    ):
        """Код отсутствия ставится на ЧЕЛОВЕКА, поэтому годится, пока открыто
        хотя бы одно рабочее место."""
        r = client.put("/api/timesheet/absence", headers=auth(admin_token), json={
            "employee_id": moonlighter.id,
            "work_date": f"{YEAR}-06-20",
            "kind": "vacation",
        })
        assert r.status_code == 200, r.text


# ── Очистка часов при смене дат ───────────────────────────────────────────────

class TestClearingOnDateChange:
    """Очистка необратима, поэтому только после подтверждения и с числами."""

    def _fill(self, client, token, emp, company, days):
        for day in days:
            r = client.put("/api/timesheet/cell", headers=auth(token), json={
                "employee_id": emp.id, "position_id": emp.primary_position.id,
                "work_date": f"{YEAR}-06-{day:02d}",
                "company_id": company.id, "hours": 8,
            })
            assert r.status_code == 200, r.text

    def test_dismiss_without_confirm_returns_numbers_and_changes_nothing(
        self, client, db_session, admin_token, employee, company, calendar
    ):
        self._fill(client, admin_token, employee, company, [16, 17, 18])

        r = client.post(
            f"/api/employees/{employee.id}/dismiss",
            headers=auth(admin_token),
            json={"dismissal_date": f"{YEAR}-06-15"},
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["days"] == 3
        assert Decimal(detail["hours"]) == Decimal("24")
        assert Decimal(detail["amount"]) > 0, "сумма обязана быть показана"

        # Ничего не сохранилось: ни дата, ни удаление часов.
        db_session.expire_all()
        emp = db_session.get(Employee, employee.id)
        assert emp.dismissal_date is None
        assert emp.is_active is True
        assert db_session.query(TimesheetEntry).filter_by(employee_id=emp.id).count() == 3

    def test_dismiss_with_confirm_clears_hours_after_the_date(
        self, client, db_session, admin_token, employee, company, calendar
    ):
        self._fill(client, admin_token, employee, company, [10, 11, 16, 17])

        r = client.post(
            f"/api/employees/{employee.id}/dismiss?confirm=true",
            headers=auth(admin_token),
            json={"dismissal_date": f"{YEAR}-06-15"},
        )
        assert r.status_code == 200, r.text

        db_session.expire_all()
        left = sorted(
            e.work_date.day
            for e in db_session.query(TimesheetEntry).filter_by(employee_id=employee.id)
        )
        assert left == [10, 11], "часы до увольнения остаются нетронутыми"

    def test_position_date_change_clears_only_that_workplace(
        self, client, db_session, admin_token, company, company2, dept, dept2,
        schedule, calendar,
    ):
        emp = make_employee(db_session, company, dept, schedule, name="Сидоров")
        extra = EmployeePosition(
            employee_id=emp.id, title="Электрик", is_primary=False, is_active=True,
            department_id=dept2.id, company_id=company2.id, schedule_id=schedule.id,
            pay_type="salary", rate=Decimal("30000"),
        )
        db_session.add(extra)
        db_session.commit()
        db_session.refresh(extra)

        for pos, comp in ((emp.primary_position, company), (extra, company2)):
            r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
                "employee_id": emp.id, "position_id": pos.id,
                "work_date": f"{YEAR}-06-20", "company_id": comp.id, "hours": 8,
            })
            assert r.status_code == 200, r.text

        r = client.patch(
            f"/api/employees/{emp.id}/positions/{extra.id}?confirm=true",
            headers=auth(admin_token),
            json={"dismissal_date": f"{YEAR}-06-15"},
        )
        assert r.status_code == 200, r.text

        db_session.expire_all()
        rows = db_session.query(TimesheetEntry).filter_by(employee_id=emp.id).all()
        assert [e.position_id for e in rows] == [emp.primary_position.id], (
            "чистится только закрытое рабочее место"
        )

    def test_hire_date_change_clears_days_before(
        self, client, db_session, admin_token, employee, company, calendar
    ):
        self._fill(client, admin_token, employee, company, [1, 2, 18])

        r = client.patch(
            f"/api/employees/{employee.id}?confirm=true",
            headers=auth(admin_token),
            json={"hire_date": f"{YEAR}-06-10"},
        )
        assert r.status_code == 200, r.text

        db_session.expire_all()
        left = sorted(
            e.work_date.day
            for e in db_session.query(TimesheetEntry).filter_by(employee_id=employee.id)
        )
        assert left == [18]

    def test_saving_without_touching_dates_clears_nothing(
        self, client, db_session, admin_token, employee, company, calendar
    ):
        """Обычное сохранение карточки часов не трогает и подтверждения не просит."""
        self._fill(client, admin_token, employee, company, [16, 17])
        r = client.patch(
            f"/api/employees/{employee.id}",
            headers=auth(admin_token),
            json={"full_name": "Иванов Иван Иванович"},
        )
        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.query(TimesheetEntry).filter_by(
            employee_id=employee.id
        ).count() == 2

    def test_widening_the_period_clears_nothing(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        """Снятие границы часов не удаляет — расширение периода безопасно."""
        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        self._fill(client, admin_token, emp, company, [10])
        r = client.patch(
            f"/api/employees/{emp.id}",
            headers=auth(admin_token),
            json={"dismissal_date": None},
        )
        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.query(TimesheetEntry).filter_by(employee_id=emp.id).count() == 1


class TestClosedPeriodIsProtected:
    """Часы в закрытом периоде не очищаются даже при подтверждении."""

    def test_closed_period_hours_survive_and_are_reported(
        self, client, db_session, admin_token, employee, company, dept, calendar
    ):
        from app.models.timesheet_periods import TimesheetPeriod

        # Часы за 20-е, затем период отдела закрывается.
        r = client.put("/api/timesheet/cell", headers=auth(admin_token), json={
            "employee_id": employee.id, "position_id": employee.primary_position.id,
            "work_date": f"{YEAR}-06-20", "company_id": company.id, "hours": 8,
        })
        assert r.status_code == 200, r.text

        period = (
            db_session.query(TimesheetPeriod)
            .filter_by(department_id=dept.id, year=YEAR, month=MONTH)
            .first()
        )
        period.status = "closed"
        db_session.commit()

        r = client.post(
            f"/api/employees/{employee.id}/dismiss",
            headers=auth(admin_token),
            json={"dismissal_date": f"{YEAR}-06-15"},
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["days"] == 0, "удалять нечего — период закрыт"
        assert detail["locked_days"] == 1
        assert detail["locked_months"], "месяц должен быть назван пользователю"

        # Подтверждение закрытый период тоже не трогает.
        r = client.post(
            f"/api/employees/{employee.id}/dismiss?confirm=true",
            headers=auth(admin_token),
            json={"dismissal_date": f"{YEAR}-06-15"},
        )
        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.query(TimesheetEntry).filter_by(
            employee_id=employee.id
        ).count() == 1


# ── Норма не меняется ─────────────────────────────────────────────────────────

class TestNormUnchanged:
    """Норма часов и дней остаётся МЕСЯЧНОЙ: оплата и так пропорциональна
    отработанному, и это существующее правило задача не трогает."""

    def test_norm_is_the_same_with_and_without_dismissal(
        self, client, db_session, admin_token, company, dept, schedule, calendar
    ):
        make_employee(db_session, company, dept, schedule, name="Без дат", tab="T-100")
        make_employee(
            db_session, company, dept, schedule, name="Уволен", tab="T-101",
            dismissal_date=D(YEAR, MONTH, 15),
        )
        r = client.get(
            f"/api/timesheet/{YEAR}/{MONTH}/payroll", headers=auth(admin_token)
        )
        assert r.status_code == 200, r.text
        rows = {row["employee_name"]: row for row in r.json()["employees"]}
        assert rows["Без дат"]["norm_hours"] == rows["Уволен"]["norm_hours"]
        assert rows["Без дат"]["norm_days"] == rows["Уволен"]["norm_days"]


# ── Права ─────────────────────────────────────────────────────────────────────

class TestTimekeeperAlsoBlocked:
    def test_rule_is_not_about_roles(
        self, db_session, company, dept, schedule
    ):
        """Правило про даты, а не про роль: проверка живёт в сервисе мутации и
        срабатывает для любого, кто дошёл до записи."""
        from app.services.employment_period import check_employment_period

        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        with pytest.raises(OutsideEmploymentPeriod):
            check_employment_period(
                db_session, emp.id, D(YEAR, MONTH, 20), emp.primary_position.id
            )


# ── Вахта: своя сетка дней, правило то же ─────────────────────────────────────

class TestVahta:
    """Раздел «Вахта» (task_vahta) ведёт смены своей сеткой — `GuardShift`
    вместо `TimesheetEntry`, — но период работы ограничивает её так же.

    Правило берётся из того же `services.employment_period`: второй копии быть
    не должно, иначе вахта и табель разойдутся.
    """

    @pytest.fixture
    def vahta(self, db_session, company, dept, schedule):
        from app.models.guard_posts import GuardPost, GuardSite, GuardZone

        # На пост встают только рабочие места ОХРАННОГО подразделения
        # (task_stage1 п.1.4) — отдел фикстуры обязан быть охранным.
        dept.is_guard_department = True
        zone = GuardZone(name="Зона 1", department_id=dept.id)
        db_session.add(zone)
        db_session.flush()
        site = GuardSite(
            zone_id=zone.id, name="Green Wood", shift_rate=Decimal("4000"),
        )
        db_session.add(site)
        db_session.flush()
        post = GuardPost(site_id=site.id, name="GW 1")
        db_session.add(post)
        db_session.commit()
        db_session.refresh(post)
        return post

    def _assign(self, db, post, employee, days=None):
        from app.services.guard_duty import create_assignment

        a = create_assignment(
            db, year=YEAR, month=MONTH, place=post,
            position=employee.primary_position if employee else None,
            days=days,
        )
        db.commit()
        return a

    def test_fill_all_days_stops_at_dismissal(
        self, db_session, vahta, company, dept, schedule
    ):
        """Постановка на пост отмечает ВЕСЬ месяц — но не после увольнения."""
        from app.services.guard_duty import marked_days

        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        a = self._assign(db_session, vahta, emp)
        days = sorted(marked_days(a))
        assert days, "до увольнения смены обязаны проставиться"
        assert max(days) == 15, f"после увольнения смен быть не должно: {days}"

    def test_fill_all_days_starts_at_hire(
        self, db_session, vahta, company, dept, schedule
    ):
        from app.services.guard_duty import marked_days

        emp = make_employee(
            db_session, company, dept, schedule, hire_date=D(YEAR, MONTH, 10)
        )
        a = self._assign(db_session, vahta, emp)
        assert min(marked_days(a)) == 10

    def test_employee_without_dates_gets_whole_month(
        self, db_session, vahta, employee
    ):
        """Опорная точка: без дат — весь месяц, как раньше."""
        from app.services.guard_duty import marked_days

        a = self._assign(db_session, vahta, employee)
        assert len(marked_days(a)) == 30  # июнь

    def test_empty_slot_is_not_restricted(self, db_session, vahta):
        """Пустой слот — место без человека, ограничивать нечем."""
        from app.services.guard_duty import marked_days

        a = self._assign(db_session, vahta, None)
        assert len(marked_days(a)) == 30

    def test_explicit_toggle_outside_period_is_refused(
        self, db_session, vahta, company, dept, schedule
    ):
        """Явный клик получает внятный отказ, а не тихое бездействие."""
        from app.services.guard_duty import GuardError, toggle_day

        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        a = self._assign(db_session, vahta, emp, days=set())
        with pytest.raises(GuardError) as exc:
            toggle_day(db_session, a, 20, True)
        assert "вне периода работы" in str(exc.value)

    def test_toggle_on_the_dismissal_day_itself_works(
        self, db_session, vahta, company, dept, schedule
    ):
        from app.services.guard_duty import marked_days, toggle_day

        emp = make_employee(
            db_session, company, dept, schedule, dismissal_date=D(YEAR, MONTH, 15)
        )
        a = self._assign(db_session, vahta, emp, days=set())
        toggle_day(db_session, a, 15, True)
        db_session.commit()
        assert 15 in marked_days(a)

    def test_existing_shift_outside_period_can_be_removed(
        self, db_session, vahta, company, dept, schedule
    ):
        """Смена, оставшаяся за новой границей, снимается — запрет только на
        ЗАПОЛНЕНИЕ, иначе её было бы нечем убрать."""
        from app.services.guard_duty import marked_days, toggle_day

        emp = make_employee(db_session, company, dept, schedule)
        a = self._assign(db_session, vahta, emp, days={20})
        emp.dismissal_date = D(YEAR, MONTH, 15)
        db_session.commit()

        toggle_day(db_session, a, 20, False)
        db_session.commit()
        assert 20 not in marked_days(a)

    def test_moonlighter_position_dates_apply(
        self, db_session, vahta, company, dept, schedule
    ):
        """Даты берутся с ПОЗИЦИИ строки: человек работает, подработка закрыта."""
        from app.services.guard_duty import marked_days

        emp = make_employee(
            db_session, company, dept, schedule, pos_dismissal=D(YEAR, MONTH, 12)
        )
        a = self._assign(db_session, vahta, emp)
        assert max(marked_days(a)) == 12
