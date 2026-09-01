"""Распределение ИТ по количеству АРМ, база «К выплате», округление до тысячи
(task_it_arm_distribution).

Три связанных изменения:
  ч.1 — механизм распределения по заявкам HR обобщён до ЛЮБОГО количественного
        показателя отдела: у ИТ это число АРМ (рабочих мест) по юрлицам;
  ч.2 — база распределения = «К выплате» (округлённая), а не «Итого начислено»:
        по юрлицам разносим ровно то, что платим;
  ч.3 — суммы по юрлицам округляются до 1000 ₽ методом floor + раздача
        недостающих тысяч по наибольшим хвостам, поэтому Σ долей ВСЕГДА ровно
        равна «К выплате».

Регрессия HR — в `test_hr_applications.py`, совместимость с целевыми премиями
проверяется и здесь, и в `test_funding_source.py`.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.company_shares import EmployeeCompanyShare
from app.models.department_quantities import DepartmentQuantity
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.distribution import distribute_largest_remainder
from app.services.quantity_distribution import quantity_percents, quantity_weights
from tests.conftest import get_token

MAY_BASIC = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]

# Реальные данные ИТ из задачи: 104 АРМ на 8 юрлиц.
ARM = {
    "ZMO": 45, "SD": 6, "EXP": 13, "KSRV": 7,
    "PROD": 5, "ESI": 9, "SEC": 12, "GHS": 7,
}
# Проверочный пример ТЗ: база «К выплате» 57 000 по этим же процентам.
EXPECTED_57K = {
    "ZMO": 25000, "SD": 3000, "EXP": 7000, "KSRV": 4000,
    "PROD": 3000, "ESI": 5000, "SEC": 6000, "GHS": 4000,
}


# ── Unit: округление до тысячи (ч.3) ──────────────────────────────────────────

class TestThousandRounding:
    def test_check_example_from_task(self):
        """Проверочный пример ТЗ: 57 000 по процентам АРМ → ровно таблица задачи.

        Точные доли (24663, 3288, 7125, 3837, 2740, 4933, 6577, 3837) floor-ятся
        до 52 000, недостающие 5 тысяч уходят четырём наибольшим хвостам
        (933 ЭкоСтройИнвест, 837 К-Сервис, 837 Грин Хаус, 740 Гермес) и пятому —
        663 ЗМО. Итог ровно 57 000.
        """
        ids = {code: i + 1 for i, code in enumerate(ARM)}
        amounts = distribute_largest_remainder(
            Decimal("57000"),
            {ids[code]: Decimal(n) for code, n in ARM.items()},
            order={ids[code]: i for i, code in enumerate(ARM)},
        )
        assert {code: amounts[ids[code]] for code in ARM} == {
            code: Decimal(v) for code, v in EXPECTED_57K.items()
        }
        assert sum(amounts.values()) == Decimal("57000")

    def test_independent_rounding_would_overshoot(self):
        """Почему нельзя округлять каждую сумму независимо: на этом же примере
        математическое округление даёт 58 000 — лишнюю тысячу."""
        naive = sum(
            (Decimal("57000") * n / 104 / 1000).quantize(Decimal("1")) * 1000
            for n in ARM.values()
        )
        assert naive == Decimal("58000")

    def test_sum_equals_base_on_many_amounts(self):
        """Σ долей = базе на любых суммах, а сами доли кратны тысяче."""
        weights = {i + 1: Decimal(n) for i, n in enumerate(ARM.values())}
        for base in ("1000", "57000", "123000", "1000000", "2000"):
            amounts = distribute_largest_remainder(Decimal(base), weights)
            assert sum(amounts.values()) == Decimal(base), base
            assert all(a % 1000 == 0 for a in amounts.values()), base

    def test_equal_remainders_are_deterministic(self):
        """Равные хвосты (в примере два по 837) разводятся порядком юрлиц —
        результат не «плавает» между пересчётами."""
        weights = {1: Decimal(1), 2: Decimal(1), 3: Decimal(1)}
        order = {1: 0, 2: 1, 3: 2}
        first = distribute_largest_remainder(Decimal("10000"), weights, order=order)
        for _ in range(5):
            assert distribute_largest_remainder(
                Decimal("10000"), weights, order=order
            ) == first
        # 3333.33 у всех троих → floor 3000 ×3, недостающая тысяча — первому
        # по настроенному порядку юрлиц.
        assert first == {1: Decimal("4000"), 2: Decimal("3000"), 3: Decimal("3000")}

    def test_order_beats_id_on_ties(self):
        """Тай-брейк — настроенный порядок юрлиц, а не их id."""
        weights = {1: Decimal(1), 2: Decimal(1)}
        assert distribute_largest_remainder(
            Decimal("1000"), weights, order={1: 5, 2: 0}
        ) == {1: Decimal("0"), 2: Decimal("1000")}

    def test_negative_base_is_not_rounded_to_thousand(self):
        """Отрицательная «к выплате» (долг сотрудника) в тысячи не округляется:
        оба направления одинаково неверны. Σ всё равно равна базе."""
        amounts = distribute_largest_remainder(
            Decimal("-350"), {1: Decimal(1), 2: Decimal(1)}
        )
        assert sum(amounts.values()) == Decimal("-350")

    def test_zero_base_keeps_companies_with_zero(self):
        """Нулевая база не выкидывает юрлица из разбивки — они остаются с 0."""
        amounts = distribute_largest_remainder(_ZERO := Decimal("0"), {1: Decimal(1), 2: Decimal(2)})
        assert amounts == {1: Decimal("0"), 2: Decimal("0")}


# ── Unit: проценты по АРМ (ч.1) ───────────────────────────────────────────────

class TestArmPercents:
    def test_percents_from_task(self):
        """45 из 104 → 43.27%, весь столбец процентов совпадает с таблицей ТЗ."""
        ids = {code: i + 1 for i, code in enumerate(ARM)}
        percents = quantity_percents({ids[c]: n for c, n in ARM.items()})
        assert percents[ids["ZMO"]] == Decimal("43.27")
        assert percents[ids["SD"]] == Decimal("5.77")
        assert percents[ids["EXP"]] == Decimal("12.50")
        assert percents[ids["KSRV"]] == Decimal("6.73")
        assert percents[ids["PROD"]] == Decimal("4.81")
        assert percents[ids["ESI"]] == Decimal("8.65")
        assert percents[ids["SEC"]] == Decimal("11.54")
        assert percents[ids["GHS"]] == Decimal("6.73")

    def test_percents_sum_to_exactly_100(self):
        ids = {code: i + 1 for i, code in enumerate(ARM)}
        percents = quantity_percents({ids[c]: n for c, n in ARM.items()})
        assert sum(percents.values()) == Decimal("100.00")

    def test_weights_are_counts_not_percents(self):
        """Веса — сами количества: доля считается от 45/104, а не от 43.27%."""
        assert quantity_weights({1: 45, 2: 0, 3: 6}) == {1: Decimal(45), 3: Decimal(6)}


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
    """Отдел ИТ: тот же флаг, что у HR, но показатель называется «АРМ» и частей
    не имеет — вводится одним числом."""
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
    emp = Employee(full_name="Админ", email="itadmin@example.com",
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
def engineer(db_session, it_dept, companies, schedule) -> Employee:
    """Оклад 57 000, полностью отработанная норма, удержаний нет → «К выплате»
    ровно 57 000 — база проверочного примера ТЗ."""
    return _worker(db_session, "Инженер", "IT-1", "57000", it_dept,
                   companies["GHS"], schedule)


def _full_norm(db: Session, emp: Employee, company: Company):
    for d in MAY_WORKDAYS:
        db.add(TimesheetEntry(employee_id=emp.id, position_id=emp.primary_position.id,
                              work_date=date(2026, 5, d), company_id=company.id, hours=8))
    db.commit()


def _set_arm(db: Session, dept: Department, companies, counts: dict[str, int],
             year: int = 2026, month: int = 5):
    """АРМ вводятся ОДНИМ числом: у показателя без разбивки вторая часть пуста."""
    for code, n in counts.items():
        db.add(DepartmentQuantity(department_id=dept.id, company_id=companies[code].id,
                                  year=year, month=month, part1=n, part2=0))
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


def _amounts(row: dict) -> dict[int, Decimal]:
    return {d["company_id"]: Decimal(d["amount"]) for d in row["distribution"]}


# ── Ведомость ИТ: проверочный пример целиком ──────────────────────────────────

class TestItStatement:
    def test_check_example_end_to_end(
        self, client, db_session, admin, calendar, engineer, it_dept, companies
    ):
        """Оклад 57 000 → «К выплате» 57 000 → суммы ровно из таблицы ТЗ."""
        _full_norm(db_session, engineer, companies["GHS"])
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "quantity"
        assert Decimal(row["net_payout"]) == Decimal("57000")
        amounts = _amounts(row)
        assert {code: amounts[companies[code].id] for code in ARM} == {
            code: Decimal(v) for code, v in EXPECTED_57K.items()
        }
        assert Decimal(row["distribution_total"]) == Decimal("57000")

    def test_percent_column_matches_task(
        self, client, db_session, admin, calendar, engineer, it_dept, companies
    ):
        """45/104 = 43.27% — процент показывается рядом с суммой."""
        _full_norm(db_session, engineer, companies["GHS"])
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), engineer.id)
        zmo = next(d for d in row["distribution"] if d["company_id"] == companies["ZMO"].id)
        assert Decimal(zmo["percent"]) == Decimal("43.27")
        assert Decimal(row["percent_sum"]) == Decimal("100.00")

    def test_arm_replaces_cascade(
        self, client, db_session, admin, calendar, engineer, it_dept, companies
    ):
        """Проценты в карточке заданы, но АРМ их заменяют целиком."""
        _full_norm(db_session, engineer, companies["GHS"])
        db_session.add(EmployeeCompanyShare(
            employee_id=engineer.id, position_id=engineer.primary_position.id,
            company_id=companies["SEC"].id, percent=Decimal("100")))
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] == "quantity"
        assert _amounts(row)[companies["SEC"].id] == Decimal("6000")

    def test_flag_without_counts_falls_back_with_metric_name(
        self, client, db_session, admin, calendar, engineer, it_dept, companies
    ):
        """АРМ за месяц не заведены → обычный каскад и предупреждение, в котором
        показатель назван своим именем («АРМ», а не «Заявки»)."""
        _full_norm(db_session, engineer, companies["GHS"])
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), engineer.id)
        assert row["distribution_source"] != "quantity"
        assert "АРМ" in row["distribution_note"]


# ── База распределения = «К выплате» (ч.2) ────────────────────────────────────

class TestDistributionBaseIsNetPayout:
    def test_sum_equals_net_payout_not_accrued(
        self, client, db_session, admin, calendar, it_dept, companies, schedule
    ):
        """С удержанием аванса «Итого начислено» и «К выплате» расходятся —
        распределяется именно «К выплате» (это и была жалоба: 152 000 против
        152 381)."""
        emp = _worker(db_session, "Инженер", "IT-2", "100000", it_dept,
                      companies["GHS"], schedule)
        _full_norm(db_session, emp, companies["GHS"])
        db_session.add(EmployeeAdjustment(
            employee_id=emp.id, position_id=emp.primary_position.id,
            year=2026, month=5, kind="advance", amount=Decimal("30000"),
            reason="аванс"))
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), emp.id)
        assert Decimal(row["accrued_total"]) == Decimal("100000")
        assert Decimal(row["net_payout"]) == Decimal("70000")
        assert sum(_amounts(row).values()) == Decimal("70000")

    def test_rounding_tail_is_not_distributed(
        self, client, db_session, admin, calendar, it_dept, companies, schedule
    ):
        """Хвост округления «к выплате» в затраты юрлиц НЕ разносится: он
        остаётся показателем «Эффект округления» на дашборде."""
        emp = _worker(db_session, "Инженер", "IT-3", "100000", it_dept,
                      companies["GHS"], schedule)
        _full_norm(db_session, emp, companies["GHS"])
        db_session.add(EmployeeAdjustment(
            employee_id=emp.id, position_id=emp.primary_position.id,
            year=2026, month=5, kind="advance", amount=Decimal("30400"),
            reason="аванс"))
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), emp.id)
        assert Decimal(row["net_payout_exact"]) == Decimal("69600")
        assert Decimal(row["net_payout"]) == Decimal("70000")
        assert Decimal(row["rounding_tail"]) == Decimal("-400")
        # Разносится округлённая выплата, а не точная и не начисленное.
        assert sum(_amounts(row).values()) == Decimal("70000")

    def test_cascade_department_also_rounded_and_matches_payout(
        self, client, db_session, admin, calendar, plain_dept, companies, schedule
    ):
        """База и округление одни на все ветки: обычный отдел (каскад по часам)
        тоже разносит «К выплате» круглыми тысячами."""
        emp = _worker(db_session, "Бухгалтер", "ACC-1", "77000", plain_dept,
                      companies["ZMO"], schedule)
        for i, d in enumerate(MAY_WORKDAYS):
            company = companies["ZMO"] if i % 2 else companies["SD"]
            db_session.add(TimesheetEntry(
                employee_id=emp.id, position_id=emp.primary_position.id,
                work_date=date(2026, 5, d), company_id=company.id, hours=8))
        db_session.commit()
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), emp.id)
        assert row["distribution_source"] == "hours"
        amounts = _amounts(row)
        assert sum(amounts.values()) == Decimal(row["net_payout"])
        assert all(a % 1000 == 0 for a in amounts.values())

    def test_statement_totals_match_sum_of_net_payouts(
        self, client, db_session, admin, calendar, engineer, it_dept, companies, schedule
    ):
        """Итоговая строка: Σ по юрлицам сходится с Σ «К выплате» всех строк."""
        _full_norm(db_session, engineer, companies["GHS"])
        other = _worker(db_session, "Инженер 2", "IT-4", "83000", it_dept,
                        companies["ZMO"], schedule)
        _full_norm(db_session, other, companies["ZMO"])
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        statement = _statement(client, token)
        totals = {int(k): Decimal(v) for k, v in statement["distribution_totals"].items()}
        assert sum(totals.values()) == Decimal(statement["total_net_payout"])
        for row in statement["rows"]:
            assert sum(_amounts(row).values()) == Decimal(row["net_payout"])


# ── Совместимость с целевыми премиями (task_funding_source) ───────────────────

class TestTargetedFundingTogether:
    def test_targeted_premium_participates_in_rounding(
        self, client, db_session, admin, calendar, it_dept, companies, schedule
    ):
        """Целевая премия ложится на свою компанию, но округляется ВМЕСТЕ с
        долями каскада: Σ по-прежнему ровно равна «К выплате»."""
        emp = _worker(db_session, "Инженер", "IT-5", "57000", it_dept,
                      companies["GHS"], schedule)
        _full_norm(db_session, emp, companies["GHS"])
        db_session.add(EmployeeAdjustment(
            employee_id=emp.id, position_id=emp.primary_position.id,
            year=2026, month=5, kind="premium", amount=Decimal("13000"),
            reason="за проект", funding_company_id=companies["SEC"].id))
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), emp.id)
        net = Decimal(row["net_payout"])
        assert net == Decimal("70000")
        amounts = _amounts(row)
        assert sum(amounts.values()) == net
        assert all(a % 1000 == 0 for a in amounts.values())
        # Целевая утяжелила компанию-источник: её доля больше «своих» 11.54%.
        assert amounts[companies["SEC"].id] > net * Decimal("0.1154")
        assert row["targeted_note"] and "13000" in row["targeted_note"]

    def test_targeted_beyond_net_payout_does_not_break(
        self, client, db_session, admin, calendar, it_dept, companies, schedule
    ):
        """Целевые больше «К выплате» (съедено удержаниями) — не падаем,
        разносим только их, пропорционально выплате, с предупреждением."""
        emp = _worker(db_session, "Инженер", "IT-6", "50000", it_dept,
                      companies["GHS"], schedule)
        _full_norm(db_session, emp, companies["GHS"])
        db_session.add_all([
            EmployeeAdjustment(
                employee_id=emp.id, position_id=emp.primary_position.id,
                year=2026, month=5, kind="premium", amount=Decimal("20000"),
                reason="целевая", funding_company_id=companies["SEC"].id),
            EmployeeAdjustment(
                employee_id=emp.id, position_id=emp.primary_position.id,
                year=2026, month=5, kind="advance", amount=Decimal("60000"),
                reason="аванс"),
        ])
        db_session.commit()
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        row = _row(_statement(client, token), emp.id)
        assert Decimal(row["net_payout"]) == Decimal("10000")
        assert sum(_amounts(row).values()) == Decimal("10000")
        assert "целевые начисления превышают" in row["distribution_note"]


# ── API количественного показателя ────────────────────────────────────────────

class TestQuantityApi:
    def test_set_arm_and_read_percents(self, client, db_session, admin, it_dept, companies):
        """Ввод АРМ одним числом и вычисленные проценты (45/104 = 43.27%)."""
        token = get_token(client, "itadmin@example.com", "admin123")
        r = client.put("/api/timesheet/quantities", headers=_h(token), json={
            "department_id": it_dept.id, "year": 2026, "month": 5,
            "items": [{"company_id": companies[c].id, "part1": n, "part2": 0}
                      for c, n in ARM.items()]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_count"] == 104
        assert body["metric_name"] == "АРМ"
        assert body["has_parts"] is False
        zmo = next(i for i in body["items"] if i["company_id"] == companies["ZMO"].id)
        assert zmo["count"] == 45
        assert Decimal(zmo["percent"]) == Decimal("43.27")

    def test_metric_name_and_parts_are_per_department(
        self, client, db_session, admin, it_dept, companies
    ):
        """У HR тот же механизм, но со своим именем показателя и двумя частями."""
        hr = Department(name="HR", code="HR", is_active=True,
                        uses_quantity_distribution=True, quantity_metric_name="Заявки",
                        quantity_part1_name="В работе", quantity_part2_name="Закрытые")
        db_session.add(hr)
        db_session.commit()
        token = get_token(client, "itadmin@example.com", "admin123")
        r = client.get("/api/timesheet/2026/5/quantities", headers=_h(token))
        assert r.status_code == 200
        by_dept = {d["department_id"]: d for d in r.json()}
        assert by_dept[it_dept.id]["metric_name"] == "АРМ"
        assert by_dept[it_dept.id]["has_parts"] is False
        assert by_dept[hr.id]["metric_name"] == "Заявки"
        assert by_dept[hr.id]["has_parts"] is True
        assert by_dept[hr.id]["part1_name"] == "В работе"

    def test_metric_name_editable_by_admin(self, client, db_session, admin, plain_dept):
        token = get_token(client, "itadmin@example.com", "admin123")
        r = client.patch(f"/api/departments/{plain_dept.id}", headers=_h(token), json={
            "uses_quantity_distribution": True, "quantity_metric_name": "АРМ"})
        assert r.status_code == 200, r.text
        assert r.json()["uses_quantity_distribution"] is True
        assert r.json()["quantity_metric_name"] == "АРМ"
        # Правка соседнего поля показатель не сбрасывает.
        r = client.patch(f"/api/departments/{plain_dept.id}",
                         json={"name": "ИТ-отдел"}, headers=_h(token))
        assert r.json()["quantity_metric_name"] == "АРМ"

    def test_default_metric_label_when_not_set(self, db_session):
        """Флаг включили, имя не задали — показатель называется нейтрально."""
        d = Department(name="Новый", code="NEWQ", is_active=True,
                       uses_quantity_distribution=True)
        db_session.add(d)
        db_session.commit()
        assert d.quantity_metric_label == "Количество"
        assert d.quantity_has_parts is False

    def test_distribution_block_in_timesheet_matches_statement(
        self, client, db_session, admin, calendar, engineer, it_dept, companies
    ):
        """Блок распределения в табеле ИТ показывает те же суммы, что ведомость."""
        _full_norm(db_session, engineer, companies["GHS"])
        _set_arm(db_session, it_dept, companies, ARM)
        token = get_token(client, "itadmin@example.com", "admin123")

        r = client.get(
            f"/api/timesheet/2026/5?department_id={it_dept.id}&include_payroll=true",
            headers=_h(token))
        assert r.status_code == 200, r.text
        rows = r.json()["quantity_distribution"]
        assert len(rows) == 1
        assert Decimal(rows[0]["base_amount"]) == Decimal("57000")
        block = {int(k): Decimal(v) for k, v in rows[0]["amounts"].items()}
        assert block == _amounts(_row(_statement(client, token), engineer.id))
        assert sum(block.values()) == Decimal("57000")
