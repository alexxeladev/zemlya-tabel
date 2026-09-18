"""Премии и заём — проверка отдела РАБОЧЕГО МЕСТА (task_stage2_access п.2.8).

Было: удаление премии, ручная правка займа и её отмена проверяли доступ к
сотруднику «по любому его рабочему месту». Менеджер отдела, где у человека
совместительство, удалял премию основной позиции (чужой отдел) и правил заём,
который удерживается с основной. Создание премии без `position_id` ложилось на
основную позицию по той же дыре.
"""

import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.loan_deductions import LoanDeduction
from app.models.positions import EmployeePosition
from tests.conftest import get_token


@pytest.fixture
def setup(db_session):
    company = Company(code="C", name="ООО Ц", is_active=True)
    dept_a = Department(name="Основной отдел", code="A", is_active=True)
    dept_b = Department(name="Отдел совместительства", code="B", is_active=True)
    db_session.add_all([company, dept_a, dept_b])
    db_session.commit()

    # Совместитель: основная позиция в A (там и заём), подработка в B.
    worker = Employee(
        full_name="Совместитель",
        is_active=True,
        rate=Decimal("80000"),
        department_id=dept_a.id,
        default_company_id=company.id,
        loan_amount=Decimal("60000"),
        loan_term_months=6,
        loan_start_date=datetime.date(2026, 1, 1),
    )
    db_session.add(worker)
    db_session.commit()
    second = EmployeePosition(
        employee_id=worker.id,
        is_primary=False,
        title="Подработка",
        department_id=dept_b.id,
        company_id=company.id,
        rate=Decimal("20000"),
    )
    db_session.add(second)
    db_session.commit()

    def manager(email, dept):
        m = Employee(
            full_name=email,
            email=email,
            role="manager",
            is_active=True,
            hashed_password=hash_password("manager-pass-1"),
            managed_departments=[dept],
        )
        db_session.add(m)
        return m

    manager("mgr-a@example.com", dept_a)
    manager("mgr-b@example.com", dept_b)
    db_session.commit()

    primary_premium = EmployeeAdjustment(
        employee_id=worker.id,
        position_id=None,
        year=2026,
        month=5,
        kind="premium",
        amount=Decimal("10000"),
        reason="основная позиция",
    )
    second_premium = EmployeeAdjustment(
        employee_id=worker.id,
        position_id=second.id,
        year=2026,
        month=5,
        kind="premium",
        amount=Decimal("3000"),
        reason="подработка",
    )
    override = LoanDeduction(
        employee_id=worker.id,
        year=2026,
        month=5,
        planned_amount=Decimal("10000"),
        actual_amount=Decimal("5000"),
    )
    db_session.add_all([primary_premium, second_premium, override])
    db_session.commit()
    return {
        "worker": worker,
        "second": second,
        "primary_premium": primary_premium,
        "second_premium": second_premium,
    }


def _h(client, email):
    return {"Authorization": f"Bearer {get_token(client, email, 'manager-pass-1')}"}


def test_foreign_manager_cannot_delete_primary_premium(client: TestClient, setup, db_session):
    resp = client.delete(
        f"/api/timesheet/adjustments/{setup['primary_premium'].id}",
        headers=_h(client, "mgr-b@example.com"),
    )
    assert resp.status_code == 403
    assert db_session.get(EmployeeAdjustment, setup["primary_premium"].id) is not None


def test_foreign_manager_cannot_create_premium_on_primary_by_omitting_position(
    client: TestClient, setup, db_session
):
    before = db_session.query(EmployeeAdjustment).count()
    resp = client.post(
        "/api/timesheet/adjustments",
        headers=_h(client, "mgr-b@example.com"),
        json={
            "employee_id": setup["worker"].id,
            "year": 2026,
            "month": 5,
            "kind": "premium",
            "amount": "99999",
            "reason": "себе в чужой отдел",
        },
    )
    assert resp.status_code == 403
    assert db_session.query(EmployeeAdjustment).count() == before


def test_foreign_manager_cannot_override_loan(client: TestClient, setup, db_session):
    resp = client.post(
        "/api/timesheet/loan-override",
        headers=_h(client, "mgr-b@example.com"),
        json={
            "employee_id": setup["worker"].id,
            "year": 2026,
            "month": 6,
            "actual_amount": "0",
        },
    )
    assert resp.status_code == 403
    assert db_session.query(LoanDeduction).filter_by(month=6).count() == 0


def test_foreign_manager_cannot_delete_loan_override(client: TestClient, setup, db_session):
    resp = client.delete(
        f"/api/timesheet/loan-override/{setup['worker'].id}/2026/5",
        headers=_h(client, "mgr-b@example.com"),
    )
    assert resp.status_code == 403
    assert db_session.query(LoanDeduction).filter_by(month=5).count() == 1


def test_owner_managers_keep_their_rights(client: TestClient, setup, db_session):
    """Не перекрыто лишнего: каждый менеджер работает со СВОИМ рабочим местом."""
    b = _h(client, "mgr-b@example.com")
    a = _h(client, "mgr-a@example.com")
    assert (
        client.delete(
            f"/api/timesheet/adjustments/{setup['second_premium'].id}", headers=b
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/timesheet/adjustments",
            headers=b,
            json={
                "employee_id": setup["worker"].id,
                "position_id": setup["second"].id,
                "year": 2026,
                "month": 5,
                "kind": "kpi",
                "amount": "1000",
                "reason": "KPI подработки",
            },
        ).status_code
        == 201
    )
    assert (
        client.delete(
            f"/api/timesheet/adjustments/{setup['primary_premium'].id}", headers=a
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/timesheet/loan-override",
            headers=a,
            json={
                "employee_id": setup["worker"].id,
                "year": 2026,
                "month": 6,
                "actual_amount": "0",
            },
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/timesheet/loan-override/{setup['worker'].id}/2026/5", headers=a
        ).status_code
        == 204
    )


def test_manager_a_cannot_delete_premium_of_second_job(client: TestClient, setup, db_session):
    resp = client.delete(
        f"/api/timesheet/adjustments/{setup['second_premium'].id}",
        headers=_h(client, "mgr-a@example.com"),
    )
    assert resp.status_code == 403
