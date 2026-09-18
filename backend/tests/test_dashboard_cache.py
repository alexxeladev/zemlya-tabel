"""Кэш помесячных итогов дашборда (task_perf п.6.2): попадание отдаёт те же
цифры, что живой расчёт; любая правка данных месяца или справочника его
обесценивает; правка другого месяца — нет; видимость ролей режется поверх."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.dashboard_cache import DashboardMonthCache, DataVersion
from app.models.employee_absences import EmployeeAbsence
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.night_shifts import NightShift
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.dashboard_cache import REFERENCE_KEY, current_versions, month_key
from tests.conftest import get_token
from tests.test_dashboard import (  # noqa: F401 — фикстуры дашборда
    MAY_BASIC, calendar_2026, company1, company2, dash_accountant, dash_admin, dash_manager,
    dept1, dept2, may_entries, schedule8, worker1, worker2,
)


def _dash(client, token, url="/api/dashboard/2026/5"):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


def _cache_row(db: Session, year=2026, month=5) -> DashboardMonthCache | None:
    db.expire_all()
    return db.query(DashboardMonthCache).filter_by(year=year, month=month).first()


def _strip_time(d: dict) -> dict:
    """Ответ без полей, которые законно меняются между запросами."""
    return {k: v for k, v in d.items() if k not in ("generated_at",)}


@pytest.fixture
def admin_token(client, dash_admin):
    return get_token(client, "dashadmin@example.com", "admin123")


class TestHitEqualsLive:
    def test_first_open_fills_cache_second_reads_it_with_same_numbers(
        self, client, db_session, admin_token, worker1, worker2, calendar_2026, may_entries,
    ):
        assert _cache_row(db_session) is None
        first = _dash(client, admin_token)
        row = _cache_row(db_session)
        assert row is not None
        assert (row.month_version, row.reference_version) == current_versions(db_session, 2026, 5)
        # В кэше — оба рабочих места, с отделом и суммами.
        assert {r["e"] for r in row.rows} == {worker1.id, worker2.id}
        assert all("total_amount" in r and "d" in r for r in row.rows)

        computed_at = row.computed_at
        second = _dash(client, admin_token)
        assert _strip_time(first) == _strip_time(second)
        assert _cache_row(db_session).computed_at == computed_at  # не пересчитывался

    def test_cached_payroll_matches_payroll_endpoint(
        self, client, db_session, admin_token, worker1, worker2, calendar_2026, may_entries,
    ):
        _dash(client, admin_token)  # промах → кэш
        data = _dash(client, admin_token)  # попадание
        payroll = client.get(
            "/api/timesheet/2026/5/payroll", headers={"Authorization": f"Bearer {admin_token}"},
        ).json()
        assert Decimal(data["payroll"]["total"]) == Decimal(payroll["grand_total"])
        assert Decimal(data["hours"]["total_hours"]) == Decimal("26")

    def test_stale_versions_in_row_are_ignored(
        self, client, db_session, admin_token, worker1, worker2, calendar_2026, may_entries,
    ):
        _dash(client, admin_token)
        row = _cache_row(db_session)
        row.rows = []  # «подложные» итоги на верной версии читались бы как есть…
        row.month_version -= 1  # …но версия старее — строка обязана быть пересчитана
        db_session.commit()
        data = _dash(client, admin_token)
        assert Decimal(data["hours"]["total_hours"]) == Decimal("26")
        assert len(_cache_row(db_session).rows) == 2


class TestInvalidation:
    def _hours(self, client, token):
        return Decimal(_dash(client, token)["hours"]["total_hours"])

    def test_cell_edit_through_api_updates_dashboard(
        self, client, db_session, admin_token, worker1, company1, calendar_2026, may_entries,
    ):
        assert self._hours(client, admin_token) == Decimal("26")
        mv_before = current_versions(db_session, 2026, 5)[0]
        r = client.put(
            "/api/timesheet/cell",
            json={"employee_id": worker1.id, "work_date": "2026-05-07",
                  "company_id": company1.id, "hours": 8},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200, r.text
        # Не «ровно +1»: правка ячейки заводит и период месяца — два флаша.
        assert current_versions(db_session, 2026, 5)[0] > mv_before
        assert self._hours(client, admin_token) == Decimal("34")

    def test_other_month_edit_keeps_may_cache(
        self, client, db_session, admin_token, worker1, company1, calendar_2026, may_entries,
    ):
        _dash(client, admin_token)
        before = _cache_row(db_session).computed_at
        mv, rv = current_versions(db_session, 2026, 5)
        db_session.add(TimesheetEntry(
            employee_id=worker1.id, work_date=date(2026, 6, 1), company_id=company1.id, hours=8,
        ))
        db_session.commit()
        assert current_versions(db_session, 2026, 5) == (mv, rv)
        assert current_versions(db_session, 2026, 6)[0] == 1
        _dash(client, admin_token)
        assert _cache_row(db_session).computed_at == before

    @pytest.mark.parametrize("make", [
        lambda w, c: EmployeeAbsence(employee_id=w.id, work_date=date(2026, 5, 12), kind="vacation"),
        lambda w, c: NightShift(employee_id=w.id, position_id=w.primary_position.id,
                                 work_date=date(2026, 5, 12)),
        lambda w, c: EmployeeAdjustment(employee_id=w.id, year=2026, month=5, kind="premium",
                                        amount=Decimal("1000"), reason="за май", created_by_id=w.id),
        lambda w, c: TimesheetPeriod(department_id=w.department_id, year=2026, month=5, status="draft"),
    ])
    def test_month_keyed_models_bump_month_version(
        self, db_session, worker1, company1, make,
    ):
        mv, rv = current_versions(db_session, 2026, 5)
        db_session.add(make(worker1, company1))
        db_session.commit()
        assert current_versions(db_session, 2026, 5) == (mv + 1, rv)

    def test_reference_change_invalidates_every_month(
        self, client, db_session, admin_token, worker1, worker2, calendar_2026, may_entries,
    ):
        before = Decimal(_dash(client, admin_token)["payroll"]["total"])
        _, rv = current_versions(db_session, 2026, 5)
        worker1.rate = Decimal("160000")  # compat-аксессор → позиция
        db_session.commit()
        assert current_versions(db_session, 2026, 5)[1] == rv + 1
        assert current_versions(db_session, 2026, 4)[1] == rv + 1
        after = Decimal(_dash(client, admin_token)["payroll"]["total"])
        assert after > before

    def test_delete_bumps_too(self, db_session, worker1, calendar_2026, may_entries):
        mv, _ = current_versions(db_session, 2026, 5)
        entry = db_session.query(TimesheetEntry).filter_by(employee_id=worker1.id).first()
        db_session.delete(entry)
        db_session.commit()
        assert current_versions(db_session, 2026, 5)[0] == mv + 1

    def test_bump_happens_after_commit_not_at_flush(self, db_session, worker1, company1):
        """Версия НЕ трогается внутри транзакции (иначе строка версии стала бы
        общей блокировкой всех писателей месяца — см. PG-тест на писателей),
        а поднимается сразу после коммита."""
        mv, rv = current_versions(db_session, 2026, 5)
        db_session.add(TimesheetEntry(
            employee_id=worker1.id, work_date=date(2026, 5, 20), company_id=company1.id, hours=8,
        ))
        db_session.flush()
        assert current_versions(db_session, 2026, 5) == (mv, rv)
        db_session.commit()
        assert current_versions(db_session, 2026, 5) == (mv + 1, rv)

    def test_rolled_back_edit_does_not_bump(self, db_session, worker1, company1):
        mv, rv = current_versions(db_session, 2026, 5)
        db_session.add(TimesheetEntry(
            employee_id=worker1.id, work_date=date(2026, 5, 20), company_id=company1.id, hours=8,
        ))
        db_session.flush()
        db_session.rollback()
        assert current_versions(db_session, 2026, 5) == (mv, rv)
        # И следующий коммит без правок ключи отката не «доносит».
        db_session.commit()
        assert current_versions(db_session, 2026, 5) == (mv, rv)

    def test_cache_writes_themselves_do_not_bump(self, client, db_session, admin_token,
                                                worker1, calendar_2026, may_entries):
        mv, rv = current_versions(db_session, 2026, 5)
        _dash(client, admin_token)
        _dash(client, admin_token)
        assert current_versions(db_session, 2026, 5) == (mv, rv)


class TestVisibilityOverCache:
    def test_manager_sees_only_own_department_from_cache(
        self, client, db_session, admin_token, dash_manager, worker1, worker2,
        calendar_2026, may_entries,
    ):
        _dash(client, admin_token)  # кэш заполнен админом — на всех
        assert {r["e"] for r in _cache_row(db_session).rows} >= {worker1.id, worker2.id}
        token = get_token(client, "dashmgr@example.com", "mgr123")
        data = _dash(client, token)
        assert Decimal(data["hours"]["total_hours"]) == Decimal("16")
        assert [d["department_name"] for d in data["hours_by_department"]] == ["Dash Dept One"]
        assert {d["department_name"] for d in data["payroll_by_department"]} == {"Dash Dept One"}

    def test_employee_sees_only_self_from_cache(
        self, client, db_session, admin_token, worker1, worker2, calendar_2026, may_entries,
    ):
        _dash(client, admin_token)
        token = get_token(client, "dashworker@example.com", "work123")
        data = _dash(client, token)
        assert Decimal(data["hours"]["total_hours"]) == Decimal("16")
        assert data["payroll"] is None

    def test_manager_miss_fills_cache_for_everyone(
        self, client, db_session, dash_manager, worker1, worker2, calendar_2026, may_entries,
    ):
        token = get_token(client, "dashmgr@example.com", "mgr123")
        data = _dash(client, token)
        assert Decimal(data["hours"]["total_hours"]) == Decimal("16")
        # Промах у менеджера посчитал всех (в т.ч. worker2 из чужого отдела и
        # самого менеджера): строка кэша общая, а не «его».
        assert {r["e"] for r in _cache_row(db_session).rows} >= {worker1.id, worker2.id}


class TestVersionsTable:
    def test_keys_are_created_lazily(self, db_session, worker1, company1):
        # Фикстуры завели сотрудника и юрлицо — это справочник; месяца ещё нет.
        keys = {v.key for v in db_session.query(DataVersion)}
        assert keys == {REFERENCE_KEY}
        assert current_versions(db_session, 2026, 5)[0] == 0
        db_session.add(TimesheetEntry(
            employee_id=worker1.id, work_date=date(2026, 5, 20), company_id=company1.id, hours=8,
        ))
        db_session.commit()
        keys = {v.key for v in db_session.query(DataVersion)}
        assert keys == {REFERENCE_KEY, month_key(2026, 5)}
        assert current_versions(db_session, 2026, 5)[0] == 1
