"""Рабочее место СТАНОВИТСЯ охранным — деньги не должны пропадать молча.

Расчёт охранной позиции ведёт вахта, общие начисления на неё не действуют. Два
перехода раньше теряли деньги тихо, и всплывало это только при сверке:

* «сделать основной» охранную позицию — заём без `loan_position_id` удерживается
  с ОСНОВНОЙ, то есть переезжал на охранную и переставал удерживаться;
* перевод обычной позиции в охранное подразделение — часы, премии и заём,
  введённые на ней, становились невидимыми для расчёта.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.services.payroll_statement import _loan_belongs_to
from app.services.positions import create_position
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
    MONTH,
    YEAR,
    _auth,
    companies,
    guard_dept,
    other_dept,
    users,
)


@pytest.fixture
def admin(client, users):
    return _auth(client, "admin")


@pytest.fixture
def office_worker(db_session, other_dept, companies) -> Employee:
    emp = Employee(full_name="Офисный Сотрудник", tab_number="T-0600", is_active=True)
    db_session.add(emp)
    db_session.commit()
    position = emp.ensure_primary_position()
    position.department_id = other_dept.id
    position.pay_type = "salary"
    position.rate = Decimal("60000")
    position.company_id = companies["ZMO"].id
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def with_guard_job(db_session, office_worker, guard_dept) -> Employee:
    """Офисный сотрудник с ПОДРАБОТКОЙ в охране (неосновная охранная позиция)."""
    create_position(office_worker, {
        "title": "Охранник", "pay_type": "per_shift", "shift_rate": Decimal("4000"),
        "department_id": guard_dept.id,
    })
    db_session.commit()
    db_session.refresh(office_worker)
    return office_worker


def _guard_position(emp):
    return next(p for p in emp.positions if not p.is_primary)


def _give_loan(db_session, emp):
    emp.loan_amount = Decimal("60000")
    emp.loan_term_months = 6
    emp.loan_start_date = date(2026, 8, 1)
    db_session.commit()


def _make_primary(client, admin, emp, position):
    return client.post(f"/api/employees/{emp.id}/positions/{position.id}/make-primary", headers=admin)


# ── «Сделать основной» охранную позицию ───────────────────────────────────────

class TestMakeGuardPositionPrimary:
    def test_loan_stays_on_the_office_position(self, client, admin, db_session, with_guard_job):
        """Заём был «на основной» без привязки. После смены основной он ЗАКРЕПЛЯЕТСЯ
        за прежним, обычным местом — и продолжает удерживаться."""
        _give_loan(db_session, with_guard_job)
        office = with_guard_job.primary_position
        guard = _guard_position(with_guard_job)

        resp = _make_primary(client, admin, with_guard_job, guard)

        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        emp = db_session.get(Employee, with_guard_job.id)
        assert emp.primary_position.id == guard.id
        assert emp.loan_position_id == office.id
        # Расчёт читает то же самое: удержание идёт с офисной позиции, не с охранной.
        assert _loan_belongs_to(emp, emp.position_by_id(office.id))
        assert not _loan_belongs_to(emp, emp.position_by_id(guard.id))

    def test_without_the_fix_the_loan_would_move(self, db_session, with_guard_job):
        """Фиксирует саму ловушку: без привязки заём принадлежит ОСНОВНОЙ позиции."""
        _give_loan(db_session, with_guard_job)
        assert with_guard_job.loan_position_id is None
        assert _loan_belongs_to(with_guard_job, with_guard_job.primary_position)

    def test_pin_is_written_to_audit_log(self, client, admin, db_session, with_guard_job):
        from app.models.audit_log import AuditLog

        _give_loan(db_session, with_guard_job)
        office_id = with_guard_job.primary_position.id
        _make_primary(client, admin, with_guard_job, _guard_position(with_guard_job))
        log = db_session.query(AuditLog).filter_by(action="make_primary").one()
        assert log.after["loan_position_id"] == office_id

    def test_no_loan_nothing_is_pinned(self, client, admin, db_session, with_guard_job):
        resp = _make_primary(client, admin, with_guard_job, _guard_position(with_guard_job))
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(Employee, with_guard_job.id).loan_position_id is None

    def test_already_pinned_loan_is_left_alone(self, client, admin, db_session, with_guard_job):
        _give_loan(db_session, with_guard_job)
        office_id = with_guard_job.primary_position.id
        with_guard_job.loan_position_id = office_id
        db_session.commit()
        _make_primary(client, admin, with_guard_job, _guard_position(with_guard_job))
        db_session.expire_all()
        assert db_session.get(Employee, with_guard_job.id).loan_position_id == office_id

    def test_office_to_office_keeps_old_behaviour(
        self, client, admin, db_session, office_worker, other_dept, companies,
    ):
        """Между обычными местами заём по-прежнему следует за основной."""
        _give_loan(db_session, office_worker)
        second = create_position(office_worker, {
            "title": "Электрик", "pay_type": "salary", "rate": Decimal("30000"),
            "department_id": other_dept.id, "company_id": companies["EKS"].id,
        })
        db_session.commit()
        resp = _make_primary(client, admin, office_worker, second)
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(Employee, office_worker.id).loan_position_id is None


# ── Перевод обычной позиции в охрану ──────────────────────────────────────────

def _hours(db_session, emp, companies, position_id="own"):
    db_session.add(TimesheetEntry(
        employee_id=emp.id,
        position_id=emp.primary_position.id if position_id == "own" else position_id,
        work_date=date(YEAR, MONTH, 5), company_id=companies["ZMO"].id, hours=8,
    ))
    db_session.commit()


def _premium(db_session, emp):
    db_session.add(EmployeeAdjustment(
        employee_id=emp.id, position_id=emp.primary_position.id, year=YEAR, month=MONTH,
        kind="premium", amount=Decimal("5000"), reason="за объект",
    ))
    db_session.commit()


def _transfer_position(client, admin, emp, dept):
    return client.patch(f"/api/employees/{emp.id}/positions/{emp.primary_position.id}",
                        headers=admin, json={"department_id": dept.id})


def _transfer_card(client, admin, emp, dept):
    return client.patch(f"/api/employees/{emp.id}", headers=admin, json={"department_id": dept.id})


TRANSFERS = [_transfer_position, _transfer_card]


@pytest.mark.parametrize("transfer", TRANSFERS, ids=["position", "card"])
class TestTransferIntoGuard:
    """Обе точки входа: правка рабочего места и плоское поле карточки (оно пишет
    основную позицию)."""

    def test_hours_block_the_transfer(
        self, client, admin, db_session, office_worker, guard_dept, companies, transfer,
    ):
        _hours(db_session, office_worker, companies)
        resp = transfer(client, admin, office_worker, guard_dept)
        assert resp.status_code == 422, resp.text
        assert "1 ячейк" in resp.json()["detail"]
        db_session.expire_all()
        assert db_session.get(Employee, office_worker.id).primary_position.department_id != guard_dept.id

    def test_legacy_hours_without_position_block_too(
        self, client, admin, db_session, office_worker, guard_dept, companies, transfer,
    ):
        """Часы с `position_id IS NULL` читаются как основная позиция."""
        _hours(db_session, office_worker, companies, position_id=None)
        assert transfer(client, admin, office_worker, guard_dept).status_code == 422

    def test_premium_blocks_the_transfer(
        self, client, admin, db_session, office_worker, guard_dept, transfer,
    ):
        _premium(db_session, office_worker)
        resp = transfer(client, admin, office_worker, guard_dept)
        assert resp.status_code == 422
        assert "преми" in resp.json()["detail"].lower()

    def test_loan_blocks_the_transfer(
        self, client, admin, db_session, office_worker, guard_dept, transfer,
    ):
        _give_loan(db_session, office_worker)
        resp = transfer(client, admin, office_worker, guard_dept)
        assert resp.status_code == 422
        assert "заём" in resp.json()["detail"].lower()

    def test_refusal_lists_everything_and_says_what_to_do(
        self, client, admin, db_session, office_worker, guard_dept, companies, transfer,
    ):
        _hours(db_session, office_worker, companies)
        _premium(db_session, office_worker)
        _give_loan(db_session, office_worker)
        detail = transfer(client, admin, office_worker, guard_dept).json()["detail"].lower()
        assert "ячейк" in detail and "преми" in detail and "заём" in detail
        assert "вахт" in detail, "должно быть сказано, как оформить человека в охрану правильно"

    def test_clean_position_is_transferred(
        self, client, admin, db_session, office_worker, guard_dept, transfer,
    ):
        resp = transfer(client, admin, office_worker, guard_dept)
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        assert db_session.get(Employee, office_worker.id).primary_position.department_id == guard_dept.id

    def test_data_of_another_position_does_not_block(
        self, client, admin, db_session, office_worker, guard_dept, other_dept, companies, transfer,
    ):
        """Проверяется переводимое рабочее место, а не человек целиком."""
        second = create_position(office_worker, {
            "title": "Электрик", "pay_type": "salary", "rate": Decimal("30000"),
            "department_id": other_dept.id, "company_id": companies["EKS"].id,
        })
        db_session.commit()
        _hours(db_session, office_worker, companies, position_id=second.id)
        assert transfer(client, admin, office_worker, guard_dept).status_code == 200


def test_transfer_between_ordinary_departments_is_untouched(
    client, admin, db_session, office_worker, companies,
):
    from app.models.departments import Department

    _hours(db_session, office_worker, companies)
    target = Department(name="Бухгалтерия", code="ACC", is_active=True)
    db_session.add(target)
    db_session.commit()
    assert _transfer_position(client, admin, office_worker, target).status_code == 200
