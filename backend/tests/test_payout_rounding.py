"""
task_payout_rounding: «К выплате» округляется МАТЕМАТИЧЕСКИ до 1000 ₽.

Было — вниз до 100 ₽, хвост всегда ≥ 0. Стало — к ближайшей тысяче, ровно
посередине вверх, и хвост бывает ОБОИХ знаков: округлили вниз — осело в пользу
компании (+), вверх — компания доплатила до тысячи (−, до 500 ₽). Отсюда и
«Эффект округления» на дашборде может выйти отрицательным.
"""
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
from app.services.payout import compute_payout, round_to_payout_step
from tests.conftest import get_token
from tests.test_payroll import MAY_BASIC, MAY_BASIC_WORKDAYS

# ── Unit: математическое округление до 1000 ───────────────────────────────────

class TestRoundToThousand:
    @pytest.mark.parametrize("exact,rounded", [
        ("110407", "110000"),   # вниз — ближе
        ("110700", "111000"),   # вверх — ближе
        ("110500", "111000"),   # ровно посередине — ВВЕРХ
        ("26826", "27000"),
        ("153659", "154000"),
        ("1000", "1000"),       # ровная тысяча не двигается
        ("499", "0"),           # меньше половины шага — вся сумма в хвост
        ("500", "1000"),        # ровно половина шага — вверх
        ("2500", "3000"),       # половина округляется вверх, а не «к чётному»
        ("0", "0"),
    ])
    def test_round(self, exact, rounded):
        assert round_to_payout_step(Decimal(exact)) == Decimal(rounded)

    def test_negative_not_rounded(self):
        """Удержали больше начисленного — это долг, оба направления неверны:
        к ближайшей тысяче долг исчезнет, «от нуля» — вырастет. Не трогаем."""
        assert round_to_payout_step(Decimal("-150")) == Decimal("-150")
        assert round_to_payout_step(Decimal("-1500")) == Decimal("-1500")

    def test_result_is_multiple_of_step(self):
        for value in ("1", "499", "500", "110407", "110500", "153659"):
            assert round_to_payout_step(Decimal(value)) % Decimal("1000") == Decimal("0")


class TestComputePayoutRounding:
    def _payout(self, accrued: str, premium: str = "0", advance: str = "0"):
        return compute_payout(
            accrued_total=Decimal(accrued),
            premium_amount=Decimal(premium),
            kpi_amount=Decimal("0"),
            advance_deduction=Decimal(advance),
            loan_deduction=Decimal("0"),
        )

    def test_rounds_down_with_positive_tail(self):
        """Округлили вниз — хвост положительный, осело в пользу компании."""
        r = self._payout("110407")
        assert r.net_payout == Decimal("110000")
        assert r.net_payout_exact == Decimal("110407")
        assert r.rounding_tail == Decimal("407")

    def test_rounds_up_with_negative_tail(self):
        """Округлили вверх — хвост ОТРИЦАТЕЛЬНЫЙ: компания доплатила до тысячи."""
        r = self._payout("110700")
        assert r.net_payout == Decimal("111000")
        assert r.net_payout_exact == Decimal("110700")
        assert r.rounding_tail == Decimal("-300")

    def test_tail_never_exceeds_half_step(self):
        """Хвост по модулю не больше половины шага — иначе округлили не туда."""
        for accrued in ("0", "50", "499", "500", "26826", "110500", "153659"):
            r = self._payout(accrued)
            assert abs(r.rounding_tail) <= Decimal("500")
            assert r.net_payout_exact - r.net_payout == r.rounding_tail

    def test_intermediate_amounts_stay_exact(self):
        """Округляется только финальная сумма: начисления/удержания — точные."""
        r = self._payout("100000", premium="10407", advance="1")
        assert r.premium_amount == Decimal("10407")
        assert r.total_deductions == Decimal("1")
        assert r.net_payout_exact == Decimal("110406")
        assert r.net_payout == Decimal("110000")

    def test_no_double_rounding(self):
        """Повторный прогон уже округлённой суммы ничего не меняет."""
        once = round_to_payout_step(Decimal("110407"))
        assert round_to_payout_step(once) == once

    def test_sum_of_rounded_differs_from_rounded_sum(self):
        """Σ округлённых ≠ округление(Σ точных): сначала каждого, потом складываем."""
        parts = [self._payout(a) for a in ("1600", "1700", "1800")]
        sum_rounded = sum((p.net_payout for p in parts), Decimal("0"))
        sum_exact = sum((p.net_payout_exact for p in parts), Decimal("0"))
        assert sum_rounded == Decimal("6000")           # 2000 + 2000 + 2000
        assert sum_exact == Decimal("5100")
        assert round_to_payout_step(sum_exact) == Decimal("5000")  # так считать нельзя
        assert sum_exact - sum_rounded == sum((p.rounding_tail for p in parts), Decimal("0"))


