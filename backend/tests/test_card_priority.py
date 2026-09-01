"""Приоритет распределения из КАРТОЧКИ над количественным показателем отдела
(task_card_priority).

Отдел с флагом «распределение по количественному показателю» (заявки у HR, АРМ
у ИТ) делится по показателю — но у отдельного рабочего места может быть своё
распределение в карточке, и тогда оно применяется ЦЕЛИКОМ, а показатель для
этой позиции не используется вовсе. Это ПОЛНАЯ ЗАМЕНА, не смешивание: складывать
карточку с показателем нельзя.

Остальные сотрудники отдела продолжают делиться по показателю — то, что кто-то
ушёл на карточку, их процентов не касается.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.company_shares import (
    CompanyShareOverride,
    DepartmentCompanyShare,
    EmployeeCompanyShare,
)
from app.models.department_quantities import DepartmentQuantity
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from tests.conftest import get_token

MAY_BASIC = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]

# АРМ ИТ-отдела из task_it_arm_distribution: 104 на 8 юрлиц.
ARM = {
    "ZMO": 45, "SD": 6, "EXP": 13, "KSRV": 7,
    "PROD": 5, "ESI": 9, "SEC": 12, "GHS": 7,
}
# Распределение 57 000 по этим АРМ (проверочный пример ТЗ ИТ).
BY_ARM_57K = {
    "ZMO": 25000, "SD": 3000, "EXP": 7000, "KSRV": 4000,
    "PROD": 3000, "ESI": 5000, "SEC": 6000, "GHS": 4000,
}


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def companies(db_session: Session) -> dict[str, Company]:
    names = {
        "ZMO": "Земля МО", "SD": "СтройДеп", "EXP": "К-Эксплуатация",
        "KSRV": "К-Сервис", "PROD": "Гермес", "ESI": "ЭкоСтройИнвест",
        "SEC": "K-Security", "GHS": "Грин Хаус Строй",
    }
    cs = {code: Company(code=code, name=name, is_active=True) for code, name in names.items()}
    db_session.add_all(cs.values())
    db_session.commit()
    for c in cs.values():
        db_session.refresh(c)
    return cs


@pytest.fixture
def it_dept(db_session: Session) -> Department:
    d = Department(name="ИТ", code="IT", is_active=True,
                   uses_quantity_distribution=True, quantity_metric_name="АРМ")
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def plain_dept(db_session: Session) -> Department:
    d = Department(name="Бухгалтерия", code="ACC", is_active=True)
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
    emp = Employee(full_name="Админ", email="cardadmin@example.com",
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


def _full_norm(db: Session, emp: Employee, company: Company, position=None):
    pos = position or emp.primary_position
    for d in MAY_WORKDAYS:
        db.add(TimesheetEntry(employee_id=emp.id, position_id=pos.id,
                              work_date=date(2026, 5, d), company_id=company.id, hours=8))
    db.commit()


def _set_arm(db: Session, dept: Department, companies, counts: dict[str, int],
             year: int = 2026, month: int = 5):
    for code, n in counts.items():
        db.add(DepartmentQuantity(department_id=dept.id, company_id=companies[code].id,
                                  year=year, month=month, part1=n, part2=0))
    db.commit()


def _card(db: Session, emp: Employee, shares: dict, position=None):
    """Распределение в карточке РАБОЧЕГО МЕСТА: {company: процент}."""
    pos = position or emp.primary_position
    for company, percent in shares.items():
        db.add(EmployeeCompanyShare(employee_id=emp.id, position_id=pos.id,
                                    company_id=company.id, percent=Decimal(str(percent))))
    db.commit()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _statement(client: TestClient, token: str, year: int = 2026, month: int = 5) -> dict:
    r = client.get(f"/api/timesheet/{year}/{month}/statement", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()


def _rows(statement: dict, employee_id: int) -> list[dict]:
    return [r for r in statement["rows"] if r["employee_id"] == employee_id]


def _row(statement: dict, employee_id: int) -> dict:
    rows = _rows(statement, employee_id)
    assert rows, "строка сотрудника не найдена в ведомости"
    return rows[0]


def _amounts(row: dict) -> dict[int, Decimal]:
    return {d["company_id"]: Decimal(d["amount"]) for d in row["distribution"]}


def _amount(row: dict, company: Company) -> Decimal:
    return _amounts(row).get(company.id, Decimal("0"))


@pytest.fixture
def engineer(db_session, it_dept, companies, schedule) -> Employee:
    """Оклад 57 000, полная норма, удержаний нет → «К выплате» ровно 57 000."""
    return _worker(db_session, "Инженер", "IT-1", "57000", it_dept,
                   companies["GHS"], schedule)


@pytest.fixture
def token(client, admin) -> str:
    return get_token(client, "cardadmin@example.com", "admin123")


# ── Карточка перебивает показатель ────────────────────────────────────────────

class TestCardBeatsQuantity:
    def test_card_replaces_quantity_entirely(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """В карточке «Секьюрити 100%» → весь расчёт на Секьюрити, АРМ не участвуют."""
        _full_norm(db_session, engineer, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 100})
        _set_arm(db_session, it_dept, companies, ARM)

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "employee"
        assert _amount(row, companies["SEC"]) == Decimal("57000")
        # Ни рубля по АРМ: доли показателя не подмешиваются к карточке.
        assert _amount(row, companies["ZMO"]) == Decimal("0")
        assert Decimal(row["distribution_total"]) == Decimal("57000")

    def test_card_two_companies(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Карточка 50/50 → пополам, а не по процентам АРМ."""
        _full_norm(db_session, engineer, companies["GHS"])
        _card(db_session, engineer, {companies["ZMO"]: 50, companies["EXP"]: 50})
        _set_arm(db_session, it_dept, companies, ARM)

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "employee"
        # 28 500 каждой floor-ится до 28 000, недостающая тысяча уходит по
        # порядку юрлиц справочника (хвосты равные) — Земля МО идёт первой.
        assert _amount(row, companies["ZMO"]) == Decimal("29000")
        assert _amount(row, companies["EXP"]) == Decimal("28000")
        assert Decimal(row["distribution_total"]) == Decimal("57000")

    def test_without_card_quantity_applies(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Карточка не задана → распределение по АРМ, как и было."""
        _full_norm(db_session, engineer, companies["GHS"])
        _set_arm(db_session, it_dept, companies, ARM)

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "quantity"
        amounts = _amounts(row)
        assert {code: amounts[companies[code].id] for code in ARM} == {
            code: Decimal(v) for code, v in BY_ARM_57K.items()
        }

    def test_empty_card_is_not_set(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Нулевая строка в карточке — это «не задано»: идём к показателю."""
        _full_norm(db_session, engineer, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 0})
        _set_arm(db_session, it_dept, companies, ARM)

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "quantity"
        assert _amount(row, companies["ZMO"]) == Decimal("25000")

    def test_card_percent_must_be_100_on_save(
        self, client, db_session, engineer, companies, token
    ):
        """«Задано в карточке» всегда означает 100%: частичный набор не сохранить."""
        r = client.put(
            f"/api/employees/{engineer.id}/company-shares", headers=_h(token),
            json={"shares": [{"company_id": companies["SEC"].id, "percent": 50}]},
        )
        assert r.status_code == 422, r.text


