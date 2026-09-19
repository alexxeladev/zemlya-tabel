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


# ── Тот же обход в часах и распределении (task_stage2_access п.2.8, доп.) ─────
# Без position_id запись ложится на ОСНОВНУЮ позицию, а доступ проверялся «по
# любому рабочему месту»: менеджер отдела подработки писал часы и менял
# распределение основной позиции в чужом отделе (воспроизведено ревью).

from app.models.company_shares import CompanyShareOverride  # noqa: E402
from app.models.timesheet_entries import TimesheetEntry  # noqa: E402


@pytest.fixture
def company_id(setup, db_session):
    return db_session.query(Company).first().id


def test_foreign_manager_cannot_write_primary_cell(client, setup, db_session, company_id):
    resp = client.put(
        "/api/timesheet/cell",
        headers=_h(client, "mgr-b@example.com"),
        json={
            "employee_id": setup["worker"].id,
            "work_date": "2026-05-05",
            "company_id": company_id,
            "hours": 8,
        },
    )
    assert resp.status_code == 403
    assert db_session.query(TimesheetEntry).count() == 0


def test_foreign_timekeeper_cannot_write_primary_cell(client, setup, db_session, company_id):
    dept_b = setup["second"].department
    tk = Employee(
        full_name="Табельщик B",
        email="tk-b@example.com",
        role="timekeeper",
        is_active=True,
        hashed_password=hash_password("manager-pass-1"),
        managed_departments=[dept_b],
    )
    db_session.add(tk)
    db_session.commit()
    resp = client.put(
        "/api/timesheet/cell",
        headers=_h(client, "tk-b@example.com"),
        json={
            "employee_id": setup["worker"].id,
            "work_date": "2026-05-05",
            "company_id": company_id,
            "hours": 8,
        },
    )
    assert resp.status_code == 403


def test_foreign_manager_batch_with_primary_cell_rejected(client, setup, db_session, company_id):
    resp = client.post(
        "/api/timesheet/cells/batch",
        headers=_h(client, "mgr-b@example.com"),
        json={
            "entries": [
                {
                    "employee_id": setup["worker"].id,
                    "position_id": setup["second"].id,
                    "work_date": "2026-05-05",
                    "company_id": company_id,
                    "hours": 4,
                },
                {
                    "employee_id": setup["worker"].id,
                    "work_date": "2026-05-06",
                    "company_id": company_id,
                    "hours": 8,
                },
            ],
        },
    )
    assert resp.status_code == 403
    db_session.rollback()  # в проде сессия запроса закрывается с откатом
    assert db_session.query(TimesheetEntry).count() == 0


def test_foreign_manager_cannot_move_primary_cell_company(client, setup, db_session, company_id):
    other = Company(code="D", name="ООО Д", is_active=True)
    db_session.add(other)
    db_session.add(
        TimesheetEntry(
            employee_id=setup["worker"].id,
            position_id=None,
            work_date=datetime.date(2026, 5, 5),
            company_id=company_id,
            hours=8,
        )
    )
    db_session.commit()
    resp = client.put(
        "/api/timesheet/cell/company",
        headers=_h(client, "mgr-b@example.com"),
        json={
            "employee_id": setup["worker"].id,
            "work_date": "2026-05-05",
            "old_company_id": company_id,
            "new_company_id": other.id,
        },
    )
    assert resp.status_code == 403
    db_session.expire_all()
    assert db_session.query(TimesheetEntry).one().company_id == company_id


def test_foreign_manager_cannot_mark_primary_night_shift(client, setup, db_session):
    """У основной позиции ночные РАЗРЕШЕНЫ — до правки отметка прошла бы (200),
    так что 403 здесь доказывает именно проверку отдела, а не отказ по флагу."""
    from app.models.night_shifts import NightShift

    setup["worker"].primary_position.has_night_shifts = True
    db_session.commit()
    resp = client.put(
        "/api/timesheet/night-shift",
        headers=_h(client, "mgr-b@example.com"),
        json={"employee_id": setup["worker"].id, "work_date": "2026-05-05", "value": True},
    )
    assert resp.status_code == 403
    assert db_session.query(NightShift).count() == 0
    # Менеджер основного отдела ту же отметку ставит — сценарий рабочий.
    resp = client.put(
        "/api/timesheet/night-shift",
        headers=_h(client, "mgr-a@example.com"),
        json={"employee_id": setup["worker"].id, "work_date": "2026-05-05", "value": True},
    )
    assert resp.status_code == 200


