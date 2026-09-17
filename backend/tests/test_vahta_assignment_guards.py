"""Постановка на пост: только охранные позиции и только в незакрытом периоде
(task_stage1 п.1.4).

Было: `POST /vahta/assignments` с `position_id` ставил на пост ЛЮБОЕ рабочее
место, после чего его строка в ведомости считалась вахтой, а обычная зарплата
исчезала (окладник 86 087 → 4 185 000 «за 31 смену»). Работало и в закрытом
месяце — статуса периода вахта не спрашивала вовсе.
"""
from decimal import Decimal

import pytest

from app.models.employees import Employee
from app.models.guard_assignments import GuardAssignment
from app.models.timesheet_periods import TimesheetPeriod
from app.services.guard_duty import (
    GuardError,
    copy_previous_period,
    create_assignment,
    replace_on_post,
)
from app.services.payroll_statement import build_payroll_statement
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
    other_dept,
    rodionov,
    users,
    zone,
)


@pytest.fixture
def office_worker(db_session, other_dept, companies) -> Employee:
    """Окладник ОБЫЧНОГО отдела — его на пост ставить нельзя."""
    emp = Employee(full_name="Офисный Сотрудник", tab_number="T-0400", is_active=True)
    db_session.add(emp)
    db_session.commit()
    position = emp.ensure_primary_position()
    position.department_id = other_dept.id
    position.pay_type = "salary"
    position.rate = Decimal("86087")
    position.company_id = companies["ZMO"].id
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def second_guard(db_session, guard_dept) -> Employee:
    return _employee(db_session, "Дозоров Иван Ильич", guard_dept, "0000-90275")


def _close(db_session, dept, year=YEAR, month=MONTH, status="closed") -> TimesheetPeriod:
    period = TimesheetPeriod(department_id=dept.id, year=year, month=month, status=status)
    db_session.add(period)
    db_session.commit()
    return period


def _assign(db_session, place, employee, days=FIRST_HALF) -> GuardAssignment:
    assignment = create_assignment(
        db_session, year=YEAR, month=MONTH, place=place,
        position=employee.primary_position, days=days,
    )
    db_session.commit()
    return assignment


def _post(client, role, **body):
    body = {"year": YEAR, "month": MONTH, **body}
    return client.post("/api/vahta/assignments", json=body, headers=_auth(client, role))


# ── Только охранные позиции ───────────────────────────────────────────────────

class TestOnlyGuardPositions:
    def test_office_position_is_rejected(self, client, users, db_session, gbr_place, office_worker):
        """Приёмка: позиция обычного отдела на пост не встаёт."""
        resp = _post(
            client, "admin", crew_id=gbr_place.id,
            position_id=office_worker.primary_position.id,
        )

        assert resp.status_code == 422
        assert "охранн" in resp.json()["detail"].lower()
        assert db_session.query(GuardAssignment).count() == 0

    def test_office_salary_stays_in_the_statement(
        self, client, users, db_session, gbr_place, office_worker,
    ):
        """Суть дыры: после попытки строка НЕ стала вахтовой."""
        _post(client, "manager", crew_id=gbr_place.id,
              position_id=office_worker.primary_position.id)

        row = build_payroll_statement(db_session, [office_worker], [], YEAR, MONTH).rows[0]
        assert row.distribution_source != "guard_post"

    def test_guard_position_is_accepted(self, client, users, gbr_place, rodionov):
        resp = _post(client, "admin", crew_id=gbr_place.id,
                     position_id=rodionov.primary_position.id)
        assert resp.status_code == 201, resp.text

    def test_empty_slot_is_accepted(self, client, users, gbr_place):
        assert _post(client, "admin", crew_id=gbr_place.id).status_code == 201

    def test_employee_id_path_still_makes_a_guard_position(
        self, client, users, db_session, gbr_place, office_worker,
    ):
        """Постановка ЧЕЛОВЕКА (не позиции) по-прежнему заводит ему охранное
        рабочее место — совместительство в охране; его обычная позиция не тронута."""
        resp = _post(client, "admin", crew_id=gbr_place.id, employee_id=office_worker.id)

        assert resp.status_code == 201, resp.text
        assignment = db_session.query(GuardAssignment).one()
        assert assignment.position_id != office_worker.primary_position.id
        assert assignment.position.department_id == gbr_place.department_id

    def test_replacement_by_office_position_is_rejected(
        self, client, users, db_session, gbr_place, rodionov, office_worker,
    ):
        assignment = _assign(db_session, gbr_place, rodionov)

        resp = client.post(
            "/api/vahta/replace",
            json={"assignment_id": assignment.id, "from_day": 5,
                  "position_id": office_worker.primary_position.id},
            headers=_auth(client, "admin"),
        )

        assert resp.status_code == 422
        assert db_session.query(GuardAssignment).count() == 1

    def test_service_refuses_too(self, db_session, gbr_place, rodionov, office_worker):
        """Проверка стоит в СЕРВИСЕ: новый эндпойнт её не обойдёт."""
        with pytest.raises(GuardError):
            create_assignment(
                db_session, year=YEAR, month=MONTH, place=gbr_place,
                position=office_worker.primary_position,
            )
        db_session.rollback()
        assignment = _assign(db_session, gbr_place, rodionov)
        with pytest.raises(GuardError):  # замена с первого числа меняет человека в строке
            replace_on_post(
                db_session, assignment, position=office_worker.primary_position, from_day=1,
            )

    def test_copy_does_not_carry_a_position_that_left_the_guard_department(
        self, db_session, gbr_place, guard_dept, other_dept, rodionov,
    ):
        """Человека перевели из охраны — копирование состава оставляет пустой слот."""
        _assign(db_session, gbr_place, rodionov)
        rodionov.primary_position.department_id = other_dept.id
        db_session.commit()

        copied = copy_previous_period(db_session, YEAR, MONTH + 1, [guard_dept.id])
        db_session.commit()

        new = db_session.query(GuardAssignment).filter_by(month=MONTH + 1).one()
        assert copied == 1
        assert new.position_id is None


