"""Tests for task_payout_rounding: «К выплате» вниз до 100 ₽ + эффект округления."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.payout import compute_payout, floor_to_payout_step
from tests.conftest import get_token
from tests.test_payroll import MAY_BASIC, MAY_BASIC_WORKDAYS

# ── Unit: округление вниз до 100 ───────────────────────────────────────────────

class TestFloorToHundred:
    @pytest.mark.parametrize("exact,rounded", [
        ("110407", "110400"),
        ("26826", "26800"),
        ("153659", "153600"),
        ("100", "100"),      # ровная сотня не двигается
        ("99", "0"),         # меньше сотни — вся сумма в хвост
        ("0", "0"),
    ])
    def test_floor(self, exact, rounded):
        assert floor_to_payout_step(Decimal(exact)) == Decimal(rounded)

    def test_negative_not_rounded(self):
        """Удержания больше начисленного: floor утянул бы сумму дальше в минус."""
        assert floor_to_payout_step(Decimal("-150")) == Decimal("-150")


class TestComputePayoutRounding:
    def _payout(self, accrued: str, premium: str = "0", advance: str = "0"):
        return compute_payout(
            accrued_total=Decimal(accrued),
            premium_amount=Decimal(premium),
            kpi_amount=Decimal("0"),
            advance_deduction=Decimal(advance),
            loan_deduction=Decimal("0"),
        )

    def test_rounds_down_and_keeps_tail(self):
        r = self._payout("110407")
        assert r.net_payout == Decimal("110400")
        assert r.net_payout_exact == Decimal("110407")
        assert r.rounding_tail == Decimal("7")

    def test_intermediate_amounts_stay_exact(self):
        """Округляется только финальная сумма: начисления/удержания — точные."""
        r = self._payout("100000", premium="10407", advance="1")
        assert r.premium_amount == Decimal("10407")
        assert r.total_deductions == Decimal("1")
        assert r.net_payout_exact == Decimal("110406")
        assert r.net_payout == Decimal("110400")

    def test_tail_never_negative(self):
        for accrued in ("0", "50", "26826", "153659", "153600"):
            r = self._payout(accrued)
            assert r.rounding_tail >= Decimal("0")
            assert r.net_payout <= r.net_payout_exact

    def test_no_double_rounding(self):
        """Повторный прогон уже округлённой суммы ничего не меняет."""
        once = floor_to_payout_step(Decimal("110407"))
        assert floor_to_payout_step(once) == once

    def test_sum_of_rounded_differs_from_rounded_sum(self):
        """Σ округлённых ≠ floor(Σ точных): сначала округляем каждого, потом складываем."""
        parts = [self._payout(a) for a in ("160", "170", "180")]
        sum_rounded = sum((p.net_payout for p in parts), Decimal("0"))
        sum_exact = sum((p.net_payout_exact for p in parts), Decimal("0"))
        assert sum_rounded == Decimal("300")
        assert floor_to_payout_step(sum_exact) == Decimal("500")  # так считать нельзя
        assert sum_exact - sum_rounded == sum((p.rounding_tail for p in parts), Decimal("0"))


# ── Fixtures: три сотрудника с хвостами 7 + 26 + 59 = 92 ───────────────────────

@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Round Dept", code="RD", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="RC", name="Round Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def admin(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Admin", email="round.admin@example.com",
        hashed_password=hash_password("admin123"), role="admin",
        is_active=True, must_change_password=False, is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_BASIC, source="manual")
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


@pytest.fixture
def workers(db_session: Session, company: Company, schedule: Schedule,
            dept: Department) -> list[Employee]:
    """Трое с полной нормой мая: оклад 100000 у каждого."""
    out = []
    for i in range(1, 4):
        emp = Employee(
            full_name=f"Round Worker {i}", is_active=True, rate=Decimal("100000"),
            schedule_id=schedule.id, default_company_id=company.id,
            department_id=dept.id,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        for d in MAY_BASIC_WORKDAYS:
            db_session.add(TimesheetEntry(
                employee_id=emp.id, work_date=date(2026, 5, d),
                company_id=company.id, hours=8,
            ))
        out.append(emp)
    db_session.commit()
    return out


def _auth(client, email="round.admin@example.com", pwd="admin123"):
    return {"Authorization": f"Bearer {get_token(client, email, pwd)}"}


def _make_tails(client, headers, workers) -> None:
    """Начисления так, чтобы точные выплаты были 110407 / 26826 / 153659."""
    client.post("/api/timesheet/adjustments", headers=headers, json={
        "employee_id": workers[0].id, "year": 2026, "month": 5,
        "kind": "premium", "amount": "10407", "reason": "премия"})
    client.post("/api/timesheet/adjustments", headers=headers, json={
        "employee_id": workers[1].id, "year": 2026, "month": 5,
        "kind": "advance", "amount": "73174", "reason": "аванс"})
    client.post("/api/timesheet/adjustments", headers=headers, json={
        "employee_id": workers[2].id, "year": 2026, "month": 5,
        "kind": "premium", "amount": "53659", "reason": "премия"})


_EXPECTED = {
    0: ("110407", "110400", "7"),
    1: ("26826", "26800", "26"),
    2: ("153659", "153600", "59"),
}


# ── Табель (/payroll) ─────────────────────────────────────────────────────────

class TestPayrollRounding:
    def test_rows_rounded_with_tails(self, client, admin, workers, company,
                                     calendar_2026, db_session):
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/payroll", headers=h).json()
        by_id = {e["employee_id"]: e for e in data["employees"]}
        for idx, (exact, rounded, tail) in _EXPECTED.items():
            row = by_id[workers[idx].id]
            assert Decimal(row["net_payout"]) == Decimal(rounded)
            assert Decimal(row["net_payout_exact"]) == Decimal(exact)
            assert Decimal(row["rounding_tail"]) == Decimal(tail)

    def test_total_is_sum_of_rounded(self, client, admin, workers, company,
                                     calendar_2026, db_session):
        """Итог = Σ округлённых, а не floor(Σ точных)."""
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/payroll", headers=h).json()
        expected = Decimal("110400") + Decimal("26800") + Decimal("153600")
        assert Decimal(data["total_net_payout"]) == expected
        assert Decimal(data["total_net_payout_exact"]) == Decimal("290892")
        assert Decimal(data["total_rounding_tail"]) == Decimal("92")
        assert (
            Decimal(data["total_net_payout"])
            == Decimal(data["total_net_payout_exact"]) - Decimal(data["total_rounding_tail"])
        )


# ── Ведомость (/statement) ────────────────────────────────────────────────────

class TestStatementRounding:
    def test_statement_rows_rounded(self, client, admin, workers, company,
                                    calendar_2026, db_session):
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/statement", headers=h).json()
        by_id = {r["employee_id"]: r for r in data["rows"]}
        for idx, (exact, rounded, tail) in _EXPECTED.items():
            row = by_id[workers[idx].id]
            assert Decimal(row["net_payout"]) == Decimal(rounded)
            assert Decimal(row["rounding_tail"]) == Decimal(tail)
        assert Decimal(data["total_rounding_tail"]) == Decimal("92")

    def test_distribution_untouched_by_rounding(self, client, admin, workers, company,
                                                calendar_2026, db_session):
        """База распределения — Итого начислено, округление «к выплате» её не трогает."""
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/statement", headers=h).json()
        by_id = {r["employee_id"]: r for r in data["rows"]}

        first = by_id[workers[0].id]
        assert Decimal(first["accrued_total"]) == Decimal("110407")   # не 110400
        assert Decimal(first["distribution_total"]) == Decimal("110407")

        for row in data["rows"]:
            # сумма частей по компаниям = Итого начислено, а не «к выплате»
            assert Decimal(row["distribution_total"]) == Decimal(row["accrued_total"])
            parts = sum((Decimal(d["amount"]) for d in row["distribution"]), Decimal("0"))
            assert parts == Decimal(row["accrued_total"])


# ── Дашборд: эффект округления ────────────────────────────────────────────────

class TestDashboardRoundingEffect:
    def test_effect_is_sum_of_tails(self, client, admin, workers, company,
                                    calendar_2026, db_session):
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/dashboard/2026/5", headers=h).json()
        assert Decimal(data["payroll"]["rounding_effect"]) == Decimal("92")

    def test_matches_payroll_endpoint(self, client, admin, workers, company,
                                      calendar_2026, db_session):
        """Дашборд и /payroll обязаны показывать один и тот же эффект."""
        h = _auth(client)
        _make_tails(client, h, workers)
        dash = client.get("/api/dashboard/2026/5", headers=h).json()
        payroll = client.get("/api/timesheet/2026/5/payroll", headers=h).json()
        assert (
            Decimal(dash["payroll"]["rounding_effect"])
            == Decimal(payroll["total_rounding_tail"])
        )

    def test_manager_sees_own_department_only(self, client, db_session, admin, workers,
                                              company, schedule, dept, calendar_2026):
        """Manager — эффект только по своему отделу (сотрудник чужого отдела не в счёт)."""
        other_dept = Department(name="Other Dept", code="OD", is_active=True)
        db_session.add(other_dept)
        db_session.commit()
        outsider = Employee(
            full_name="Outsider", is_active=True, rate=Decimal("100000"),
            schedule_id=schedule.id, default_company_id=company.id,
            department_id=other_dept.id,
        )
        db_session.add(outsider)
        db_session.commit()
        db_session.refresh(outsider)
        for d in MAY_BASIC_WORKDAYS:
            db_session.add(TimesheetEntry(employee_id=outsider.id, work_date=date(2026, 5, d),
                                          company_id=company.id, hours=8))
        manager = Employee(
            full_name="Manager", email="round.manager@example.com",
            hashed_password=hash_password("mgr12345"), role="manager",
            is_active=True, must_change_password=False, department_id=dept.id,
            managed_departments=[dept],
        )
        db_session.add(manager)
        db_session.commit()

        ah = _auth(client)
        _make_tails(client, ah, workers)
        # чужому отделу — свой хвост 33, он не должен попасть в цифру менеджера
        client.post("/api/timesheet/adjustments", headers=ah, json={
            "employee_id": outsider.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "33", "reason": "премия"})

        mh = _auth(client, "round.manager@example.com", "mgr12345")
        dash = client.get("/api/dashboard/2026/5", headers=mh).json()
        assert Decimal(dash["payroll"]["rounding_effect"]) == Decimal("92")

        admin_dash = client.get("/api/dashboard/2026/5", headers=ah).json()
        assert Decimal(admin_dash["payroll"]["rounding_effect"]) == Decimal("125")  # 92 + 33

    def test_employee_sees_no_payroll_block(self, client, db_session, admin, workers,
                                            company, dept, calendar_2026):
        emp = Employee(
            full_name="Plain Emp", email="round.emp@example.com",
            hashed_password=hash_password("emp12345"), role="employee",
            is_active=True, must_change_password=False, department_id=dept.id,
        )
        db_session.add(emp)
        db_session.commit()
        h = _auth(client, "round.emp@example.com", "emp12345")
        data = client.get("/api/dashboard/2026/5", headers=h).json()
        assert data["payroll"] is None