# ── Смешанный отдел ───────────────────────────────────────────────────────────

class TestMixedDepartment:
    def test_card_and_quantity_coexist(
        self, client, db_session, calendar, engineer, it_dept, companies, schedule, token
    ):
        """Один сотрудник отдела по карточке, другой по АРМ — одновременно."""
        other = _worker(db_session, "Админ сети", "IT-2", "57000", it_dept,
                        companies["GHS"], schedule)
        _full_norm(db_session, engineer, companies["GHS"])
        _full_norm(db_session, other, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 100})
        _set_arm(db_session, it_dept, companies, ARM)

        statement = _statement(client, token)
        card_row = _row(statement, engineer.id)
        qty_row = _row(statement, other.id)

        assert card_row["distribution_source"] == "employee"
        assert _amount(card_row, companies["SEC"]) == Decimal("57000")

        # Уход соседа на карточку процентов показателя не меняет: они считаются
        # от количеств отдела, а не от числа делящихся по ним людей.
        assert qty_row["distribution_source"] == "quantity"
        amounts = _amounts(qty_row)
        assert {code: amounts[companies[code].id] for code in ARM} == {
            code: Decimal(v) for code, v in BY_ARM_57K.items()
        }

    def test_department_totals_include_both(
        self, client, db_session, calendar, engineer, it_dept, companies, schedule, token
    ):
        """Итог ведомости по юрлицам сходится и в смешанном отделе."""
        other = _worker(db_session, "Админ сети", "IT-2", "57000", it_dept,
                        companies["GHS"], schedule)
        _full_norm(db_session, engineer, companies["GHS"])
        _full_norm(db_session, other, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 100})
        _set_arm(db_session, it_dept, companies, ARM)

        statement = _statement(client, token)
        totals = {int(cid): Decimal(v) for cid, v in statement["distribution_totals"].items()}
        assert sum(totals.values()) == Decimal("114000")
        # SEC = 57 000 карточки + 6 000 своей доли АРМ у второго сотрудника.
        assert totals[companies["SEC"].id] == Decimal("63000")

    def test_position_level_not_employee_level(
        self, client, db_session, calendar, it_dept, companies, schedule, token
    ):
        """У совместителя карточка одной позиции не уводит с показателя вторую."""
        emp = _worker(db_session, "Совместитель", "IT-3", "57000", it_dept,
                      companies["GHS"], schedule)
        emp.positions.append(EmployeePosition(
            title="Электрик", rate=Decimal("57000"), schedule_id=schedule.id,
            department_id=it_dept.id, company_id=companies["GHS"].id,
        ))
        db_session.commit()
        db_session.refresh(emp)
        second = [p for p in emp.positions if not p.is_primary][0]

        _full_norm(db_session, emp, companies["GHS"])
        _full_norm(db_session, emp, companies["GHS"], position=second)
        _card(db_session, emp, {companies["SEC"]: 100})  # только основная
        _set_arm(db_session, it_dept, companies, ARM)

        rows = {r["position_id"]: r for r in _rows(_statement(client, token), emp.id)}
        primary_row = rows[emp.primary_position.id]
        second_row = rows[second.id]

        assert primary_row["distribution_source"] == "employee"
        assert _amount(primary_row, companies["SEC"]) == Decimal("57000")
        assert second_row["distribution_source"] == "quantity"
        assert _amount(second_row, companies["ZMO"]) == Decimal("25000")