# ── Закрытый период ───────────────────────────────────────────────────────────

class TestClosedPeriod:
    @pytest.fixture
    def assignment(self, db_session, gbr_place, rodionov) -> GuardAssignment:
        return _assign(db_session, gbr_place, rodionov)

    def test_new_assignment_is_rejected(self, client, users, db_session, gbr_place, guard_dept, rodionov):
        """Приёмка: назначение в закрытом месяце отклоняется."""
        _close(db_session, guard_dept)

        resp = _post(client, "admin", crew_id=gbr_place.id,
                     position_id=rodionov.primary_position.id)

        assert resp.status_code == 409
        assert db_session.query(GuardAssignment).count() == 0

    @pytest.mark.parametrize("call", [
        "patch", "delete", "day_on", "day_off", "days", "replace",
    ])
    def test_every_change_of_an_assignment_is_rejected(
        self, client, users, db_session, guard_dept, assignment, second_guard, call,
    ):
        _close(db_session, guard_dept)
        headers = _auth(client, "admin")
        aid = assignment.id
        resp = {
            "patch": lambda: client.patch(f"/api/vahta/assignments/{aid}", json={"rate": "9000"}, headers=headers),
            "delete": lambda: client.delete(f"/api/vahta/assignments/{aid}", headers=headers),
            "day_on": lambda: client.put("/api/vahta/day", json={"assignment_id": aid, "day": 20, "value": True}, headers=headers),
            "day_off": lambda: client.put("/api/vahta/day", json={"assignment_id": aid, "day": 3, "value": False}, headers=headers),
            "days": lambda: client.put("/api/vahta/days", json={"assignment_id": aid, "days": []}, headers=headers),
            "replace": lambda: client.post("/api/vahta/replace", json={
                "assignment_id": aid, "from_day": 5,
                "position_id": second_guard.primary_position.id}, headers=headers),
        }[call]()

        assert resp.status_code == 409, resp.text
        db_session.expire_all()
        kept = db_session.query(GuardAssignment).one()
        assert {s.work_date.day for s in kept.shifts} == FIRST_HALF
        assert kept.rate == Decimal("5000")

    def test_copy_into_closed_month_is_rejected(
        self, client, users, db_session, guard_dept, assignment,
    ):
        _close(db_session, guard_dept, month=MONTH + 1)

        resp = client.post(f"/api/vahta/{YEAR}/{MONTH + 1}/copy-previous",
                           headers=_auth(client, "admin"))

        assert resp.status_code == 409
        assert db_session.query(GuardAssignment).filter_by(month=MONTH + 1).count() == 0

    def test_quick_hire_with_assignment_is_rejected(
        self, client, users, db_session, guard_dept, gbr_place,
    ):
        _close(db_session, guard_dept)
        before = db_session.query(Employee).count()

        resp = client.post("/api/vahta/quick-hire", json={
            "full_name": "Новый Охранник Тестович", "crew_id": gbr_place.id,
            "assign": True, "year": YEAR, "month": MONTH,
        }, headers=_auth(client, "admin"))

        assert resp.status_code == 409
        assert db_session.query(Employee).count() == before

    def test_closed_statement_row_is_unchanged(
        self, client, users, db_session, guard_dept, assignment, rodionov,
    ):
        _close(db_session, guard_dept)
        before = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0].accrued_total

        client.put("/api/vahta/days", json={"assignment_id": assignment.id, "days": []},
                   headers=_auth(client, "manager"))

        db_session.expire_all()
        after = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0].accrued_total
        assert before == after == Decimal("75000.00")

    @pytest.mark.parametrize("status", ["draft", "pending_review"])
    def test_not_closed_month_is_editable(
        self, client, users, db_session, guard_dept, assignment, status,
    ):
        """Блокирует только `closed`: так сформулирована задача («в закрытом периоде»)."""
        _close(db_session, guard_dept, status=status)

        resp = client.put("/api/vahta/day", json={
            "assignment_id": assignment.id, "day": 20, "value": True,
        }, headers=_auth(client, "admin"))

        assert resp.status_code == 200, resp.text

    def test_month_without_a_period_is_editable(self, client, users, assignment):
        resp = client.put("/api/vahta/day", json={
            "assignment_id": assignment.id, "day": 20, "value": True,
        }, headers=_auth(client, "admin"))
        assert resp.status_code == 200

    def test_other_closed_month_does_not_block(
        self, client, users, db_session, guard_dept, assignment,
    ):
        _close(db_session, guard_dept, month=MONTH - 1)
        resp = client.put("/api/vahta/day", json={
            "assignment_id": assignment.id, "day": 20, "value": True,
        }, headers=_auth(client, "admin"))
        assert resp.status_code == 200


class TestScreenKnowsAboutClosedMonth:
    """Кнопка, которая ответит 409, не должна выглядеть рабочей."""

    def test_closed_month_is_read_only(self, client, users, db_session, guard_dept, gbr_place):
        _close(db_session, guard_dept)
        data = client.get(f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "admin")).json()
        assert data["period_closed"] is True
        assert data["can_edit"] is False

    def test_open_month_is_editable(self, client, users, gbr_place):
        data = client.get(f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "admin")).json()
        assert data["period_closed"] is False
        assert data["can_edit"] is True
