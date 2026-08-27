"""Инструмент «Перенести отдел в другую компанию» (task_move_department).

Главная проверка здесь — что ЗАКРЫТЫЙ месяц после переноса считается ровно так
же, как до него. Расчёт в системе не снапшотится, поэтому перенос сначала
фиксирует расклад закрытых месяцев месячным override-ом (см.
`app/services/department_move.py`), и тесты сверяют суммы до/после построчно.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.company_shares import CompanyShareOverride, EmployeeCompanyShare
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.payroll_statement import build_payroll_statement
from tests.conftest import get_token

MAY = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]
JUNE = {"year": 2026, "months": [{"month": 6, "days": "6,7,13,14,20,21,27,28"}]}
JUNE_WORKDAYS = [d for d in range(1, 31) if d not in (6, 7, 13, 14, 20, 21, 27, 28)]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _statement(db: Session, emp_ids: list[int], year: int, month: int):
    """Расклад по юрлицам за месяц: {(employee, position): {company: сумма}}."""
    db.expire_all()
    emps = db.query(Employee).filter(Employee.id.in_(emp_ids)).all()
    from app.services.timesheet import get_month_entries

    entries = get_month_entries(db, emps, year, month)
    st = build_payroll_statement(db, emps, entries, year, month)
    return {
        (r.employee_id, r.position_id): {d.company_id: d.amount for d in r.distribution}
        for r in st.rows
    }


@pytest.fixture
def org(db_session: Session):
    """Стройдепартамент в «Земле МО» + соседний отдел в другой компании."""
    old = Company(code="ZMO", name="Земля МО", is_active=True)
    new = Company(code="STR", name="Стройдепартамент", is_active=True)
    other = Company(code="KMF", name="Комфорт", is_active=True)
    db_session.add_all([old, new, other])
    stroy = Department(name="Стройдеп", code="SD", is_active=True)
    neighbour = Department(name="Соседний", code="ND", is_active=True)
    db_session.add_all([stroy, neighbour])
    sched = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday", is_active=True)
    db_session.add(sched)
    db_session.add_all([
        ProductionCalendar(year=2026, data=MAY, source="manual"),
    ])
    db_session.commit()
    for o in (old, new, other, stroy, neighbour, sched):
        db_session.refresh(o)
    stroy.head_company_id = old.id
    neighbour.head_company_id = other.id
    db_session.commit()
    return {
        "old": old, "new": new, "other": other,
        "stroy": stroy, "neighbour": neighbour, "sched": sched,
    }


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(full_name="Админ", email="mvadmin@example.com",
                   hashed_password=hash_password("admin123"), role="admin",
                   is_active=True, must_change_password=False, is_system_admin=True)
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def staff(db_session: Session, org):
    """Трое: обычный сотрудник с часами, совместитель (вторая позиция в соседнем
    отделе) и сотрудник БЕЗ часов, но с премией — на нём и ломалась история."""
    sched, old = org["sched"], org["old"]
    worker = Employee(full_name="Рабочий", tab_number="W-1", is_active=True,
                      rate=Decimal("80000"), schedule_id=sched.id,
                      default_company_id=old.id, department_id=org["stroy"].id)
    combiner = Employee(full_name="Совместитель", tab_number="C-1", is_active=True,
                        rate=Decimal("60000"), schedule_id=sched.id,
                        default_company_id=old.id, department_id=org["stroy"].id)
    bonus_only = Employee(full_name="Только премия", tab_number="P-1", is_active=True,
                          rate=Decimal("50000"), schedule_id=sched.id,
                          default_company_id=old.id, department_id=org["stroy"].id)
    db_session.add_all([worker, combiner, bonus_only])
    db_session.commit()
    for e in (worker, combiner, bonus_only):
        db_session.refresh(e)

    # Вторая позиция совместителя — в СОСЕДНЕМ отделе и на другой компании.
    from app.services.positions import create_position

    side = create_position(combiner, {
        "title": "Электрик", "department_id": org["neighbour"].id,
        "company_id": org["other"].id, "schedule_id": sched.id,
        "pay_type": "salary", "rate": Decimal("30000"),
    })
    db_session.add(side)
    db_session.commit()
    db_session.refresh(side)
    return {"worker": worker, "combiner": combiner, "bonus": bonus_only, "side": side}


def _fill_may(db: Session, org, staff):
    """Закрытый май: часы по двум юрлицам + премия у сотрудника без часов."""
    old, other = org["old"], org["other"]
    w, c, b = staff["worker"], staff["combiner"], staff["bonus"]
    for i, day in enumerate(MAY_WORKDAYS):
        # Рабочий: часть часов на стороннее юрлицо — расклад закрытого месяца
        # должен остаться именно таким.
        db.add(TimesheetEntry(employee_id=w.id, position_id=w.primary_position.id,
                              work_date=date(2026, 5, day),
                              company_id=other.id if i % 3 == 0 else old.id, hours=8))
        db.add(TimesheetEntry(employee_id=c.id, position_id=c.primary_position.id,
                              work_date=date(2026, 5, day), company_id=old.id, hours=8))
        db.add(TimesheetEntry(employee_id=c.id, position_id=staff["side"].id,
                              work_date=date(2026, 5, day), company_id=other.id, hours=4))
    db.add(EmployeeAdjustment(employee_id=b.id, position_id=b.primary_position.id,
                              year=2026, month=5, kind="premium",
                              amount=Decimal("10000"), reason="за объект"))
    db.add(TimesheetPeriod(department_id=org["stroy"].id, year=2026, month=5, status="closed"))
    db.add(TimesheetPeriod(department_id=org["neighbour"].id, year=2026, month=5, status="closed"))
    db.commit()


def _move(client, token, dept_id, company_id):
    return client.post(f"/api/departments/{dept_id}/move",
                       json={"target_company_id": company_id}, headers=_h(token))


# ── Сам перенос ───────────────────────────────────────────────────────────────

class TestMove:
    def test_moves_head_company_and_positions(
        self, client: TestClient, db_session, org, admin, staff
    ):
        token = get_token(client, "mvadmin@example.com", "admin123")
        resp = _move(client, token, org["stroy"].id, org["new"].id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["positions_moved"] == 3      # три основные позиции отдела
        assert body["employees_affected"] == 3

        db_session.expire_all()
        assert db_session.get(Department, org["stroy"].id).head_company_id == org["new"].id
        for key in ("worker", "combiner", "bonus"):
            emp = db_session.get(Employee, staff[key].id)
            assert emp.primary_position.company_id == org["new"].id

    def test_other_department_positions_untouched(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Совместитель: позиция в соседнем отделе остаётся на своей компании."""
        token = get_token(client, "mvadmin@example.com", "admin123")
        _move(client, token, org["stroy"].id, org["new"].id)

        db_session.expire_all()
        side = db_session.get(type(staff["side"]), staff["side"].id)
        assert side.company_id == org["other"].id
        assert side.department_id == org["neighbour"].id
        # И сам соседний отдел не переехал.
        assert db_session.get(Department, org["neighbour"].id).head_company_id == org["other"].id

    def test_managers_keep_access(self, client: TestClient, db_session, org, admin):
        """Отдел не пересоздаётся — связи руководителя и табельщика на месте."""
        mgr = Employee(full_name="Рук", email="mvmgr@example.com",
                       hashed_password=hash_password("x12345"), role="manager",
                       is_active=True, must_change_password=False)
        tk = Employee(full_name="Таб", email="mvtk@example.com",
                      hashed_password=hash_password("x12345"), role="timekeeper",
                      is_active=True, must_change_password=False)
        db_session.add_all([mgr, tk])
        db_session.commit()
        stroy = db_session.get(Department, org["stroy"].id)
        stroy.managers.extend([mgr, tk])
        db_session.commit()

        token = get_token(client, "mvadmin@example.com", "admin123")
        assert _move(client, token, org["stroy"].id, org["new"].id).status_code == 200

        db_session.expire_all()
        from app.services.org_access import can_access_department, managed_department_ids

        mgr = db_session.get(Employee, mgr.id)
        tk = db_session.get(Employee, tk.id)
        assert org["stroy"].id in managed_department_ids(mgr)
        assert org["stroy"].id in managed_department_ids(tk)
        assert can_access_department(mgr, org["stroy"].id)
        assert can_access_department(tk, org["stroy"].id)

    def test_same_company_rejected(self, client: TestClient, org, admin, staff):
        token = get_token(client, "mvadmin@example.com", "admin123")
        resp = _move(client, token, org["stroy"].id, org["old"].id)
        assert resp.status_code == 422

    def test_unknown_company(self, client: TestClient, org, admin):
        token = get_token(client, "mvadmin@example.com", "admin123")
        assert _move(client, token, org["stroy"].id, 9999).status_code == 404

    def test_admin_only(self, client: TestClient, db_session, org, admin):
        for role, email in (("manager", "mvm2@example.com"), ("accountant", "mva2@example.com"),
                            ("timekeeper", "mvt2@example.com")):
            emp = Employee(full_name=role, email=email, role=role, is_active=True,
                           hashed_password=hash_password("x12345"), must_change_password=False)
            db_session.add(emp)
        db_session.commit()
        for email in ("mvm2@example.com", "mva2@example.com", "mvt2@example.com"):
            token = get_token(client, email, "x12345")
            assert _move(client, token, org["stroy"].id, org["new"].id).status_code == 403
            assert client.get(
                f"/api/departments/{org['stroy'].id}/move-preview",
                params={"target_company_id": org["new"].id}, headers=_h(token),
            ).status_code == 403