def test_foreign_manager_cannot_override_primary_distribution(
    client, setup, db_session, company_id
):
    resp = client.put(
        "/api/timesheet/distribution",
        headers=_h(client, "mgr-b@example.com"),
        json={
            "employee_id": setup["worker"].id,
            "year": 2026,
            "month": 5,
            "shares": [{"company_id": company_id, "percent": "100"}],
        },
    )
    assert resp.status_code == 403
    assert db_session.query(CompanyShareOverride).count() == 0


def test_delete_all_overrides_needs_access_to_every_position(client, setup, db_session, company_id):
    """Без position_id снимаются правки ВСЕХ мест — менеджер подработки не
    должен сбросить заодно правку основной позиции в чужом отделе."""
    w = setup["worker"]
    db_session.add_all(
        [
            CompanyShareOverride(
                employee_id=w.id,
                position_id=None,
                company_id=company_id,
                year=2026,
                month=5,
                percent=Decimal("100"),
            ),
            CompanyShareOverride(
                employee_id=w.id,
                position_id=setup["second"].id,
                company_id=company_id,
                year=2026,
                month=5,
                percent=Decimal("100"),
            ),
        ]
    )
    db_session.commit()
    b = _h(client, "mgr-b@example.com")
    assert client.delete(f"/api/timesheet/distribution/{w.id}/2026/5", headers=b).status_code == 403
    assert db_session.query(CompanyShareOverride).count() == 2
    # Своё место он сбрасывает, как и раньше.
    resp = client.delete(
        f"/api/timesheet/distribution/{w.id}/2026/5?position_id={setup['second'].id}",
        headers=b,
    )
    assert resp.status_code == 204


def test_foreign_manager_does_not_see_primary_premium(client, setup):
    b = client.get("/api/timesheet/2026/5/adjustments", headers=_h(client, "mgr-b@example.com"))
    assert b.status_code == 200
    assert [r["reason"] for r in b.json()] == ["подработка"]
    a = client.get("/api/timesheet/2026/5/adjustments", headers=_h(client, "mgr-a@example.com"))
    assert [r["reason"] for r in a.json()] == ["основная позиция"]


# ── Доработки по второму ревью ────────────────────────────────────────────────


def test_primary_manager_cannot_delete_all_overrides_touching_second_job(
    client, setup, db_session, company_id,
):
    """Цикл по позициям в DELETE без position_id: менеджер ОСНОВНОЙ позиции не
    сносит заодно правку подработки в чужом отделе."""
    w = setup["worker"]
    db_session.add_all([
        CompanyShareOverride(employee_id=w.id, position_id=None, company_id=company_id,
                             year=2026, month=5, percent=Decimal("100")),
        CompanyShareOverride(employee_id=w.id, position_id=setup["second"].id,
                             company_id=company_id, year=2026, month=5, percent=Decimal("100")),
    ])
    db_session.commit()
    resp = client.delete(
        f"/api/timesheet/distribution/{w.id}/2026/5", headers=_h(client, "mgr-a@example.com"),
    )
    assert resp.status_code == 403
    assert db_session.query(CompanyShareOverride).count() == 2


def test_second_job_override_keeps_legacy_primary_rows(client, setup, db_session, company_id):
    """Строки без позиции — основной; правка подработки их не стирает."""
    w = setup["worker"]
    db_session.add(CompanyShareOverride(employee_id=w.id, position_id=None,
                                        company_id=company_id, year=2026, month=5,
                                        percent=Decimal("100")))
    db_session.commit()
    resp = client.put("/api/timesheet/distribution", headers=_h(client, "mgr-b@example.com"), json={
        "employee_id": w.id, "position_id": setup["second"].id, "year": 2026, "month": 5,
        "shares": [{"company_id": company_id, "percent": "100"}],
    })
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(CompanyShareOverride).filter_by(position_id=None).count() == 1