# ── Fixtures: три сотрудника с хвостами +407 − 174 − 341 = −108 ───────────────
#
# Набор подобран так, что округление идёт в ОБЕ стороны, а суммарный эффект
# выходит ОТРИЦАТЕЛЬНЫМ: компания за месяц доплатила больше, чем удержала.
# Именно на этом случае и проверяется, что дашборд с минусом не ломается.

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
    """Начисления так, чтобы точные выплаты были 110407 / 26826 / 153659:
    первая округлится вниз, две другие — вверх."""
    client.post("/api/timesheet/adjustments", headers=headers, json={
        "employee_id": workers[0].id, "year": 2026, "month": 5,
        "kind": "premium", "amount": "10407", "reason": "премия"})
    client.post("/api/timesheet/adjustments", headers=headers, json={
        "employee_id": workers[1].id, "year": 2026, "month": 5,
        "kind": "advance", "amount": "73174", "reason": "аванс"})
    client.post("/api/timesheet/adjustments", headers=headers, json={
        "employee_id": workers[2].id, "year": 2026, "month": 5,
        "kind": "premium", "amount": "53659", "reason": "премия"})


# employee_id → (точно, округлено, хвост = точное − округлённое)
_EXPECTED = {
    0: ("110407", "110000", "407"),    # вниз: 407 осело в пользу компании
    1: ("26826", "27000", "-174"),     # вверх: компания доплатила 174
    2: ("153659", "154000", "-341"),   # вверх: компания доплатила 341
}
_TOTAL_EXACT = Decimal("290892")
_TOTAL_ROUNDED = Decimal("291000")     # 110000 + 27000 + 154000
_TOTAL_TAIL = Decimal("-108")          # 407 − 174 − 341: за месяц ПЕРЕПЛАТА


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
        """Итог = Σ округлённых, а не округление(Σ точных).

        Разница здесь видна невооружённым глазом: Σ округлённых = 291000, а
        округление суммы 290892 дало бы 291000 же — поэтому проверяем ещё и
        равенство «итог = точное − хвост», которое ловит подмену формулы.
        """
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/payroll", headers=h).json()
        assert Decimal(data["total_net_payout"]) == _TOTAL_ROUNDED
        assert Decimal(data["total_net_payout_exact"]) == _TOTAL_EXACT
        assert Decimal(data["total_rounding_tail"]) == _TOTAL_TAIL
        assert (
            Decimal(data["total_net_payout"])
            == Decimal(data["total_net_payout_exact"]) - Decimal(data["total_rounding_tail"])
        )

    def test_total_tail_can_be_negative(self, client, admin, workers, company,
                                        calendar_2026, db_session):
        """Суммарный эффект за месяц — со знаком минус, и это не ошибка."""
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/payroll", headers=h).json()
        assert Decimal(data["total_rounding_tail"]) < 0
        assert Decimal(data["total_net_payout"]) > Decimal(data["total_net_payout_exact"])


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
        assert Decimal(data["total_rounding_tail"]) == _TOTAL_TAIL

    def test_distribution_follows_rounded_payout(self, client, admin, workers, company,
                                                 calendar_2026, db_session):
        """База распределения — ОКРУГЛЁННАЯ «К выплате» (task_it_arm_distribution ч.2).

        Раньше распределялось «Итого начислено», и «Σ распред.» не сходилась с
        «К выплате» (110407 против 110000). Теперь по юрлицам разносится ровно
        то, что платим; сам хвост округления (407 ₽) в затраты юрлиц НЕ идёт —
        он остаётся показателем «Эффект округления» на дашборде, что проверяет
        TestDashboardRoundingEffect ниже.
        """
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/timesheet/2026/5/statement", headers=h).json()
        by_id = {r["employee_id"]: r for r in data["rows"]}

        first = by_id[workers[0].id]
        assert Decimal(first["accrued_total"]) == Decimal("110407")
        assert Decimal(first["net_payout"]) == Decimal("110000")
        assert Decimal(first["distribution_total"]) == Decimal("110000")

        for row in data["rows"]:
            # сумма частей по компаниям = «К выплате», а не начисленное
            assert Decimal(row["distribution_total"]) == Decimal(row["net_payout"])
            parts = sum((Decimal(d["amount"]) for d in row["distribution"]), Decimal("0"))
            assert parts == Decimal(row["net_payout"])


# ── Дашборд: эффект округления ────────────────────────────────────────────────

class TestDashboardRoundingEffect:
    def test_effect_is_sum_of_tails(self, client, admin, workers, company,
                                    calendar_2026, db_session):
        h = _auth(client)
        _make_tails(client, h, workers)
        data = client.get("/api/dashboard/2026/5", headers=h).json()
        # Отрицательный: за месяц компания доплатила больше, чем удержала.
        assert Decimal(data["payroll"]["rounding_effect"]) == _TOTAL_TAIL

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
        # Чужому отделу — свой хвост: 100000 + 333 = 100333 → 100000, хвост +333.
        # В цифру менеджера он попасть не должен. Знак у него ПРОТИВОПОЛОЖНЫЙ
        # итогу отдела (−108), поэтому подмена выборки сразу видна по знаку.
        client.post("/api/timesheet/adjustments", headers=ah, json={
            "employee_id": outsider.id, "year": 2026, "month": 5,
            "kind": "premium", "amount": "333", "reason": "премия"})

        mh = _auth(client, "round.manager@example.com", "mgr12345")
        dash = client.get("/api/dashboard/2026/5", headers=mh).json()
        assert Decimal(dash["payroll"]["rounding_effect"]) == _TOTAL_TAIL

        admin_dash = client.get("/api/dashboard/2026/5", headers=ah).json()
        # Разные знаки складываются как числа, а не по модулю: −108 + 333 = 225.
        assert Decimal(admin_dash["payroll"]["rounding_effect"]) == Decimal("225")

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
