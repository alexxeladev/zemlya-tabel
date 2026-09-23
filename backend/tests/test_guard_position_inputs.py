"""Начисления основной системы на ОХРАННУЮ позицию не вводятся (аудит 2-Г).

Позиция охранного подразделения считается модулем вахты: часов в табеле у
охранника нет, премия, штраф и официальная выплата у вахты свои. Общий табель,
премии/KPI/аванс и заём для такой позиции раньше ПРИНИМАЛИСЬ, а расчёт их молча
игнорировал (а в месяц без назначения на пост — вдруг учитывал). Теперь ввод
отклоняется на бэке во всех точках входа, с понятной причиной.

Снятие — всегда разрешено: иначе уже введённое было бы нечем убрать.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.loan_deductions import LoanDeduction
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.position_terms import set_effective_from
from app.services.positions import create_position
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
    MONTH,
    YEAR,
    _auth,
    companies,
    guard_dept,
    other_dept,
    rodionov,
    users,
)

WORK_DATE = f"{YEAR}-{MONTH:02d}-05"
AUGUST = {"year": 2026, "months": [{"month": 8, "days": "1,2,8,9,15,16,22,23,29,30"}]}


@pytest.fixture
def admin(client, users):
    return _auth(client, "admin")


@pytest.fixture
def moonlighter(db_session, rodionov, other_dept, companies) -> Employee:
    """Охранник (основная позиция — охрана) с подработкой в ОБЫЧНОМ отделе."""
    create_position(rodionov, {
        "title": "Электрик", "pay_type": "salary", "rate": Decimal("30000"),
        "department_id": other_dept.id, "company_id": companies["ZMO"].id,
    })
    db_session.commit()
    db_session.refresh(rodionov)
    return rodionov


def _office_position(employee):
    return next(p for p in employee.positions if not p.is_primary)


def _cell(employee, company, hours, position=None, work_date=WORK_DATE) -> dict:
    return {"employee_id": employee.id, "work_date": work_date, "company_id": company.id,
            "hours": hours, "position_id": (position or employee.primary_position).id}


def _guard_hours(db_session, employee, company, hours=8) -> TimesheetEntry:
    """Наследство: часы на охранной позиции, введённые до запрета."""
    entry = TimesheetEntry(
        employee_id=employee.id, position_id=employee.primary_position.id,
        work_date=date.fromisoformat(WORK_DATE), company_id=company.id, hours=hours,
    )
    db_session.add(entry)
    db_session.commit()
    return entry


def _assert_refused(resp):
    assert resp.status_code == 403, resp.text
    assert "вахт" in resp.json()["detail"].lower(), "в отказе должно быть сказано, где это ведётся"


# ── Часы ──────────────────────────────────────────────────────────────────────

class TestHours:
    def test_cell_is_refused(self, client, admin, db_session, rodionov, companies):
        resp = client.put("/api/timesheet/cell", headers=admin,
                          json=_cell(rodionov, companies["ZMO"], 8))
        _assert_refused(resp)
        assert db_session.query(TimesheetEntry).count() == 0

    def test_cell_without_position_id_goes_to_primary_and_is_refused(
        self, client, admin, db_session, rodionov, companies,
    ):
        body = _cell(rodionov, companies["ZMO"], 8)
        del body["position_id"]
        _assert_refused(client.put("/api/timesheet/cell", headers=admin, json=body))

    def test_batch_is_refused_as_a_whole(self, client, admin, db_session, moonlighter, companies):
        """Массовая запись: одна охранная ячейка отклоняет весь пакет."""
        resp = client.post("/api/timesheet/cells/batch", headers=admin, json={"entries": [
            _cell(moonlighter, companies["ZMO"], 8, _office_position(moonlighter)),
            _cell(moonlighter, companies["ZMO"], 8),
        ]})
        _assert_refused(resp)
        assert db_session.query(TimesheetEntry).count() == 0

    def test_editing_legacy_hours_is_refused(self, client, admin, db_session, rodionov, companies):
        _guard_hours(db_session, rodionov, companies["ZMO"])
        resp = client.put("/api/timesheet/cell", headers=admin,
                          json=_cell(rodionov, companies["ZMO"], 12))
        _assert_refused(resp)
        assert db_session.query(TimesheetEntry).one().hours == 8

    def test_company_change_is_refused(self, client, admin, db_session, rodionov, companies):
        _guard_hours(db_session, rodionov, companies["ZMO"])
        resp = client.put("/api/timesheet/cell/company", headers=admin, json={
            "employee_id": rodionov.id, "position_id": rodionov.primary_position.id,
            "work_date": WORK_DATE, "old_company_id": companies["ZMO"].id,
            "new_company_id": companies["EKS"].id,
        })
        _assert_refused(resp)
        assert db_session.query(TimesheetEntry).one().company_id == companies["ZMO"].id

    def test_legacy_hours_can_be_removed(self, client, admin, db_session, rodionov, companies):
        """Снятие разрешено всегда — иначе введённое до запрета нечем убрать."""
        _guard_hours(db_session, rodionov, companies["ZMO"])
        resp = client.put("/api/timesheet/cell", headers=admin,
                          json=_cell(rodionov, companies["ZMO"], 0))
        assert resp.status_code == 200
        assert db_session.query(TimesheetEntry).count() == 0

    def test_autofill_skips_guard_position_with_a_reason(
        self, client, admin, db_session, moonlighter, companies,
    ):
        db_session.add(ProductionCalendar(year=2026, data=AUGUST, source="manual"))
        schedule = Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8)
        db_session.add(schedule)
        db_session.commit()
        for position in moonlighter.positions:
            # Условия версионируются (task_stage3_historicity): без даты график
            # действовал бы с 1-го числа следующего месяца, а тест про август.
            set_effective_from(position, date(YEAR, MONTH, 1))
            position.schedule_id = schedule.id
            position.company_id = companies["ZMO"].id
        db_session.commit()
        guard_position_id = moonlighter.primary_position.id

        preview = client.post("/api/timesheet/autofill/preview", headers=admin, json={
            "year": YEAR, "month": MONTH, "department_id": None}).json()
        applied = client.post("/api/timesheet/autofill/apply", headers=admin, json={
            "year": YEAR, "month": MONTH, "department_id": None})

        assert applied.status_code == 200, applied.text
        assert {c["position_id"] for c in preview["entries_to_create"]} == {
            _office_position(moonlighter).id}
        reasons = [s["reason"] for s in preview["employees_skipped"]
                   if s["employee_id"] == moonlighter.id]
        assert any("вахт" in r.lower() for r in reasons), reasons
        assert db_session.query(TimesheetEntry).filter_by(position_id=guard_position_id).count() == 0
        assert db_session.query(TimesheetEntry).count() == 21

    def test_office_position_of_the_same_person_still_works(
        self, client, admin, db_session, moonlighter, companies,
    ):
        resp = client.put("/api/timesheet/cell", headers=admin,
                          json=_cell(moonlighter, companies["ZMO"], 8, _office_position(moonlighter)))
        assert resp.status_code == 200, resp.text


# ── Премии, KPI, аванс ────────────────────────────────────────────────────────

def _adjustment(employee, kind="premium", position=None) -> dict:
    return {"employee_id": employee.id, "year": YEAR, "month": MONTH, "kind": kind,
            "amount": "5000", "reason": "за объект",
            "position_id": (position or employee.primary_position).id}


class TestAdjustments:
    @pytest.mark.parametrize("kind", ["premium", "kpi", "advance"])
    def test_refused_for_guard_position(self, client, admin, db_session, rodionov, kind):
        resp = client.post("/api/timesheet/adjustments", headers=admin,
                           json=_adjustment(rodionov, kind))
        _assert_refused(resp)
        assert db_session.query(EmployeeAdjustment).count() == 0

    def test_refused_without_position_id_when_primary_is_guard(self, client, admin, rodionov):
        body = _adjustment(rodionov)
        del body["position_id"]
        _assert_refused(client.post("/api/timesheet/adjustments", headers=admin, json=body))

    def test_manager_is_refused_too(self, client, users, rodionov):
        resp = client.post("/api/timesheet/adjustments", headers=_auth(client, "manager"),
                           json=_adjustment(rodionov))
        _assert_refused(resp)

    def test_legacy_adjustment_can_be_deleted(self, client, admin, db_session, rodionov):
        adj = EmployeeAdjustment(
            employee_id=rodionov.id, position_id=rodionov.primary_position.id,
            year=YEAR, month=MONTH, kind="premium", amount=Decimal("5000"), reason="до запрета",
        )
        db_session.add(adj)
        db_session.commit()
        resp = client.delete(f"/api/timesheet/adjustments/{adj.id}", headers=admin)
        assert resp.status_code == 204
        assert db_session.query(EmployeeAdjustment).count() == 0

    def test_office_position_of_the_same_person_still_works(self, client, admin, moonlighter):
        resp = client.post("/api/timesheet/adjustments", headers=admin,
                           json=_adjustment(moonlighter, position=_office_position(moonlighter)))
        assert resp.status_code == 201, resp.text


# ── Заём ──────────────────────────────────────────────────────────────────────

LOAN = {"loan_amount": "60000", "loan_term_months": 6, "loan_start_date": "2026-08-01"}


def _give_loan(db_session, employee):
    """Наследство: заём на охранной позиции, заведённый до запрета."""
    employee.loan_amount = Decimal("60000")
    employee.loan_term_months = 6
    employee.loan_start_date = date(2026, 8, 1)
    db_session.commit()


class TestLoan:
    def test_new_loan_in_the_card_is_refused(self, client, admin, db_session, rodionov):
        resp = client.patch(f"/api/employees/{rodionov.id}", headers=admin, json=LOAN)
        _assert_refused(resp)
        db_session.expire_all()
        assert db_session.get(Employee, rodionov.id).loan_amount is None

    def test_changing_legacy_loan_is_refused(self, client, admin, db_session, rodionov):
        _give_loan(db_session, rodionov)
        resp = client.patch(f"/api/employees/{rodionov.id}", headers=admin,
                            json={**LOAN, "loan_amount": "90000"})
        _assert_refused(resp)

    def test_card_saved_without_touching_the_loan_is_fine(self, client, admin, db_session, rodionov):
        """Форма шлёт поля целиком: присутствие прежних значений — не правка."""
        _give_loan(db_session, rodionov)
        resp = client.patch(f"/api/employees/{rodionov.id}", headers=admin,
                            json={**LOAN, "full_name": "Караулов Олег Петрович-Новый"})
        assert resp.status_code == 200, resp.text

    def test_legacy_loan_can_be_cleared(self, client, admin, db_session, rodionov):
        _give_loan(db_session, rodionov)
        resp = client.patch(f"/api/employees/{rodionov.id}", headers=admin, json={
            "loan_amount": None, "loan_term_months": None, "loan_start_date": None})
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        assert db_session.get(Employee, rodionov.id).loan_amount is None

    def test_loan_override_is_refused(self, client, admin, db_session, rodionov):
        _give_loan(db_session, rodionov)
        resp = client.post("/api/timesheet/loan-override", headers=admin, json={
            "employee_id": rodionov.id, "year": YEAR, "month": MONTH, "actual_amount": "1000"})
        _assert_refused(resp)
        assert db_session.query(LoanDeduction).count() == 0

    def test_legacy_loan_override_can_be_removed(self, client, admin, db_session, rodionov):
        _give_loan(db_session, rodionov)
        db_session.add(LoanDeduction(
            employee_id=rodionov.id, year=YEAR, month=MONTH,
            planned_amount=Decimal("10000"), actual_amount=Decimal("1000"),
        ))
        db_session.commit()
        resp = client.delete(f"/api/timesheet/loan-override/{rodionov.id}/{YEAR}/{MONTH}",
                             headers=admin)
        assert resp.status_code == 204
        assert db_session.query(LoanDeduction).count() == 0

    def test_office_employee_loan_still_works(self, client, admin, db_session, other_dept):
        emp = Employee(full_name="Офисный Сотрудник", tab_number="T-0500", is_active=True)
        db_session.add(emp)
        db_session.commit()
        emp.ensure_primary_position().department_id = other_dept.id
        db_session.commit()
        resp = client.patch(f"/api/employees/{emp.id}", headers=admin, json=LOAN)
        assert resp.status_code == 200, resp.text


class TestReviewFindings:
    """Правки по итогам ревью."""

    def test_partial_clear_of_legacy_loan_is_allowed(self, client, admin, db_session, rodionov):
        """Запрос шлёт только изменённое: один `loan_amount: null` — тоже снятие."""
        _give_loan(db_session, rodionov)
        resp = client.patch(f"/api/employees/{rodionov.id}", headers=admin, json={"loan_amount": None})
        assert resp.status_code == 200, resp.text

    def test_changing_only_the_term_of_a_legacy_loan_is_refused(
        self, client, admin, db_session, rodionov,
    ):
        _give_loan(db_session, rodionov)
        resp = client.patch(f"/api/employees/{rodionov.id}", headers=admin,
                            json={"loan_term_months": 12})
        _assert_refused(resp)

    def test_card_reports_the_loan_position(self, client, admin, db_session, moonlighter):
        """Экран блокирует заём по тому же месту, что и бэк."""
        moonlighter.loan_position_id = _office_position(moonlighter).id
        db_session.commit()
        card = client.get(f"/api/employees/{moonlighter.id}", headers=admin).json()
        assert card["loan_position_id"] == _office_position(moonlighter).id
        # Заём на ОБЫЧНОМ месте совместителя-охранника заводится.
        resp = client.patch(f"/api/employees/{moonlighter.id}", headers=admin, json=LOAN)
        assert resp.status_code == 200, resp.text
