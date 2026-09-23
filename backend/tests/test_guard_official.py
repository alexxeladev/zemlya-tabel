"""Официальная зарплата охранника и вычисляемая выплата (task_guard_form_rate_official).

Две проверки задачи, которые нельзя потерять:

* **выплата вычисляется**, а не вводится: половина оф. зарплаты в каждую
  половину месяца, пропорционально КАЛЕНДАРНЫМ дням на рабочем месте (смены
  тут ни при чём — вахта круглосуточная);
* **охранное место без строки вахты в месяце — «не на посту», 0 ₽**: в общий
  расчёт оно не идёт ни при каком графике, потому что ставки у него больше нет.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.position_terms import TERMS_BEGINNING
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.schedules import Schedule
from app.services.position_terms import set_effective_from
from app.services.dashboard import build_dashboard
from app.services.guard_duty import (
    create_assignment,
    official_by_assignment,
    official_month_payouts,
    replace_on_post,
)
from app.services.guard_month import build_guard_month
from app.services.guard_payroll import official_half_payout
from app.services.payroll_statement import build_payroll_statement
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
    ALL_DAYS,
    FIRST_HALF,
    MONTH,
    YEAR,
    _auth,
    companies,
    crew,
    gbr_place,
    guard_dept,
    guard_post,
    other_dept,
    rodionov,
    site,
    users,
    zone,
)

_ZERO = Decimal("0")
SALARY = Decimal("25230")
# Снятие признака действует с даты (task_stage3_historicity): с 1-го числа
# месяца теста, иначе оно началось бы со следующего месяца и август не задело.
FROM_MONTH = f"{YEAR}-{MONTH:02d}-01"


def _official(position: EmployeePosition, salary=SALARY) -> None:
    # Официальная зарплата версионируется (task_stage3_historicity): у
    # существующего места без даты она действовала бы со следующего месяца.
    set_effective_from(position, TERMS_BEGINNING)
    position.is_official = True
    position.official_salary = salary


def _admin(db) -> Employee:
    admin = Employee(full_name="Админ вахты", email="guardofficial@example.com",
                     role="admin", is_active=True, is_system_admin=True)
    db.add(admin)
    db.commit()
    return admin


# ── Чистый расчёт выплаты ─────────────────────────────────────────────────────

class TestOfficialHalfPayout:
    def test_full_half_gives_exactly_half_the_salary(self):
        """Весь месяц на месте → по половине зарплаты в каждую половину."""
        assert official_half_payout(SALARY, YEAR, MONTH, 1, 15) == Decimal("12615.00")
        assert official_half_payout(SALARY, YEAR, MONTH, 2, 16) == Decimal("12615.00")

    def test_part_of_the_half_is_proportional(self):
        """Принят 10-го: в первой половине 6 дней из 15."""
        assert official_half_payout(SALARY, YEAR, MONTH, 1, 6) == Decimal("5046.00")

    def test_no_days_no_payout(self):
        assert official_half_payout(SALARY, YEAR, MONTH, 1, 0) == _ZERO

    def test_no_salary_no_payout(self):
        assert official_half_payout(None, YEAR, MONTH, 1, 15) == _ZERO
        assert official_half_payout(_ZERO, YEAR, MONTH, 1, 15) == _ZERO

    def test_kopecks_are_kept(self):
        """Округление до копейки, дальше не округляем (правило вахты)."""
        assert official_half_payout(
            Decimal("30000"), YEAR, MONTH, 1, 7
        ) == Decimal("7000.00")
        assert official_half_payout(
            Decimal("25231"), YEAR, MONTH, 1, 7
        ) == Decimal("5887.23")


# ── Выплата рабочего места за месяц ───────────────────────────────────────────

class TestMonthPayout:
    def test_full_month_equals_the_salary(self, db_session, rodionov):
        _official(rodionov.primary_position)
        db_session.commit()
        payouts = official_month_payouts(rodionov.primary_position, YEAR, MONTH)
        assert payouts == {1: Decimal("12615.00"), 2: Decimal("12615.00")}
        assert sum(payouts.values()) == SALARY

    def test_hired_on_the_tenth(self, db_session, rodionov):
        """Принят 10-го → первая половина 6/15, вторая целиком."""
        position = rodionov.primary_position
        _official(position)
        position.hire_date = date(YEAR, MONTH, 10)
        db_session.commit()
        payouts = official_month_payouts(position, YEAR, MONTH)
        assert payouts[1] == Decimal("5046.00")
        assert payouts[2] == Decimal("12615.00")

    def test_dismissed_on_the_twentieth(self, db_session, rodionov):
        """Уволен 20-го → вторая половина 5 дней из 16 (16–20 включительно)."""
        position = rodionov.primary_position
        _official(position)
        position.dismissal_date = date(YEAR, MONTH, 20)
        db_session.commit()
        payouts = official_month_payouts(position, YEAR, MONTH)
        assert payouts[1] == Decimal("12615.00")
        assert payouts[2] == Decimal("3942.19")

    def test_not_official_gives_nothing(self, db_session, rodionov):
        payouts = official_month_payouts(rodionov.primary_position, YEAR, MONTH)
        assert payouts == {1: _ZERO, 2: _ZERO}

    def test_empty_slot_gives_nothing(self):
        assert official_month_payouts(None, YEAR, MONTH) == {1: _ZERO, 2: _ZERO}

    def test_two_rows_in_a_month_do_not_double_the_payout(
        self, db_session, gbr_place, guard_post, rodionov
    ):
        """Замена внутри месяца — две строки, но банк платит ОДИН раз."""
        _official(rodionov.primary_position)
        db_session.commit()
        first = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=set(range(1, 11)),
        )
        second = create_assignment(
            db_session, year=YEAR, month=MONTH, place=guard_post,
            position=rodionov.primary_position, days=set(range(11, 16)),
        )
        db_session.commit()
        payouts = official_by_assignment([first, second])
        total = sum(sum(p.values()) for p in payouts.values())
        assert total == SALARY
        # Первая половина делится по сменам: 10 и 5 дней → 2/3 и 1/3.
        assert payouts[first.id][1] == Decimal("8410.00")
        assert payouts[second.id][1] == Decimal("4205.00")
        # Во второй половине смен нет ни у одной строки — выплата на первой.
        assert payouts[first.id][2] == Decimal("12615.00")
        assert payouts[second.id][2] == _ZERO


# ── Налог и «к выплате» ───────────────────────────────────────────────────────

class TestTaxAndPayout:
    def test_tax_is_payout_times_rate_by_half(self, db_session, gbr_place, rodionov):
        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=ALL_DAYS,
        )
        db_session.commit()
        row = build_guard_month(db_session, _admin(db_session), YEAR, MONTH) \
            .zones[0].cards[0].rows[0]
        assert [h.official_payout for h in row.halves] == [
            Decimal("12615.00"), Decimal("12615.00"),
        ]
        # Ставка по умолчанию 40 %.
        assert [h.tax for h in row.halves] == [Decimal("5046.00"), Decimal("5046.00")]
        assert row.tax == Decimal("10092.00")
        # «К выплате» половины = начислено − выплата, вверх до 500 ₽.
        # 15 × 5 000 = 75 000 − 12 615 = 62 385 → 62 500.
        assert row.halves[0].net_payout == Decimal("62500")
        # 16 × 5 000 = 80 000 − 12 615 = 67 385 → 67 500.
        assert row.halves[1].net_payout == Decimal("67500")
        assert row.net_payout == Decimal("130000")

    def test_unofficial_row_has_no_payout_and_no_tax(
        self, db_session, gbr_place, rodionov
    ):
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=ALL_DAYS,
        )
        db_session.commit()
        row = build_guard_month(db_session, _admin(db_session), YEAR, MONTH) \
            .zones[0].cards[0].rows[0]
        assert row.official_payout == _ZERO
        assert row.tax == _ZERO
        assert row.is_official is False
        assert row.distribution_base == row.accrued

    def test_statement_takes_the_computed_payout(
        self, db_session, gbr_place, rodionov
    ):
        """Ведомость берёт ту же выплату: «удержано» = оф. выплата."""
        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=ALL_DAYS,
        )
        db_session.commit()
        row = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0]
        assert row.deductions == SALARY
        assert row.guard_tax_amount == Decimal("10092.00")
        assert row.accrued_total == Decimal("155000")


# ── Руками выплату не ввести ──────────────────────────────────────────────────

class TestPayoutIsReadOnly:
    def test_api_ignores_official_fields(
        self, client, db_session, users, gbr_place, rodionov
    ):
        """Прямой запрос к API тоже не задаёт выплату — полей нет в схеме."""
        _official(rodionov.primary_position)
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=ALL_DAYS,
        )
        db_session.commit()
        resp = client.patch(
            f"/api/vahta/assignments/{assignment.id}",
            json={"official_payout_h1": "99999", "is_official": False,
                  "premium_h1": "100"},
            headers=_auth(client, "admin"),
        )
        assert resp.status_code == 200, resp.text
        row = build_guard_month(db_session, _admin(db_session), YEAR, MONTH) \
            .zones[0].cards[0].rows[0]
        # Премия сохранилась, выплата осталась вычисленной, признак — с места.
        assert row.premium == Decimal("100")
        assert row.official_payout == SALARY
        assert row.is_official is True


# ── Снятие признака ───────────────────────────────────────────────────────────

class TestRemovalConfirmation:
    @pytest.fixture
    def staff(self, db_session, gbr_place, rodionov):
        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=ALL_DAYS,
        )
        db_session.commit()
        return rodionov.primary_position

    def test_warns_with_months_and_sums(self, client, db_session, users, staff):
        resp = client.patch(
            f"/api/vahta/staff/{staff.id}",
            json={"is_official": False, "terms_effective_from": FROM_MONTH}, headers=_auth(client, "admin"),
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "guard_official_removal_confirmation_required"
        assert detail["months"] == [
            {"year": YEAR, "month": MONTH, "amount": "25230.00"}
        ]
        db_session.expire_all()
        assert db_session.get(EmployeePosition, staff.id).is_official is True

    def test_confirmed_removal_zeroes_the_payout(
        self, client, db_session, users, staff
    ):
        resp = client.patch(
            f"/api/vahta/staff/{staff.id}", params={"confirm": True},
            json={"is_official": False, "terms_effective_from": FROM_MONTH}, headers=_auth(client, "admin"),
        )
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        position = db_session.get(EmployeePosition, staff.id)
        assert position.is_official is False
        assert position.official_salary is None
        row = build_guard_month(db_session, _admin(db_session), YEAR, MONTH) \
            .zones[0].cards[0].rows[0]
        assert row.official_payout == _ZERO
        assert row.tax == _ZERO

    def test_transfer_out_clears_the_flag_and_names_the_sums(
        self, client, db_session, users, staff, other_dept
    ):
        """Перевод из охраны гасит признак и называет обнуляемые выплаты.

        Одно «Продолжить?» не может подтверждать два разных действия: раньше
        снятие признака вместе с переводом показывало текст только про выплаты,
        а перевод проходил молча (нашло второе ревью).
        """
        url = f"/api/vahta/staff/{staff.id}"
        resp = client.patch(
            url, json={"department_id": other_dept.id, "is_official": False,
                  "terms_effective_from": FROM_MONTH},
            headers=_auth(client, "admin"),
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "guard_transfer_out_confirmation_required"
        assert detail["official_months"] == [
            {"year": YEAR, "month": MONTH, "amount": "25230.00"}
        ]

        resp = client.patch(
            url, params={"confirm": True},
            json={"department_id": other_dept.id, "amount": "4000"},
            headers=_auth(client, "admin"),
        )
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        position = db_session.get(EmployeePosition, staff.id)
        # Признак и зарплата на обычной позиции не остаются: показывать и
        # править их там негде, а строки вахты прошлых месяцев считали бы по ним.
        assert position.department_id == other_dept.id
        assert position.is_official is False
        assert position.official_salary is None

    def test_salary_is_required_when_official(self, client, db_session, users, staff):
        resp = client.patch(
            f"/api/vahta/staff/{staff.id}",
            json={"official_salary": None}, headers=_auth(client, "admin"),
        )
        assert resp.status_code == 422
        assert "официальную зарплату" in resp.json()["detail"]


# ── Охранное место без строки вахты ───────────────────────────────────────────

class TestNotOnPost:
    @pytest.fixture
    def with_schedule(self, db_session, rodionov):
        """Охранное место с графиком и ставкой — «как раньше» бы заплатило."""
        schedule = Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8,
                            is_active=True)
        db_session.add(schedule)
        db_session.commit()
        position = rodionov.primary_position
        position.schedule_id = schedule.id
        position.shift_rate = Decimal("5000")
        db_session.commit()
        return position

    def test_statement_row_is_zero(self, db_session, with_schedule, rodionov):
        row = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0]
        assert row.accrued_total == _ZERO
        assert row.net_payout == _ZERO
        assert row.fact_hours == _ZERO
        # Строка расчётная: ноль — полный ответ, а не «карточка не заполнена».
        assert row.is_calculable is True
        assert row.distribution_total == _ZERO

    def test_dashboard_does_not_pay_it(self, db_session, with_schedule, rodionov):
        dashboard = build_dashboard(db_session, _admin(db_session), YEAR, MONTH)
        assert dashboard.payroll is not None
        assert dashboard.payroll.total == _ZERO
        assert dashboard.hours.total_hours == _ZERO

    def test_official_payout_needs_a_post(self, db_session, rodionov):
        """Не на посту — выплаты и налога нет, даже если место официальное.

        Банк платит, пока человек стоит на посту: выплата считается по строкам
        месяца. Решение зафиксировано тестом, потому что из формулы «половина
        зарплаты в каждую половину» оно прямо не следует — см. отчёт по задаче.
        """
        _official(rodionov.primary_position)
        db_session.commit()
        row = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0]
        assert row.deductions == _ZERO
        assert row.guard_tax_amount == _ZERO

    def test_payout_covers_the_whole_month_of_a_posted_place(
        self, db_session, gbr_place, rodionov
    ):
        """Смены только в первой половине — выплата всё равно за обе.

        «Дни на месте» — календарные, а не смены: во второй половине человек на
        месте числится, и банк платит ему половину зарплаты.
        """
        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        row = build_guard_month(db_session, _admin(db_session), YEAR, MONTH) \
            .zones[0].cards[0].rows[0]
        assert [h.official_payout for h in row.halves] == [
            Decimal("12615.00"), Decimal("12615.00"),
        ]
        # Во второй половине начислений нет — «к выплате» уходит в минус, и
        # такой долг НЕ округляется (правило вахты).
        assert row.halves[1].net_payout == Decimal("-12615.00")

    def test_premiums_and_loan_survive_without_a_post(
        self, db_session, users, rodionov
    ):
        """«Не на посту» обнуляет ЗАРАБОТОК, но не премии и не заём.

        Правило вахты про закрытый общий ввод прямо говорит: сами данные запрет
        не трогает, в месяцы без назначения они участвуют в расчёте. Ревью
        поймало, что первая версия обнуляла и их — из ведомости пропадали
        премии и удержание займа, а остаток займа продолжал уменьшаться.
        """
        from datetime import date

        from app.models.employee_adjustments import EmployeeAdjustment

        db_session.add(EmployeeAdjustment(
            employee_id=rodionov.id, position_id=rodionov.primary_position.id,
            year=YEAR, month=MONTH, kind="premium", amount=Decimal("10000"),
            reason="за объект", created_by_id=users["admin"].id,
        ))
        rodionov.loan_amount = Decimal("60000")
        rodionov.loan_term_months = 10
        rodionov.loan_start_date = date(YEAR, MONTH, 1)
        db_session.commit()

        row = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0]
        assert row.base_salary == _ZERO          # заработка нет
        assert row.premium_amount == Decimal("10000")
        # Удержание займа (60 000 / 10 мес) осталось в ведомости.
        assert row.deductions == Decimal("6000")
        assert row.accrued_total == Decimal("10000")
        assert row.net_payout == Decimal("4000")

    def test_row_on_a_post_is_paid_as_usual(
        self, db_session, with_schedule, gbr_place, rodionov
    ):
        """Та же позиция со строкой вахты считается вахтой, а не по нулям."""
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        row = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0]
        assert row.accrued_total == Decimal("75000")
