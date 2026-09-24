"""Заём при нулевом начислении (REMEDIATION_PLAN п.5.1, аудит 2-Б).

Удержать можно только то, что начислено позиции займа за месяц, за вычетом
аванса: нечего удерживать — месяц пропускается, остаток не уменьшается, срок
займа растягивается. Решения заказчика 24.09.2026:

- начислено меньше платежа — удерживается сколько есть, недоудержанное
  остаётся в долге;
- решает начисление ПОЗИЦИИ займа, а не человека целиком;
- начисление — «Итого начислено» (с премией и KPI);
- месяц «на проверке» считается как открытый;
- закрытый месяц — факт из снимка; закрытый без снимка и месяц до первого
  периода отдела — доля удержана по графику, как раньше.

Почасовая оплата на цикле 2/2 — чтобы начисление было ровно «часы × ставка»,
без производственного календаря: 8 ч × 1 000 = 8 000 ₽ за день.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.loan_deductions import LoanDeduction
from app.models.positions import EmployeePosition
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.dashboard_cache import REFERENCE_KEY, current_versions
from app.services.payout import loan_capacity, loan_month_state
from app.services.payroll_statement import build_payroll_summary, loan_status
from app.services.timesheet_periods import close_period
from tests.conftest import get_token

D = Decimal
LOAN = D("60000")  # 6 месяцев по 10 000, с июня по ноябрь 2026
SHARE = D("10000")


# ── Чистая функция ────────────────────────────────────────────────────────────

class TestPureRule:
    def _state(self, y, m, capacities=None, overrides=None):
        return loan_month_state(LOAN, 6, date(2026, 6, 1), y, m, overrides, capacities)

    def test_zero_capacity_skips_month_and_keeps_debt(self):
        s = self._state(2026, 6, {(2026, 6): D("0")})
        assert s.planned == SHARE
        assert s.actual == D("0"), "при нулевом начислении удерживать нельзя"
        assert s.remaining_after == LOAN, "остаток не должен уменьшиться"
        assert s.shortfall == SHARE

    def test_skip_extends_term(self):
        s = self._state(2026, 7, {(2026, 6): D("0")})
        assert s.actual == SHARE
        assert s.remaining_after == D("50000")
        assert s.payments_left == 5
        assert s.payments_left_by_term(2026, 7) == 4
        assert s.payments_left > s.payments_left_by_term(2026, 7)
        assert s.short_months == ((2026, 6, SHARE, D("0")),)

    def test_less_than_share_takes_what_there_is(self):
        s = self._state(2026, 6, {(2026, 6): D("2308")})
        assert s.actual == D("2308")
        assert s.shortfall == D("7692")
        assert s.remaining_after == D("57692")

    def test_enough_capacity_takes_full_share(self):
        s = self._state(2026, 6, {(2026, 6): D("10001")})
        assert s.actual == SHARE and s.shortfall == D("0")

    def test_month_without_capacity_keeps_old_rule(self):
        # Нет данных о начислении (закрыт без снимка, до начала учёта) — по графику.
        s = self._state(2026, 7, {})
        assert s.remaining_after == D("40000")
        assert s.short_months == ()

    def test_manual_override_beats_zero_capacity(self):
        s = self._state(2026, 6, {(2026, 6): D("0")}, {(2026, 6): D("3000")})
        assert s.actual == D("3000") and s.is_manual and s.shortfall == D("0")

    def test_skipped_months_push_last_payment_past_term(self):
        caps = {(2026, 6): D("0"), (2026, 7): D("0")}
        nov = self._state(2026, 11, caps)
        assert nov.remaining_after == D("20000"), "два пропуска — два платежа сверх срока"
        dec = self._state(2026, 12, caps)
        assert dec.active and dec.actual == SHARE

    def test_capacity_is_accrued_minus_advance_never_negative(self):
        assert loan_capacity(D("16000"), D("10000")) == D("6000")
        assert loan_capacity(D("5000"), D("8000")) == D("0")


# ── Расчёт с базой ────────────────────────────────────────────────────────────

@pytest.fixture
def world(db_session: Session, admin_user):
    zmo = Company(code="ZMO", name="Земля МО")
    kft = Company(code="KFT", name="Комфорт")
    sched = Schedule(
        name="2/2", hours_per_shift=12, schedule_type="cyclic",
        cycle_start_date=date(2026, 5, 31), cycle_work_days=2, cycle_off_days=2,
    )
    db_session.add_all([zmo, kft, sched])
    db_session.commit()
    ito = Department(name="ИТО", code="ITO", head_company_id=zmo.id)
    other = Department(name="Сервис", code="SRV", head_company_id=kft.id)
    db_session.add_all([ito, other])
    db_session.commit()
    emp = Employee(
        full_name="Заёмщик Пётр", tab_number="T-0100",
        pay_type="hourly", hour_rate=D("1000"),
        schedule_id=sched.id, department_id=ito.id, default_company_id=zmo.id,
        loan_amount=LOAN, loan_term_months=6, loan_start_date=date(2026, 6, 1),
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return {"emp": emp, "zmo": zmo, "kft": kft, "ito": ito, "other": other,
            "sched": sched, "admin": admin_user}


def _period(db, dept, month, status="draft"):
    p = TimesheetPeriod(department_id=dept.id, year=2026, month=month, status=status)
    db.add(p)
    db.commit()
    return p


def _hours(db, emp, position, company, month, days, hours=8):
    for day in days:
        db.add(TimesheetEntry(
            employee_id=emp.id, position_id=position.id, company_id=company.id,
            work_date=date(2026, month, day), hours=hours,
        ))
    db.commit()


def _adj(db, w, month, kind, amount, position=None):
    db.add(EmployeeAdjustment(
        employee_id=w["emp"].id, position_id=(position or w["emp"].primary_position).id,
        year=2026, month=month, kind=kind, amount=D(amount), reason="тест",
        created_by_id=w["admin"].id,
    ))
    db.commit()


def _rows(db, emp, month):
    entries = db.query(TimesheetEntry).filter(
        TimesheetEntry.work_date >= date(2026, month, 1),
        TimesheetEntry.work_date < date(2026 + month // 12, month % 12 + 1, 1),
    ).all()
    summary = build_payroll_summary(db, [emp], entries, 2026, month)
    return {p.position_id: p for p in summary.employees}


def _loan_row(db, emp, month):
    return _rows(db, emp, month)[emp.primary_position.id]


class TestZeroAccrualMonth:
    def test_no_deduction_and_debt_unchanged(self, db_session, world):
        _period(db_session, world["ito"], 6)
        row = _loan_row(db_session, world["emp"], 6)
        assert row.total_amount == D("0")
        assert row.loan_deduction == D("0"), "заём удержан при нулевом начислении"
        assert row.loan_remaining == LOAN, "остаток уменьшился без выплаты"
        assert row.net_payout == D("0"), "выплата ушла в минус"
        assert row.loan_planned_deduction == SHARE
        assert row.loan_shortfall == SHARE

    def test_next_month_pays_full_share_and_term_grows(self, db_session, world):
        _period(db_session, world["ito"], 6)
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2])
        row = _loan_row(db_session, world["emp"], 7)
        assert row.loan_deduction == SHARE
        assert row.loan_remaining == D("50000"), "пропущенный июнь не должен гасить долг"
        st = loan_status(db_session, world["emp"], 2026, 7)
        assert st.payments_left == 5 and st.payments_left_by_term(2026, 7) == 4

    def test_premium_alone_counts_as_accrual(self, db_session, world):
        _period(db_session, world["ito"], 6)
        _adj(db_session, world, 6, "premium", "5000")
        row = _loan_row(db_session, world["emp"], 6)
        assert row.loan_deduction == D("5000")
        assert row.net_payout == D("0")
        assert row.loan_remaining == D("55000")

    def test_pending_review_month_follows_new_rule(self, db_session, world):
        _period(db_session, world["ito"], 6, status="pending_review")
        row = _loan_row(db_session, world["emp"], 6)
        assert row.loan_deduction == D("0")


class TestLessThanShare:
    def test_takes_only_what_was_accrued(self, db_session, world):
        _period(db_session, world["ito"], 6)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 6, [1])
        row = _loan_row(db_session, world["emp"], 6)
        assert row.total_amount == D("8000")
        assert row.loan_deduction == D("8000"), "удержано больше начисленного"
        assert row.net_payout == D("0")
        assert row.loan_remaining == D("52000")
        assert row.loan_shortfall == D("2000")

    def test_advance_goes_first(self, db_session, world):
        _period(db_session, world["ito"], 6)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 6, [1, 2])
        _adj(db_session, world, 6, "advance", "10000")
        row = _loan_row(db_session, world["emp"], 6)
        assert row.loan_deduction == D("6000"), "после аванса заём не уводит выплату в минус"
        assert row.net_payout == D("0")
        assert row.loan_remaining == D("54000")

    def test_shortfall_noted_in_statement(self, db_session, world):
        from app.services.payroll_statement import build_payroll_statement

        _period(db_session, world["ito"], 6)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 6, [1])
        entries = db_session.query(TimesheetEntry).all()
        st = build_payroll_statement(db_session, [world["emp"]], entries, 2026, 6)
        assert "начисления не хватило" in (st.rows[0].loan_note or "")


class TestPartialAccrual:
    def test_partial_month_enough_for_share_takes_full(self, db_session, world):
        # Два дня из месяца — начисление неполное, но на платёж хватает.
        _period(db_session, world["ito"], 6)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 6, [1, 2])
        row = _loan_row(db_session, world["emp"], 6)
        assert row.total_amount == D("16000")
        assert row.loan_deduction == SHARE
        assert row.loan_shortfall == D("0")
        assert row.net_payout == D("6000")


class TestMultiplePositions:
    def _second(self, db, w, **kw):
        pos = EmployeePosition(
            employee_id=w["emp"].id, is_primary=False, title="Подработка",
            department_id=w["other"].id, company_id=w["kft"].id,
            pay_type="hourly", hour_rate=D("1000"), schedule_id=w["sched"].id, **kw,
        )
        db.add(pos)
        db.commit()
        db.refresh(w["emp"])
        return pos

    def test_loan_position_accrues_other_zero(self, db_session, world):
        emp = world["emp"]
        second = self._second(db_session, world)
        _period(db_session, world["ito"], 6)
        _period(db_session, world["other"], 6)
        _hours(db_session, emp, emp.primary_position, world["zmo"], 6, [1, 2])
        rows = _rows(db_session, emp, 6)
        assert rows[emp.primary_position.id].loan_deduction == SHARE
        assert rows[second.id].loan_deduction == D("0")
        assert rows[second.id].net_payout == D("0"), "заём не должен попасть на чужую позицию"

    def test_loan_position_zero_other_has_accrual(self, db_session, world):
        emp = world["emp"]
        second = self._second(db_session, world)
        _period(db_session, world["ito"], 6)
        _period(db_session, world["other"], 6)
        _hours(db_session, emp, second, world["kft"], 6, [1, 2, 5])
        rows = _rows(db_session, emp, 6)
        loan_row = rows[emp.primary_position.id]
        assert loan_row.loan_deduction == D("0"), "удержано с позиции без начисления"
        assert loan_row.loan_remaining == LOAN
        assert loan_row.net_payout == D("0")
        assert rows[second.id].loan_deduction == D("0"), "заём не переезжает на другую позицию"
        assert rows[second.id].net_payout == D("24000")

    def test_pinned_loan_position_zero_primary_accrues(self, db_session, world):
        emp = world["emp"]
        second = self._second(db_session, world)
        emp.loan_position_id = second.id
        db_session.commit()
        _period(db_session, world["ito"], 6)
        _period(db_session, world["other"], 6)
        _hours(db_session, emp, emp.primary_position, world["zmo"], 6, [1, 2, 5])
        rows = _rows(db_session, emp, 6)
        assert rows[second.id].loan_deduction == D("0")
        assert rows[second.id].loan_remaining == LOAN
        assert rows[emp.primary_position.id].loan_deduction == D("0")
        assert rows[emp.primary_position.id].net_payout == D("24000")


class TestClosedAndUntrackedMonths:
    def test_month_before_first_period_counts_as_deducted(self, db_session, world):
        # Июнь отдел не вёл (периода нет): доля считается удержанной по графику.
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2])
        row = _loan_row(db_session, world["emp"], 7)
        assert row.loan_remaining == D("40000")

    def test_closed_without_snapshot_keeps_old_deduction(self, db_session, world):
        _period(db_session, world["ito"], 6, status="closed")
        june = _loan_row(db_session, world["emp"], 6)
        assert june.loan_deduction == SHARE, "закрытый месяц без снимка не должен пересчитываться"
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2])
        assert _loan_row(db_session, world["emp"], 7).loan_remaining == D("40000")

    def test_closed_with_snapshot_is_a_fact(self, db_session, world):
        june = _period(db_session, world["ito"], 6, status="pending_review")
        close_period(db_session, june, world["admin"])
        row = _loan_row(db_session, world["emp"], 6)
        assert row.loan_deduction == D("0")
        # Условия займа поменяли после закрытия — факт июня прежний.
        world["emp"].loan_term_months = 3
        db_session.commit()
        assert _loan_row(db_session, world["emp"], 6).loan_deduction == D("0")
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2, 5])
        july = _loan_row(db_session, world["emp"], 7)
        assert july.loan_deduction == D("20000")
        assert july.loan_remaining == D("40000")

    def test_manual_override_in_zero_month_is_kept(self, db_session, world):
        _period(db_session, world["ito"], 6)
        db_session.add(LoanDeduction(
            employee_id=world["emp"].id, year=2026, month=6,
            planned_amount=SHARE, actual_amount=D("3000"), created_by_id=world["admin"].id,
        ))
        db_session.commit()
        row = _loan_row(db_session, world["emp"], 6)
        assert row.loan_deduction == D("3000") and row.loan_is_manual


class TestLoanStatusApi:
    def _url(self, w, month=7):
        return f"/api/employees/{w['emp'].id}/loan-status?year=2026&month={month}"

    def test_card_shows_more_payments_than_term(self, client, db_session, world):
        _period(db_session, world["ito"], 6)
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2])
        token = get_token(client, "admin@example.com", "admin123")
        r = client.get(self._url(world), headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payments_left"] == 5
        assert body["payments_left_by_term"] == 4
        assert D(body["remaining_after"]) == D("50000")
        assert body["short_months"] == [
            {"year": 2026, "month": 6, "planned": "10000", "actual": "0"}
        ]

    def test_timekeeper_gets_403(self, client, db_session, world):
        tk = Employee(full_name="Табельщик", email="tk@example.com", role="timekeeper",
                      hashed_password=hash_password("Timekeep1"), must_change_password=False)
        db_session.add(tk)
        db_session.commit()
        token = get_token(client, "tk@example.com", "Timekeep1")
        r = client.get(self._url(world), headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_manager_of_other_department_gets_403(self, client, db_session, world):
        mgr = Employee(full_name="Чужой руководитель", email="mgr@example.com", role="manager",
                       hashed_password=hash_password("Manager11"), must_change_password=False)
        mgr.managed_departments = [world["other"]]
        db_session.add(mgr)
        db_session.commit()
        token = get_token(client, "mgr@example.com", "Manager11")
        r = client.get(self._url(world), headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403


class TestDashboardCache:
    def test_hours_of_loan_holder_invalidate_later_months(self, db_session, world):
        _period(db_session, world["ito"], 6)
        before = current_versions(db_session, 2026, 7)[1]
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 6, [1])
        assert current_versions(db_session, 2026, 7)[1] > before, (
            "правка июня у заёмщика меняет удержание июля — кэш июля должен устареть"
        )

    def test_hours_without_loan_keep_reference(self, db_session, world):
        plain = Employee(
            full_name="Без займа", tab_number="T-0101", pay_type="hourly",
            hour_rate=D("1000"), schedule_id=world["sched"].id,
            department_id=world["ito"].id, default_company_id=world["zmo"].id,
        )
        db_session.add(plain)
        db_session.commit()
        db_session.refresh(plain)
        before = current_versions(db_session, 2026, 7)[1]
        _hours(db_session, plain, plain.primary_position, world["zmo"], 6, [1])
        assert current_versions(db_session, 2026, 7)[1] == before
        assert REFERENCE_KEY  # ключ справочника — общий для всех месяцев


# ── Находки ревью ─────────────────────────────────────────────────────────────

class TestUnopenedMonthIsConsistent:
    """Месяц после начала учёта, в табель которого никто не заходил (периода
    нет — они создаются лениво), считается одинаково и сам по себе, и как
    прошлый для следующего месяца."""

    def test_unopened_month_after_start_follows_rule_both_ways(self, db_session, world):
        _period(db_session, world["ito"], 5)  # учёт отдела начат в мае
        june = _loan_row(db_session, world["emp"], 6)  # июнь никто не открывал
        assert june.loan_deduction == D("0")
        assert june.loan_remaining == LOAN
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2])
        july = _loan_row(db_session, world["emp"], 7)
        assert july.loan_remaining == D("50000"), "история и сам июнь разошлись"

    def test_target_month_before_first_period_keeps_old_rule(self, db_session, world):
        _period(db_session, world["ito"], 7)  # учёт начат в июле
        june = _loan_row(db_session, world["emp"], 6)
        assert june.loan_deduction == SHARE
        assert june.loan_remaining == D("50000")

    def test_department_without_periods_keeps_old_rule(self, db_session, world):
        june = _loan_row(db_session, world["emp"], 6)
        assert june.loan_deduction == SHARE


class TestClosedFactKeepsShortfall:
    def test_skipped_month_stays_listed_after_close(self, db_session, world):
        june = _period(db_session, world["ito"], 6, status="pending_review")
        close_period(db_session, june, world["admin"])
        _period(db_session, world["ito"], 7)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 7, [1, 2])
        st = loan_status(db_session, world["emp"], 2026, 7)
        assert st.remaining_after == D("50000")
        assert st.short_months == ((2026, 6, SHARE, D("0")),), (
            "закрытие месяца не должно прятать пропуск"
        )
        assert not st.is_manual


class TestCardDefaultMonth:
    def test_default_is_last_finished_month(self, client, db_session, world):
        today = date.today()
        cur = date(today.year, today.month, 1)
        prev = (cur - timedelta(days=1)).replace(day=1)
        emp = world["emp"]
        emp.loan_start_date = prev
        db_session.commit()
        for d in (prev, cur):
            db_session.add(TimesheetPeriod(
                department_id=world["ito"].id, year=d.year, month=d.month, status="draft",
            ))
        db_session.commit()
        for day in (1, 2):
            db_session.add(TimesheetEntry(
                employee_id=emp.id, position_id=emp.primary_position.id,
                company_id=world["zmo"].id, work_date=prev.replace(day=day), hours=8,
            ))
        db_session.commit()
        token = get_token(client, "admin@example.com", "admin123")
        r = client.get(f"/api/employees/{emp.id}/loan-status",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["year"], body["month"]) == (prev.year, prev.month)
        # Текущий месяц без часов не должен выглядеть растянутым сроком.
        assert body["short_months"] == []
        assert body["payments_left"] == body["payments_left_by_term"] == 5

    def test_loan_starting_this_month_shows_current(self, client, db_session, world):
        today = date.today()
        cur = date(today.year, today.month, 1)
        world["emp"].loan_start_date = cur
        db_session.commit()
        token = get_token(client, "admin@example.com", "admin123")
        r = client.get(f"/api/employees/{world['emp'].id}/loan-status",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert (r.json()["year"], r.json()["month"]) == (cur.year, cur.month)


class TestPeriodsMoveLoanCache:
    def test_reopen_bumps_reference(self, db_session, world):
        from app.services.timesheet_periods import reopen_period

        june = _period(db_session, world["ito"], 6, status="pending_review")
        close_period(db_session, june, world["admin"])
        before = current_versions(db_session, 2026, 7)[1]
        reopen_period(db_session, june, world["admin"], "проверка кэша")
        db_session.commit()
        assert current_versions(db_session, 2026, 7)[1] > before

    def test_past_month_period_bumps_reference(self, db_session, world):
        before = current_versions(db_session, 2026, 7)[1]
        _period(db_session, world["ito"], 3)
        assert current_versions(db_session, 2026, 7)[1] > before

    def test_current_month_period_keeps_reference(self, db_session, world):
        today = date.today()
        before = current_versions(db_session, today.year, today.month)[1]
        db_session.add(TimesheetPeriod(
            department_id=world["ito"].id, year=today.year, month=today.month, status="draft",
        ))
        db_session.commit()
        assert current_versions(db_session, today.year, today.month)[1] == before


# ── Находки повторного ревью ─────────────────────────────────────────────────

class TestClosedMonthInCard:
    def test_closed_month_state_is_read_from_facts(self, db_session, world):
        june = _period(db_session, world["ito"], 6, status="pending_review")
        close_period(db_session, june, world["admin"])
        st = loan_status(db_session, world["emp"], 2026, 6)
        assert st is not None, "закрытый месяц не должен давать пустое состояние"
        assert st.actual == D("0") and st.remaining_after == LOAN

    def test_card_default_uses_closed_previous_month(self, client, db_session, world):
        today = date.today()
        cur = date(today.year, today.month, 1)
        prev = (cur - timedelta(days=1)).replace(day=1)
        emp = world["emp"]
        emp.loan_start_date = prev
        db_session.commit()
        prev_period = TimesheetPeriod(
            department_id=world["ito"].id, year=prev.year, month=prev.month,
            status="pending_review",
        )
        db_session.add_all([prev_period, TimesheetPeriod(
            department_id=world["ito"].id, year=cur.year, month=cur.month, status="draft",
        )])
        for day in (1, 2):
            db_session.add(TimesheetEntry(
                employee_id=emp.id, position_id=emp.primary_position.id,
                company_id=world["zmo"].id, work_date=prev.replace(day=day), hours=8,
            ))
        db_session.commit()
        close_period(db_session, prev_period, world["admin"])
        token = get_token(client, "admin@example.com", "admin123")
        r = client.get(f"/api/employees/{emp.id}/loan-status",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["year"], body["month"]) == (prev.year, prev.month), (
            "после закрытия прошлого месяца карточка ушла на незаконченный текущий"
        )
        assert body["short_months"] == []
        assert body["payments_left"] == body["payments_left_by_term"] == 5


class TestManualOverrideSurvivesClose:
    def test_manual_month_not_listed_as_shortfall_after_close(self, db_session, world):
        june = _period(db_session, world["ito"], 6)
        _hours(db_session, world["emp"], world["emp"].primary_position, world["zmo"], 6, [1, 2])
        db_session.add(LoanDeduction(
            employee_id=world["emp"].id, year=2026, month=6,
            planned_amount=SHARE, actual_amount=D("3000"), created_by_id=world["admin"].id,
        ))
        db_session.commit()
        june.status = "pending_review"
        db_session.commit()
        close_period(db_session, june, world["admin"])
        _period(db_session, world["ito"], 7)
        st = loan_status(db_session, world["emp"], 2026, 7)
        assert st.remaining_after == D("57000")  # удержано 3 000 вручную
        assert all((y, m) != (2026, 6) for y, m, *_ in st.short_months), (
            "ручная правка после закрытия не должна выглядеть как нехватка начисления"
        )


class TestLazyPeriodKeepsCache:
    def test_later_past_period_does_not_bump_reference(self, db_session, world):
        _period(db_session, world["ito"], 3)
        before = current_versions(db_session, 2026, 7)[1]
        _period(db_session, world["ito"], 5)  # не первый — начало учёта не сдвинулось
        assert current_versions(db_session, 2026, 7)[1] == before
