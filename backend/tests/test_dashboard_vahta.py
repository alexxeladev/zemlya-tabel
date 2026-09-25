"""Дашборд × вахта (task_stage1 п.1.2).

Вахта была врезана в ведомость (`build_payroll_summary`), а дашборд считал месяц
собственным циклом, который про неё не знал: охранные места уходили в общий
расчёт → «нет графика» → 0 ₽. За август на деве терялось 4,88 млн из 13,57 млн.

Тесты стоят на СТЫКЕ модулей: в одной базе и обычный отдел, и вахта.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.employees import Employee
from app.models.position_terms import TERMS_BEGINNING
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.guard_duty import create_assignment
from app.services.position_terms import set_effective_from
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
    FIRST_HALF,
    MONTH,
    YEAR,
    _auth,
    _employee,
    companies,
    crew,
    gbr_place,
    guard_dept,
    guard_post,
    other_dept,
    rodionov,
    site,
    users,
    zone,
)

# Август 2026: выходные — субботы и воскресенья, праздников нет.
AUGUST = {"year": 2026, "months": [{"month": 8, "days": "1,2,8,9,15,16,22,23,29,30"}]}
URL = f"/api/dashboard/{YEAR}/{MONTH}"


@pytest.fixture
def office_worker(db_session, other_dept, companies) -> Employee:
    """Окладник обычного отдела с часами — чтобы в месяце была НЕ только вахта."""
    db_session.add(ProductionCalendar(year=2026, data=AUGUST, source="manual"))
    schedule = Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8)
    db_session.add(schedule)
    emp = Employee(full_name="Офисный Сотрудник", tab_number="T-0200", is_active=True)
    db_session.add(emp)
    db_session.commit()
    position = emp.ensure_primary_position()
    set_effective_from(position, TERMS_BEGINNING)  # версии условий: «с начала»
    position.department_id = other_dept.id
    position.pay_type = "salary"
    position.rate = Decimal("63000")
    position.schedule_id = schedule.id
    position.company_id = companies["ZMO"].id
    for day in (3, 4, 5):
        db_session.add(TimesheetEntry(
            employee_id=emp.id, position_id=position.id,
            work_date=date(YEAR, MONTH, day), company_id=companies["ZMO"].id, hours=8,
        ))
    db_session.commit()
    return emp


@pytest.fixture
def guard_month(db_session, gbr_place, guard_post, rodionov, guard_dept):
    """Две строки вахты: ГБР в экипаже (35/60/5) и охранник на посту (100 % SEC)."""
    create_assignment(
        db_session, year=YEAR, month=MONTH, place=gbr_place,
        position=rodionov.primary_position, days=FIRST_HALF,
    )
    guard = _employee(db_session, "Дозоров Иван Ильич", guard_dept, "0000-90275")
    create_assignment(
        db_session, year=YEAR, month=MONTH, place=guard_post,
        position=guard.primary_position, days=set(range(1, 11)),
    )
    db_session.commit()


def _dashboard(client, role="admin", url=URL) -> dict:
    resp = client.get(url, headers=_auth(client, role))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _payroll(client, role="admin") -> dict:
    resp = client.get(f"/api/timesheet/{YEAR}/{MONTH}/payroll", headers=_auth(client, role))
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestPayrollIncludesVahta:
    def test_total_matches_payroll_endpoint(self, client, users, office_worker, guard_month):
        """Главное: ФОТ дашборда = grand_total расчёта, вахта внутри."""
        dash, payroll = _dashboard(client), _payroll(client)
        guard_total = sum(
            Decimal(e["total_amount"]) for e in payroll["employees"] if e["is_guard_row"]
        )
        # 15 смен × 5 000 + 10 смен × 4 000
        assert guard_total == Decimal("115000")
        assert Decimal(dash["payroll"]["total"]) == Decimal(payroll["grand_total"])
        assert Decimal(dash["payroll"]["total"]) > guard_total  # и офис тоже внутри

    def test_guard_department_row_carries_its_payroll(
        self, client, users, office_worker, guard_month, guard_dept,
    ):
        dash = _dashboard(client)
        by_dept = {d["department_id"]: Decimal(d["total"]) for d in dash["payroll_by_department"]}
        assert by_dept[guard_dept.id] == Decimal("115000")
        assert sum(by_dept.values()) == Decimal(dash["payroll"]["total"])

    def test_guard_rows_are_not_counted_as_non_calculable(
        self, client, users, office_worker, guard_month,
    ):
        """Раньше охранник шёл общим расчётом → «нет графика» → «не вошёл в ФОТ»."""
        payroll = _payroll(client)
        not_calculable = [e for e in payroll["employees"] if not e["is_calculable"]]
        # Не считаются только учётки фикстуры `users` (у них нет ни графика, ни оклада).
        assert not any(e["is_guard_row"] for e in not_calculable)
        assert _dashboard(client)["payroll"]["non_calculable_employees"] == len(not_calculable)

    def test_trend_point_includes_vahta(self, client, users, office_worker, guard_month):
        dash, payroll = _dashboard(client), _payroll(client)
        point = [p for p in dash["trend"] if (p["year"], p["month"]) == (YEAR, MONTH)][0]
        assert Decimal(point["payroll_total"]) == Decimal(payroll["grand_total"])

    def test_range_sums_vahta_of_every_month(self, client, users, guard_month):
        dash = _dashboard(client, url=f"/api/dashboard/{YEAR}/7?to_year={YEAR}&to_month={MONTH}")
        assert Decimal(dash["payroll"]["total"]) == Decimal("115000")

    def test_manager_of_guard_department_sees_its_payroll(self, client, users, guard_month):
        assert Decimal(_dashboard(client, "manager")["payroll"]["total"]) == Decimal("115000")

    def test_timekeeper_still_gets_no_money(self, client, users, guard_month):
        dash = _dashboard(client, "timekeeper")
        assert dash["payroll"] is None
        assert dash["payroll_by_department"] == [] and dash["payroll_by_company"] == []
        assert all(p["payroll_total"] is None for p in dash["trend"])


class TestByCompany:
    """Решение заказчика: вахта в разрезе юрлиц — по процентам МЕСТА РАБОТЫ."""

    def test_guard_salary_is_split_by_place_shares(self, client, users, guard_month, companies):
        dash = _dashboard(client)
        by_company = {c["company_id"]: Decimal(c["total"]) for c in dash["payroll_by_company"]}
        # экипаж 75 000 по 35/60/5 + пост 40 000 на 100 % SEC, без округления
        assert by_company == {
            companies["ZMO"].id: Decimal("26250.00"),
            companies["EKS"].id: Decimal("45000.00"),
            companies["SEC"].id: Decimal("3750.00") + Decimal("40000.00"),
        }

    def test_split_adds_up_to_guard_payroll(self, client, users, guard_month):
        dash = _dashboard(client)
        assert sum(Decimal(c["total"]) for c in dash["payroll_by_company"]) == Decimal("115000")


class TestHoursBlockIsWithoutVahta:
    """Решение заказчика: круглосуточные часы охраны в блок «Часы» НЕ входят."""

    def test_guard_hours_are_not_added(self, client, users, office_worker, guard_month):
        dash = _dashboard(client)
        assert Decimal(dash["hours"]["total_hours"]) == Decimal("24")  # только офис: 3 × 8
        assert Decimal(dash["hours"]["norm_hours"]) == Decimal("168")  # 21 рабочий день × 8

    def test_guard_department_has_zero_hours(self, client, users, guard_month, guard_dept):
        dash = _dashboard(client)
        rows = [d for d in dash["hours_by_department"] if d["department_id"] == guard_dept.id]
        assert all(Decimal(r["total_hours"]) == 0 and r["norm_hours"] is None for r in rows)