def test_owner_manager_still_writes_own_workplace(client, setup, db_session, company_id):
    """Не перекрыто лишнего: всё то же самое на СВОЁМ месте с position_id."""
    from app.models.night_shifts import NightShift

    second = setup["second"]
    second.has_night_shifts = True
    other = Company(code="E", name="ООО Е", is_active=True)
    db_session.add(other)
    db_session.commit()
    h = _h(client, "mgr-b@example.com")
    w = setup["worker"].id
    assert client.put("/api/timesheet/cell", headers=h, json={
        "employee_id": w, "position_id": second.id, "work_date": "2026-05-05",
        "company_id": company_id, "hours": 4,
    }).status_code == 200
    assert client.post("/api/timesheet/cells/batch", headers=h, json={"entries": [
        {"employee_id": w, "position_id": second.id, "work_date": "2026-05-06",
         "company_id": company_id, "hours": 4},
    ]}).status_code == 200
    assert client.put("/api/timesheet/cell/company", headers=h, json={
        "employee_id": w, "position_id": second.id, "work_date": "2026-05-05",
        "old_company_id": company_id, "new_company_id": other.id,
    }).status_code == 200
    assert client.put("/api/timesheet/night-shift", headers=h, json={
        "employee_id": w, "position_id": second.id, "work_date": "2026-05-07", "value": True,
    }).status_code == 200
    assert db_session.query(NightShift).count() == 1
    assert client.put("/api/timesheet/distribution", headers=h, json={
        "employee_id": w, "position_id": second.id, "year": 2026, "month": 5,
        "shares": [{"company_id": company_id, "percent": "100"}],
    }).status_code == 200


def test_manager_acts_on_inactive_second_job_of_own_department(client, setup, db_session):
    """Снятая с учёта подработка своего отдела — всё ещё его: премию на ней
    удалить можно (было 403 из-за проверки «по любому АКТИВНОМУ месту»)."""
    setup["second"].is_active = False
    db_session.commit()
    resp = client.delete(
        f"/api/timesheet/adjustments/{setup['second_premium'].id}",
        headers=_h(client, "mgr-b@example.com"),
    )
    assert resp.status_code == 204


def test_company_shares_read_checks_requested_workplace(client, setup):
    w, second = setup["worker"].id, setup["second"].id
    a, b = _h(client, "mgr-a@example.com"), _h(client, "mgr-b@example.com")
    url = f"/api/employees/{w}/company-shares"
    assert client.get(url, params={"position_id": second}, headers=a).status_code == 403
    assert client.get(url, params={"position_id": second}, headers=b).status_code == 200
    assert client.get(url, headers=b).status_code == 403
    assert client.get(url, headers=a).status_code == 200


# ── Доработки по третьему ревью ───────────────────────────────────────────────

from app.models.company_shares import EmployeeCompanyShare  # noqa: E402


def _admin(client):
    return {"Authorization": f"Bearer {get_token(client, 'admin@example.com', 'admin123')}"}


def test_card_shares_of_second_job_keep_legacy_primary_rows(
    client, setup, db_session, company_id, admin_user,
):
    """Вторая копия правила (карточка): проценты подработки не стирают строки
    без позиции — они принадлежат основной."""
    w = setup["worker"]
    db_session.add(EmployeeCompanyShare(employee_id=w.id, position_id=None,
                                        company_id=company_id, percent=Decimal("100")))
    db_session.commit()
    resp = client.put(f"/api/employees/{w.id}/company-shares", headers=_admin(client), json={
        "position_id": setup["second"].id,
        "shares": [{"company_id": company_id, "percent": "100"}],
    })
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(EmployeeCompanyShare).filter_by(position_id=None).count() == 1


def test_card_shares_of_primary_replace_legacy_rows(
    client, setup, db_session, company_id, admin_user,
):
    """Обратный случай: правка ОСНОВНОЙ заменяет и её строки без позиции."""
    w = setup["worker"]
    db_session.add(EmployeeCompanyShare(employee_id=w.id, position_id=None,
                                        company_id=company_id, percent=Decimal("100")))
    db_session.commit()
    resp = client.put(f"/api/employees/{w.id}/company-shares", headers=_admin(client), json={
        "shares": [{"company_id": company_id, "percent": "100"}],
    })
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(EmployeeCompanyShare).filter_by(position_id=None).count() == 0


def test_primary_override_replaces_legacy_rows(client, setup, db_session, company_id):
    """Обратный случай для месячной правки: основная заменяет строки без позиции."""
    w = setup["worker"]
    db_session.add(CompanyShareOverride(employee_id=w.id, position_id=None,
                                        company_id=company_id, year=2026, month=5,
                                        percent=Decimal("100")))
    db_session.commit()
    resp = client.put("/api/timesheet/distribution", headers=_h(client, "mgr-a@example.com"), json={
        "employee_id": w.id, "year": 2026, "month": 5,
        "shares": [{"company_id": company_id, "percent": "100"}],
    })
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(CompanyShareOverride).filter_by(position_id=None).count() == 0
