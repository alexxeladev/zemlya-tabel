"""Распределение зарплаты по заявкам на подбор (task_hr_applications).

Отдел с флагом «распределение по заявкам» (у нас HR) делит зарплату СВОИХ
сотрудников по числу отработанных за месяц заявок для каждого юрлица; обычный
каскад (месяц% → карточка → отдел → часы) для него не применяется. Остальные
отделы не затронуты — это проверяется отдельно.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.company_shares import DepartmentCompanyShare
from app.models.department_applications import DepartmentApplication
from app.models.departments import Department
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.applications import application_percents, application_weights
from app.services.distribution import distribute
from tests.conftest import get_token

MAY_BASIC = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]

# Пример из задачи (реальный файл HR за июль): 8 юрлиц, 43 заявки.
EXAMPLE = {
    "ZMO": 16, "SD": 9, "EXP": 2, "KSRV": 2,
    "PROD": 3, "ESI": 6, "SEC": 2, "GHS": 3,
}


# ── Unit: проценты из заявок ──────────────────────────────────────────────────

class TestApplicationPercents:
    def test_example_from_task(self):
        """ЗМО 16 из 43 → 37.21%."""
        percents = application_percents({1: 16, 2: 9, 3: 2, 4: 2, 5: 3, 6: 6, 7: 2, 8: 3})
        assert percents[1] == Decimal("37.21")

    def test_percents_sum_to_exactly_100(self):
        """Проценты показываются человеку и обязаны давать ровно 100.00 —
        считаются тем же методом наибольшего остатка, что и деньги."""
        percents = application_percents({1: 16, 2: 9, 3: 2, 4: 2, 5: 3, 6: 6, 7: 2, 8: 3})
        assert sum(percents.values()) == Decimal("100.00")

    def test_percents_do_not_depend_on_employee(self):
        """Набор один на отдел: остаток округления НЕ уходит на основную компанию
        сотрудника, иначе у разных людей проценты разошлись бы."""
        counts = {1: 1, 2: 1, 3: 1}
        assert application_percents(counts) == application_percents(counts)
        assert sum(application_percents(counts).values()) == Decimal("100.00")

    def test_zero_counts_ignored(self):
        assert application_percents({1: 5, 2: 0}) == {1: Decimal("100.00")}

    def test_no_applications_no_percents(self):
        assert application_percents({}) == {}

    def test_amount_from_counts_not_from_rounded_percent(self):
        """320000 × 16/43 = 119069.77 → 119070 ₽ (шаг округления — рубль, как во
        всём распределении). Считать от округлённого процента нельзя: 37.21% дало
        бы 119072."""
        counts = {1: 16, 2: 9, 3: 2, 4: 2, 5: 3, 6: 6, 7: 2, 8: 3}
        exact = Decimal("320000") * 16 / 43
        assert exact.quantize(Decimal("0.01")) == Decimal("119069.77")
        amounts = distribute(Decimal("320000"), application_weights(counts), main_key=8)
        assert amounts[1] == Decimal("119070")
        assert sum(amounts.values()) == Decimal("320000")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def companies(db_session: Session) -> dict[str, Company]:
    names = {
        "ZMO": "ЗМО", "SD": "СтройДеп", "EXP": "Эксплуатация", "KSRV": "К-Сервис",
        "PROD": "Производство", "ESI": "ЭкоСтройИнвест", "SEC": "Секьюрити",
        "GHS": "ГХС",
    }
    cs = {code: Company(code=code, name=name, is_active=True) for code, name in names.items()}
    db_session.add_all(cs.values())
    db_session.commit()
    for c in cs.values():
        db_session.refresh(c)
    return cs


@pytest.fixture
def hr_dept(db_session: Session) -> Department:
    d = Department(name="HR", code="HR", is_active=True,
                   uses_applications_distribution=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def other_dept(db_session: Session) -> Department:
    d = Department(name="ИТО", code="ITO", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def calendar(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_BASIC, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(full_name="HR Admin", email="hradmin@example.com",
                   hashed_password=hash_password("admin123"), role="admin",
                   is_active=True, must_change_password=False, is_system_admin=True)
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def _worker(db: Session, name: str, tab: str, rate, dept, company, schedule) -> Employee:
    emp = Employee(full_name=name, tab_number=tab, is_active=True,
                   rate=Decimal(rate), schedule_id=schedule.id,
                   default_company_id=company.id, department_id=dept.id)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@pytest.fixture
def recruiter(db_session, hr_dept, companies, schedule) -> Employee:
    """Оклад 320000 и полностью отработанная норма → «Итого начислено» = 320000,
    ровно как в примере задачи. Основная компания — ГХС, чтобы остаток округления
    не садился на проверяемую долю ЗМО."""
    return _worker(db_session, "Рекрутёр", "HR-1", "320000", hr_dept,
                   companies["GHS"], schedule)


@pytest.fixture
def hr_head(db_session, hr_dept, companies, schedule) -> Employee:
    return _worker(db_session, "Руководитель HR", "HR-2", "150000", hr_dept,
                   companies["GHS"], schedule)


def _full_norm(db: Session, emp: Employee, company: Company):
    for d in MAY_WORKDAYS:
        db.add(TimesheetEntry(employee_id=emp.id, position_id=emp.primary_position.id,
                              work_date=date(2026, 5, d), company_id=company.id, hours=8))
    db.commit()


def _set_applications(db: Session, dept: Department, companies, counts: dict[str, int],
                      year: int = 2026, month: int = 5):
    """Заводит заявки по общему числу: одна закрыта, остальные в работе —
    распределение считается от суммы, поэтому раскладка на него не влияет."""
    for code, n in counts.items():
        db.add(DepartmentApplication(
            department_id=dept.id, company_id=companies[code].id,
            year=year, month=month,
            in_progress=max(0, n - 1), closed=min(1, n),
        ))
    db.commit()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _statement(client: TestClient, token: str, year: int = 2026, month: int = 5) -> dict:
    r = client.get(f"/api/timesheet/{year}/{month}/statement", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()


def _row(statement: dict, employee_id: int) -> dict:
    rows = [r for r in statement["rows"] if r["employee_id"] == employee_id]
    assert rows, "строка сотрудника не найдена в ведомости"
    return rows[0]


def _amount(row: dict, company: Company) -> Decimal:
    for d in row["distribution"]:
        if d["company_id"] == company.id:
            return Decimal(d["amount"])
    return Decimal("0")


# ── Распределение HR по заявкам ───────────────────────────────────────────────

class TestHrDistribution:
    def test_employee_split_by_applications(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """320000 × 16/43 = 119069.77 → 119070 ₽ на ЗМО, сумма частей = 320000."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")

        row = _row(_statement(client, token), recruiter.id)
        assert Decimal(row["accrued_total"]) == Decimal("320000")
        assert row["distribution_source"] == "applications"
        assert _amount(row, companies["ZMO"]) == Decimal("119070")
        assert sum(Decimal(d["amount"]) for d in row["distribution"]) == Decimal("320000")

    def test_percent_shown_next_to_amount(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """ЗМО 16/43 = 37.21%, сумма процентов ровно 100."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")

        row = _row(_statement(client, token), recruiter.id)
        zmo = next(d for d in row["distribution"] if d["company_id"] == companies["ZMO"].id)
        assert Decimal(zmo["percent"]) == Decimal("37.21")
        assert Decimal(row["percent_sum"]) == Decimal("100.00")

    def test_all_hr_employees_share_the_same_percents(
        self, client, db_session, admin, calendar, recruiter, hr_head, hr_dept, companies
    ):
        """Заявки отработаны отделом → проценты у всех сотрудников HR одни."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _full_norm(db_session, hr_head, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")
        statement = _statement(client, token)

        def percents(emp_id):
            row = _row(statement, emp_id)
            return {d["company_id"]: Decimal(d["percent"]) for d in row["distribution"]}

        assert percents(recruiter.id) == percents(hr_head.id)
        # …а суммы разные — оклады разные.
        head_row = _row(statement, hr_head.id)
        assert Decimal(head_row["accrued_total"]) == Decimal("150000")
        assert sum(Decimal(d["amount"]) for d in head_row["distribution"]) == Decimal("150000")

    def test_applications_replace_cascade(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Дефолт отдела задан, но для HR он не применяется — заявки заменяют
        каскад целиком."""
        _full_norm(db_session, recruiter, companies["GHS"])
        db_session.add(DepartmentCompanyShare(department_id=hr_dept.id,
                                              company_id=companies["SEC"].id,
                                              percent=Decimal("100")))
        db_session.commit()
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")

        row = _row(_statement(client, token), recruiter.id)
        assert row["distribution_source"] == "applications"
        # 100% дефолта отдела не применились: SEC получила свои 2 заявки из 43.
        assert _amount(row, companies["SEC"]) == Decimal("14884")

    def test_monthly_applications_do_not_leak_to_other_month(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Заявки помесячные: заведённые на май на июнь не действуют."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE, month=5)
        token = get_token(client, "hradmin@example.com", "admin123")

        assert _row(_statement(client, token, month=5), recruiter.id)["distribution_source"] \
            == "applications"
        june = _row(_statement(client, token, month=6), recruiter.id)
        assert june["distribution_source"] != "applications"
        assert june["distribution_note"]

    def test_different_counts_per_month(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Заявки вводятся заново каждый месяц — проценты месяцев независимы."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE, month=5)
        _set_applications(db_session, hr_dept, companies, {"ZMO": 1, "SD": 1}, month=6)
        token = get_token(client, "hradmin@example.com", "admin123")

        may = _row(_statement(client, token, month=5), recruiter.id)
        june = _row(_statement(client, token, month=6), recruiter.id)
        may_zmo = next(d for d in may["distribution"] if d["company_id"] == companies["ZMO"].id)
        june_zmo = next(d for d in june["distribution"] if d["company_id"] == companies["ZMO"].id)
        assert Decimal(may_zmo["percent"]) == Decimal("37.21")
        assert Decimal(june_zmo["percent"]) == Decimal("50.00")

    def test_flag_without_applications_falls_back_to_cascade(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Флаг есть, заявок за месяц нет → обычный каскад + предупреждение
        (молча обнулить распределение отдела нельзя)."""
        _full_norm(db_session, recruiter, companies["GHS"])
        db_session.add(DepartmentCompanyShare(department_id=hr_dept.id,
                                              company_id=companies["SEC"].id,
                                              percent=Decimal("100")))
        db_session.commit()
        token = get_token(client, "hradmin@example.com", "admin123")

        row = _row(_statement(client, token), recruiter.id)
        assert row["distribution_source"] == "department"
        assert _amount(row, companies["SEC"]) == Decimal("320000")
        assert "Заявки" in row["distribution_note"]


# ── Регрессия: другие отделы ──────────────────────────────────────────────────

class TestOtherDepartmentsUntouched:
    def test_department_default_still_wins_for_non_hr(
        self, client, db_session, admin, calendar, other_dept, companies, schedule
    ):
        """Отдел без флага распределяется каскадом, как раньше."""
        worker = _worker(db_session, "Инженер", "IT-1", "100000", other_dept,
                         companies["ZMO"], schedule)
        _full_norm(db_session, worker, companies["ZMO"])
        db_session.add(DepartmentCompanyShare(department_id=other_dept.id,
                                              company_id=companies["SD"].id,
                                              percent=Decimal("100")))
        db_session.commit()
        token = get_token(client, "hradmin@example.com", "admin123")

        row = _row(_statement(client, token), worker.id)
        assert row["distribution_source"] == "department"
        assert row["distribution_note"] is None
        assert _amount(row, companies["SD"]) == Decimal("100000")

    def test_hr_applications_do_not_touch_other_department(
        self, client, db_session, admin, calendar, recruiter, hr_dept, other_dept,
        companies, schedule
    ):
        """Заявки HR не влияют на соседний отдел — он остаётся на авто по часам."""
        worker = _worker(db_session, "Инженер", "IT-1", "100000", other_dept,
                         companies["ZMO"], schedule)
        _full_norm(db_session, worker, companies["ZMO"])
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")
        statement = _statement(client, token)

        assert _row(statement, recruiter.id)["distribution_source"] == "applications"
        other = _row(statement, worker.id)
        assert other["distribution_source"] == "hours"
        assert _amount(other, companies["ZMO"]) == Decimal("100000")


# ── API заявок ────────────────────────────────────────────────────────────────

class TestApplicationsApi:
    def test_set_and_read_back(self, client, db_session, admin, hr_dept, companies):
        token = get_token(client, "hradmin@example.com", "admin123")
        payload = {
            "department_id": hr_dept.id, "year": 2026, "month": 5,
            "applications": [
                {"company_id": companies[code].id, "in_progress": n - 1, "closed": 1}
                for code, n in EXAMPLE.items()
            ],
        }
        r = client.put("/api/timesheet/applications", json=payload, headers=_h(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_applications"] == 43
        assert body["is_empty"] is False
        zmo = next(a for a in body["applications"] if a["company_id"] == companies["ZMO"].id)
        assert (zmo["in_progress"], zmo["closed"]) == (15, 1)
        # «Всего» не хранится, а считается — сумма частей, как в файле HR.
        assert zmo["count"] == 16
        assert Decimal(zmo["percent"]) == Decimal("37.21")
        assert body["total_in_progress"] == 43 - len(EXAMPLE)
        assert body["total_closed"] == len(EXAMPLE)
        assert body["total_in_progress"] + body["total_closed"] == 43

        r = client.get("/api/timesheet/2026/5/applications", headers=_h(token))
        assert r.status_code == 200
        assert r.json()[0]["total_applications"] == 43

    def test_replaces_previous_set(self, client, db_session, admin, hr_dept, companies):
        token = get_token(client, "hradmin@example.com", "admin123")
        base = {"department_id": hr_dept.id, "year": 2026, "month": 5}
        client.put("/api/timesheet/applications", headers=_h(token), json={
            **base, "applications": [
                {"company_id": companies["ZMO"].id, "in_progress": 16, "closed": 0}]})
        r = client.put("/api/timesheet/applications", headers=_h(token), json={
            **base, "applications": [
                {"company_id": companies["SD"].id, "in_progress": 1, "closed": 3}]})
        assert r.status_code == 200
        assert r.json()["total_applications"] == 4
        assert len(r.json()["applications"]) == 1

    def test_zero_counts_are_not_stored(self, client, db_session, admin, hr_dept, companies):
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.put("/api/timesheet/applications", headers=_h(token), json={
            "department_id": hr_dept.id, "year": 2026, "month": 5,
            "applications": [
                {"company_id": companies["ZMO"].id, "in_progress": 3, "closed": 2},
                {"company_id": companies["SD"].id, "in_progress": 0, "closed": 0},
            ]})
        assert r.status_code == 200
        assert len(r.json()["applications"]) == 1
        assert r.json()["is_empty"] is False

    def test_department_without_flag_rejected(
        self, client, db_session, admin, other_dept, companies
    ):
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.put("/api/timesheet/applications", headers=_h(token), json={
            "department_id": other_dept.id, "year": 2026, "month": 5,
            "applications": [
                {"company_id": companies["ZMO"].id, "in_progress": 5, "closed": 0}]})
        assert r.status_code == 422
        assert "заявк" in r.json()["detail"].lower()

    def test_only_flagged_departments_listed(
        self, client, db_session, admin, hr_dept, other_dept
    ):
        """В выдаче только отделы с флагом; отдел с флагом и без заявок —
        с признаком is_empty (иначе блок ввода негде показать)."""
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.get("/api/timesheet/2026/5/applications", headers=_h(token))
        assert r.status_code == 200
        body = r.json()
        assert [d["department_id"] for d in body] == [hr_dept.id]
        assert body[0]["is_empty"] is True
        assert body[0]["total_applications"] == 0

    def test_applications_in_month_response(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.get(f"/api/timesheet/2026/5?department_id={hr_dept.id}", headers=_h(token))
        assert r.status_code == 200
        apps = r.json()["applications"]
        assert len(apps) == 1
        assert apps[0]["total_applications"] == 43

    def test_timekeeper_cannot_read_or_write(
        self, client, db_session, admin, hr_dept, companies
    ):
        keeper = Employee(full_name="Табельщик", email="hrkeeper@example.com",
                          hashed_password=hash_password("keeper123"), role="timekeeper",
                          is_active=True, must_change_password=False)
        keeper.managed_departments = [hr_dept]
        db_session.add(keeper)
        db_session.commit()
        token = get_token(client, "hrkeeper@example.com", "keeper123")

        assert client.get("/api/timesheet/2026/5/applications",
                          headers=_h(token)).status_code == 403
        assert client.put("/api/timesheet/applications", headers=_h(token), json={
            "department_id": hr_dept.id, "year": 2026, "month": 5,
            "applications": [
                {"company_id": companies["ZMO"].id, "in_progress": 5, "closed": 0}],
        }).status_code == 403
        # …и в табеле заявок он тоже не видит
        r = client.get(f"/api/timesheet/2026/5?department_id={hr_dept.id}", headers=_h(token))
        assert r.status_code == 200
        assert r.json()["applications"] == []

    def test_manager_of_other_department_forbidden(
        self, client, db_session, admin, hr_dept, other_dept, companies
    ):
        mgr = Employee(full_name="Чужой руководитель", email="hrother@example.com",
                       hashed_password=hash_password("mgr123"), role="manager",
                       is_active=True, must_change_password=False)
        mgr.managed_departments = [other_dept]
        db_session.add(mgr)
        db_session.commit()
        token = get_token(client, "hrother@example.com", "mgr123")

        assert client.put("/api/timesheet/applications", headers=_h(token), json={
            "department_id": hr_dept.id, "year": 2026, "month": 5,
            "applications": [
                {"company_id": companies["ZMO"].id, "in_progress": 5, "closed": 0}],
        }).status_code == 403
        r = client.get("/api/timesheet/2026/5/applications", headers=_h(token))
        assert r.status_code == 200
        assert r.json() == []


# ── Флаг отдела ───────────────────────────────────────────────────────────────

class TestDepartmentFlag:
    def test_flag_defaults_to_false(self, db_session: Session):
        d = Department(name="Новый", code="NEW", is_active=True)
        db_session.add(d)
        db_session.commit()
        db_session.refresh(d)
        assert d.uses_applications_distribution is False

    def test_admin_can_switch_flag(self, client, db_session, admin, other_dept):
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.patch(f"/api/departments/{other_dept.id}",
                         json={"uses_applications_distribution": True}, headers=_h(token))
        assert r.status_code == 200
        assert r.json()["uses_applications_distribution"] is True

        r = client.patch(f"/api/departments/{other_dept.id}",
                         json={"uses_applications_distribution": False}, headers=_h(token))
        assert r.json()["uses_applications_distribution"] is False

    def test_other_patch_does_not_reset_flag(self, client, db_session, admin, hr_dept):
        """Правка соседнего поля не сбрасывает флаг: null означает «не менять»."""
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.patch(f"/api/departments/{hr_dept.id}",
                         json={"name": "Подбор персонала"}, headers=_h(token))
        assert r.status_code == 200
        assert r.json()["uses_applications_distribution"] is True


# ── Заявки «в работе» / «закрытые» и распределение в табеле ───────────────────

class TestApplicationsBreakdown:
    def test_total_is_sum_of_parts(self, client, db_session, admin, hr_dept, companies):
        """«Заявок» не хранится, а считается: в работе + закрытые (как в файле HR)."""
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.put("/api/timesheet/applications", headers=_h(token), json={
            "department_id": hr_dept.id, "year": 2026, "month": 5,
            "applications": [
                {"company_id": companies["ZMO"].id, "in_progress": 13, "closed": 3},
                {"company_id": companies["SD"].id, "in_progress": 8, "closed": 1},
            ]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_in_progress"] == 21
        assert body["total_closed"] == 4
        assert body["total_applications"] == 25
        zmo = next(a for a in body["applications"] if a["company_id"] == companies["ZMO"].id)
        assert zmo["count"] == 16

    def test_split_does_not_change_distribution(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Распределение считается от ОБЩЕГО числа: как заявки разложены на
        «в работе» и «закрытые», на суммы не влияет."""
        _full_norm(db_session, recruiter, companies["GHS"])
        token = get_token(client, "hradmin@example.com", "admin123")
        base = {"department_id": hr_dept.id, "year": 2026, "month": 5}
        payloads = [
            [{"company_id": companies[c].id, "in_progress": n, "closed": 0}
             for c, n in EXAMPLE.items()],
            [{"company_id": companies[c].id, "in_progress": 0, "closed": n}
             for c, n in EXAMPLE.items()],
        ]
        amounts = []
        for applications in payloads:
            client.put("/api/timesheet/applications", headers=_h(token),
                       json={**base, "applications": applications})
            row = _row(_statement(client, token), recruiter.id)
            amounts.append(_amount(row, companies["ZMO"]))
        assert amounts[0] == amounts[1] == Decimal("119070")

    def test_distribution_in_month_response(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Табель отдела «по заявкам» отдаёт суммы распределения — те же, что
        в ведомости (фронт их не пересчитывает)."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")

        r = client.get(
            f"/api/timesheet/2026/5?department_id={hr_dept.id}&include_payroll=true",
            headers=_h(token),
        )
        assert r.status_code == 200, r.text
        rows = r.json()["applications_distribution"]
        assert len(rows) == 1
        row = rows[0]
        assert row["employee_id"] == recruiter.id
        assert Decimal(row["accrued_total"]) == Decimal("320000")
        assert Decimal(row["amounts"][str(companies["ZMO"].id)]) == Decimal("119070")
        assert sum(Decimal(v) for v in row["amounts"].values()) == Decimal("320000")

        # …и ровно то же число в ведомости
        stmt_row = _row(_statement(client, token), recruiter.id)
        assert _amount(stmt_row, companies["ZMO"]) == Decimal("119070")

    def test_no_distribution_without_payroll(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Без расчёта делить нечего — блок пуст (табель после правки часов
        перечитывает только часы)."""
        _full_norm(db_session, recruiter, companies["GHS"])
        _set_applications(db_session, hr_dept, companies, EXAMPLE)
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.get(f"/api/timesheet/2026/5?department_id={hr_dept.id}", headers=_h(token))
        assert r.status_code == 200
        assert r.json()["applications_distribution"] == []

    def test_no_distribution_for_other_departments(
        self, client, db_session, admin, calendar, other_dept, companies, schedule
    ):
        """У отдела без флага блока распределения в табеле нет вовсе."""
        worker = _worker(db_session, "Инженер", "IT-1", "100000", other_dept,
                         companies["ZMO"], schedule)
        _full_norm(db_session, worker, companies["ZMO"])
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.get(
            f"/api/timesheet/2026/5?department_id={other_dept.id}&include_payroll=true",
            headers=_h(token),
        )
        assert r.status_code == 200
        assert r.json()["applications"] == []
        assert r.json()["applications_distribution"] == []

    def test_flag_without_applications_gives_no_distribution_block(
        self, client, db_session, admin, calendar, recruiter, hr_dept, companies
    ):
        """Флаг есть, заявок нет → сумм показывать нечего: распределение в этом
        месяце идёт каскадом, а блок соврал бы."""
        _full_norm(db_session, recruiter, companies["GHS"])
        token = get_token(client, "hradmin@example.com", "admin123")
        r = client.get(
            f"/api/timesheet/2026/5?department_id={hr_dept.id}&include_payroll=true",
            headers=_h(token),
        )
        assert r.status_code == 200
        assert r.json()["applications"][0]["is_empty"] is True
        assert r.json()["applications_distribution"] == []