# ── Что не изменилось ─────────────────────────────────────────────────────────

class TestUnchanged:
    def test_plain_department_cascade_intact(
        self, client, db_session, calendar, plain_dept, companies, schedule, token
    ):
        """Обычный отдел: карточка по-прежнему уровень каскада, не исключение."""
        worker = _worker(db_session, "Бухгалтер", "ACC-1", "57000", plain_dept,
                         companies["ZMO"], schedule)
        _full_norm(db_session, worker, companies["ZMO"])
        _card(db_session, worker, {companies["SEC"]: 100})

        row = _row(_statement(client, token), worker.id)
        assert row["distribution_source"] == "employee"
        assert _amount(row, companies["SEC"]) == Decimal("57000")

    def test_plain_department_month_override_still_wins(
        self, client, db_session, calendar, plain_dept, companies, schedule, token
    ):
        """В обычном отделе месячная правка по-прежнему выше карточки."""
        worker = _worker(db_session, "Бухгалтер", "ACC-1", "57000", plain_dept,
                         companies["ZMO"], schedule)
        _full_norm(db_session, worker, companies["ZMO"])
        _card(db_session, worker, {companies["SEC"]: 100})
        db_session.add(CompanyShareOverride(
            employee_id=worker.id, position_id=worker.primary_position.id,
            company_id=companies["EXP"].id, percent=Decimal("100"),
            year=2026, month=5))
        db_session.commit()

        row = _row(_statement(client, token), worker.id)
        assert row["distribution_source"] == "month"
        assert _amount(row, companies["EXP"]) == Decimal("57000")

    def test_department_default_still_replaced_by_quantity(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Дефолт ОТДЕЛА показатель по-прежнему заменяет — исключение только
        карточка позиции."""
        _full_norm(db_session, engineer, companies["GHS"])
        db_session.add(DepartmentCompanyShare(department_id=it_dept.id,
                                              company_id=companies["SEC"].id,
                                              percent=Decimal("100")))
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "quantity"
        assert _amount(row, companies["SEC"]) == Decimal("6000")

    def test_month_override_still_ignored_in_quantity_department(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Правка на месяц в отделе с показателем не применяется: она в ведомости
        заблокирована, исключения задаются только в карточке."""
        _full_norm(db_session, engineer, companies["GHS"])
        db_session.add(CompanyShareOverride(
            employee_id=engineer.id, position_id=engineer.primary_position.id,
            company_id=companies["SEC"].id, percent=Decimal("100"),
            year=2026, month=5))
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "quantity"
        assert _amount(row, companies["SEC"]) == Decimal("6000")

    def test_no_counts_falls_back_to_cascade(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Показатель за месяц не заведён → обычный каскад и предупреждение."""
        _full_norm(db_session, engineer, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 100})

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "employee"
        assert "АРМ" in (row["distribution_note"] or "")


# ── Признак «отдел делится по показателю» для интерфейса ──────────────────────

class TestQuantityMetricFlag:
    def test_metric_name_on_both_kinds_of_rows(
        self, client, db_session, calendar, engineer, it_dept, companies, schedule, token
    ):
        """Ведомость помечает показателем ВСЕ строки такого отдела — и те, что
        ушли на карточку: ручная правка процентов остаётся заблокированной для
        всего отдела."""
        other = _worker(db_session, "Админ сети", "IT-2", "57000", it_dept,
                        companies["GHS"], schedule)
        _full_norm(db_session, engineer, companies["GHS"])
        _full_norm(db_session, other, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 100})
        _set_arm(db_session, it_dept, companies, ARM)

        statement = _statement(client, token)
        assert _row(statement, engineer.id)["quantity_metric_name"] == "АРМ"
        assert _row(statement, other.id)["quantity_metric_name"] == "АРМ"

    def test_no_metric_name_for_plain_department(
        self, client, db_session, calendar, plain_dept, companies, schedule, token
    ):
        worker = _worker(db_session, "Бухгалтер", "ACC-1", "57000", plain_dept,
                         companies["ZMO"], schedule)
        _full_norm(db_session, worker, companies["ZMO"])

        assert _row(_statement(client, token), worker.id)["quantity_metric_name"] is None

    def test_no_metric_name_without_counts(
        self, client, db_session, calendar, engineer, it_dept, companies, token
    ):
        """Флаг стоит, а показателя за месяц нет → отдел временно на каскаде,
        и правка процентов остаётся доступной, как и раньше."""
        _full_norm(db_session, engineer, companies["GHS"])

        assert _row(_statement(client, token), engineer.id)["quantity_metric_name"] is None


# ── Блок распределения в табеле ───────────────────────────────────────────────

class TestTimesheetDistributionBlock:
    def test_block_matches_statement_for_both(
        self, client, db_session, calendar, engineer, it_dept, companies, schedule, token
    ):
        """Суммы в табеле отдела совпадают с ведомостью и у карточной строки."""
        other = _worker(db_session, "Админ сети", "IT-2", "57000", it_dept,
                        companies["GHS"], schedule)
        _full_norm(db_session, engineer, companies["GHS"])
        _full_norm(db_session, other, companies["GHS"])
        _card(db_session, engineer, {companies["SEC"]: 100})
        _set_arm(db_session, it_dept, companies, ARM)

        r = client.get(
            f"/api/timesheet/2026/5?department_id={it_dept.id}&include_payroll=true",
            headers=_h(token),
        )
        assert r.status_code == 200, r.text
        rows = {row["employee_id"]: row for row in r.json()["quantity_distribution"]}
        assert set(rows) == {engineer.id, other.id}

        card = rows[engineer.id]["amounts"]
        assert Decimal(card[str(companies["SEC"].id)]) == Decimal("57000")
        assert sum(Decimal(v) for v in card.values()) == Decimal("57000")

        qty = rows[other.id]["amounts"]
        assert Decimal(qty[str(companies["ZMO"].id)]) == Decimal("25000")
        assert sum(Decimal(v) for v in qty.values()) == Decimal("57000")

        statement = _statement(client, token)
        assert _amount(_row(statement, engineer.id), companies["SEC"]) == Decimal("57000")
        assert _amount(_row(statement, other.id), companies["ZMO"]) == Decimal("25000")