# ── Закрытые периоды: главная проверка ────────────────────────────────────────

class TestClosedPeriodsUnchanged:
    def test_closed_month_distribution_identical(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Расклад ЗАКРЫТОГО мая до и после переноса — рубль в рубль."""
        _fill_may(db_session, org, staff)
        ids = [staff["worker"].id, staff["combiner"].id, staff["bonus"].id]
        before = _statement(db_session, ids, 2026, 5)
        assert before, "ведомость мая должна быть непустой"

        token = get_token(client, "mvadmin@example.com", "admin123")
        assert _move(client, token, org["stroy"].id, org["new"].id).status_code == 200

        after = _statement(db_session, ids, 2026, 5)
        assert after == before

    def test_bonus_only_employee_stays_on_old_company(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Сотрудник без часов, но с премией: без заморозки его 10 000 ₽ уехали бы
        на новое юрлицо целиком — это и есть правка истории."""
        _fill_may(db_session, org, staff)
        b = staff["bonus"]
        before = _statement(db_session, [b.id], 2026, 5)[(b.id, b.primary_position.id)]
        assert before == {org["old"].id: Decimal("10000")}

        token = get_token(client, "mvadmin@example.com", "admin123")
        _move(client, token, org["stroy"].id, org["new"].id)

        after = _statement(db_session, [b.id], 2026, 5)[(b.id, b.primary_position.id)]
        assert after == {org["old"].id: Decimal("10000")}
        assert org["new"].id not in after

    def test_freeze_writes_overrides_for_closed_month_only(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Заморозка касается только закрытых месяцев отдела."""
        _fill_may(db_session, org, staff)
        token = get_token(client, "mvadmin@example.com", "admin123")
        body = _move(client, token, org["stroy"].id, org["new"].id).json()
        assert body["closed_months_frozen"] == 1
        assert body["override_rows_written"] > 0

        rows = db_session.query(CompanyShareOverride).all()
        assert rows and {(r.year, r.month) for r in rows} == {(2026, 5)}
        # Подработка в соседнем отделе не замораживается — её компания не менялась.
        assert all(r.position_id != staff["side"].id for r in rows)

    def test_existing_manual_override_not_overwritten(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Ручной расклад бухгалтера уже на вершине каскада — не трогаем его."""
        _fill_may(db_session, org, staff)
        w = staff["worker"]
        db_session.add(CompanyShareOverride(
            employee_id=w.id, position_id=w.primary_position.id,
            company_id=org["other"].id, year=2026, month=5, percent=Decimal("100"),
        ))
        db_session.commit()
        before = _statement(db_session, [w.id], 2026, 5)

        token = get_token(client, "mvadmin@example.com", "admin123")
        _move(client, token, org["stroy"].id, org["new"].id)

        mine = db_session.query(CompanyShareOverride).filter_by(
            employee_id=w.id, year=2026, month=5).all()
        assert len(mine) == 1 and mine[0].percent == Decimal("100")
        assert _statement(db_session, [w.id], 2026, 5) == before

    def test_legacy_null_position_override_not_duplicated(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Набор, заведённый до появления позиций (`position_id IS NULL`), каскад
        читает как ОСНОВНУЮ позицию. Если не разрешить NULL так же, заморозка
        допишет второй набор на ту же позицию, и в каскаде они сольются."""
        _fill_may(db_session, org, staff)
        w = staff["worker"]
        db_session.add(CompanyShareOverride(
            employee_id=w.id, position_id=None, company_id=org["other"].id,
            year=2026, month=5, percent=Decimal("100"),
        ))
        db_session.commit()
        before = _statement(db_session, [w.id], 2026, 5)

        token = get_token(client, "mvadmin@example.com", "admin123")
        _move(client, token, org["stroy"].id, org["new"].id)

        rows = db_session.query(CompanyShareOverride).filter_by(
            employee_id=w.id, year=2026, month=5).all()
        assert len(rows) == 1 and rows[0].position_id is None
        assert _statement(db_session, [w.id], 2026, 5) == before

    def test_no_closed_periods_writes_nothing(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Отдел без закрытых месяцев переносится без единой строки заморозки."""
        token = get_token(client, "mvadmin@example.com", "admin123")
        body = _move(client, token, org["stroy"].id, org["new"].id).json()
        assert body["closed_months_frozen"] == 0
        assert body["override_rows_written"] == 0
        assert db_session.query(CompanyShareOverride).count() == 0


# ── Новый месяц считается на новую компанию ───────────────────────────────────

class TestForwardMonth:
    def test_new_month_goes_to_new_company(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Июнь (открытый) после переноса распределяется на целевое юрлицо."""
        _fill_may(db_session, org, staff)
        token = get_token(client, "mvadmin@example.com", "admin123")
        _move(client, token, org["stroy"].id, org["new"].id)

        db_session.add(TimesheetPeriod(department_id=org["stroy"].id, year=2026,
                                       month=6, status="draft"))
        db_session.commit()
        db_session.expire_all()
        w = db_session.get(Employee, staff["worker"].id)
        # Часы нового месяца заводятся уже на новую компанию — она подставляется
        # из позиции.
        assert w.primary_position.company_id == org["new"].id
        for day in JUNE_WORKDAYS:
            db_session.add(TimesheetEntry(
                employee_id=w.id, position_id=w.primary_position.id,
                work_date=date(2026, 6, day),
                company_id=w.primary_position.company_id, hours=8))
        db_session.commit()

        june = _statement(db_session, [w.id], 2026, 6)[(w.id, w.primary_position.id)]
        assert set(june) == {org["new"].id}
        assert june[org["new"].id] > 0

    def test_bonus_only_employee_new_month_on_new_company(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """А вот в ОТКРЫТОМ месяце премия сотрудника без часов уходит уже на новое
        юрлицо — смена действует вперёд."""
        _fill_may(db_session, org, staff)
        b = staff["bonus"]
        db_session.add(EmployeeAdjustment(
            employee_id=b.id, position_id=b.primary_position.id, year=2026, month=6,
            kind="premium", amount=Decimal("7000"), reason="июнь"))
        db_session.commit()

        token = get_token(client, "mvadmin@example.com", "admin123")
        _move(client, token, org["stroy"].id, org["new"].id)

        june = _statement(db_session, [b.id], 2026, 6)[(b.id, b.primary_position.id)]
        assert june == {org["new"].id: Decimal("7000")}


# ── Предпросмотр ──────────────────────────────────────────────────────────────

class TestPreview:
    def test_preview_reports_scope(self, client: TestClient, db_session, org, admin, staff):
        _fill_may(db_session, org, staff)
        token = get_token(client, "mvadmin@example.com", "admin123")
        resp = client.get(f"/api/departments/{org['stroy'].id}/move-preview",
                          params={"target_company_id": org["new"].id}, headers=_h(token))
        assert resp.status_code == 200, resp.text
        p = resp.json()
        assert p["department_name"] == "Стройдеп"
        assert p["source_company_name"] == "Земля МО"
        assert p["target_company_name"] == "Стройдепартамент"
        assert p["employee_count"] == 3
        assert p["position_count"] == 3
        # Подработка совместителя в соседнем отделе — та, что НЕ переедет.
        assert p["untouched_position_count"] == 1
        assert p["closed_months"] == [{"year": 2026, "month": 5}]

    def test_preview_changes_nothing(self, client: TestClient, db_session, org, admin, staff):
        _fill_may(db_session, org, staff)
        token = get_token(client, "mvadmin@example.com", "admin123")
        client.get(f"/api/departments/{org['stroy'].id}/move-preview",
                   params={"target_company_id": org["new"].id}, headers=_h(token))
        db_session.expire_all()
        assert db_session.get(Department, org["stroy"].id).head_company_id == org["old"].id
        assert db_session.query(CompanyShareOverride).count() == 0

    def test_preview_warns_about_stale_shares(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """% в карточке на старую компанию перенос не трогает — предупреждаем."""
        w = staff["worker"]
        db_session.add_all([
            EmployeeCompanyShare(employee_id=w.id, position_id=w.primary_position.id,
                                 company_id=org["old"].id, percent=Decimal("60")),
            EmployeeCompanyShare(employee_id=w.id, position_id=w.primary_position.id,
                                 company_id=org["other"].id, percent=Decimal("40")),
        ])
        db_session.commit()
        token = get_token(client, "mvadmin@example.com", "admin123")
        p = client.get(f"/api/departments/{org['stroy'].id}/move-preview",
                       params={"target_company_id": org["new"].id},
                       headers=_h(token)).json()
        assert p["stale_share_position_count"] == 1

    def test_preview_silent_when_target_already_in_shares(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Целевая компания уже есть в наборе — перенастраивать нечего, молчим."""
        w = staff["worker"]
        db_session.add_all([
            EmployeeCompanyShare(employee_id=w.id, position_id=w.primary_position.id,
                                 company_id=org["new"].id, percent=Decimal("70")),
            EmployeeCompanyShare(employee_id=w.id, position_id=w.primary_position.id,
                                 company_id=org["other"].id, percent=Decimal("30")),
        ])
        db_session.commit()
        token = get_token(client, "mvadmin@example.com", "admin123")
        p = client.get(f"/api/departments/{org['stroy'].id}/move-preview",
                       params={"target_company_id": org["new"].id},
                       headers=_h(token)).json()
        assert p["stale_share_position_count"] == 0

    def test_preview_flags_department_default_without_target(
        self, client: TestClient, db_session, org, admin, staff
    ):
        """Дефолт отдела переживает перенос и стоит выше авто по часам."""
        from app.models.company_shares import DepartmentCompanyShare

        db_session.add_all([
            DepartmentCompanyShare(department_id=org["stroy"].id,
                                   company_id=org["old"].id, percent=Decimal("50")),
            DepartmentCompanyShare(department_id=org["stroy"].id,
                                   company_id=org["other"].id, percent=Decimal("50")),
        ])
        db_session.commit()
        token = get_token(client, "mvadmin@example.com", "admin123")
        p = client.get(f"/api/departments/{org['stroy'].id}/move-preview",
                       params={"target_company_id": org["new"].id},
                       headers=_h(token)).json()
        assert p["department_shares_stale"] is True


# ── Точность заморозки ────────────────────────────────────────────────────────

class TestFreezePrecision:
    """Проценты заморозки считаются из уже посчитанных СУММ, и обратный пересчёт
    обязан вернуть те же рубли. Шаг 0.01 и даже 0.001 этого не даёт — заморозка
    сама сдвигала бы историю, ради которой затевалась (см. миграцию
    `e1f2a3b4c5d6`). Тест держит шаг: «упростив» его обратно, регрессию видно."""

    def test_step_reproduces_amounts_exactly(self):
        import random

        from app.services.department_move import FREEZE_PERCENT_STEP
        from app.services.distribution import distribute

        rnd = random.Random(20260827)
        for _ in range(2000):
            n = rnd.randint(2, 5)
            weights = {i: Decimal(rnd.randint(1, 200)) for i in range(1, n + 1)}
            total = Decimal(rnd.randint(1000, 900000))
            main = rnd.choice(list(weights))
            original = distribute(total, weights, main)
            frozen = distribute(Decimal("100"), original, None, FREEZE_PERCENT_STEP)
            # После переноса основной компании в наборе уже нет — остаток
            # округления достаётся наибольшей доле, а не ей.
            recomputed = distribute(total, frozen, 10**6)
            assert recomputed == original

    def test_coarser_step_would_lose_money(self):
        """Контрольный пример: на трёх знаках расклад разъезжается."""
        from app.services.distribution import distribute

        weights = {1: Decimal("56"), 3: Decimal("112"), 5: Decimal("31")}
        original = distribute(Decimal("734500"), weights, 5)
        coarse = distribute(Decimal("100"), original, None, Decimal("0.001"))
        assert distribute(Decimal("734500"), coarse, 10**6) != original
