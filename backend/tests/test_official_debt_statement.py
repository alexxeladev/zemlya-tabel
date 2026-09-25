"""
Долг по официальной выплате в ведомости (task_official_payout_debt).

Чистое правило проверено в `test_official_debt.py`; здесь — что оно доехало до
ведомости целиком: перенос между месяцами, месяц без поста, факт закрытой
половины, неизменность базы распределения и налога.
"""
from decimal import Decimal

import pytest

from app.models.employees import Employee
from app.models.position_terms import TERMS_BEGINNING
from app.services.guard_duty import create_assignment
from app.services.payroll_statement import build_payroll_statement
from app.services.position_terms import set_effective_from
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
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
#: Оклад официальный 30 000 — ровно пример из постановки заказчика.
SALARY = Decimal("30000")
#: Ставка экипажа ГБР из фикстуры — 5 000 за смену, 15 смен = 75 000.
NEXT_YEAR, NEXT_MONTH = (YEAR, MONTH + 1) if MONTH < 12 else (YEAR + 1, 1)


def _admin(db) -> Employee:
    admin = Employee(full_name="Админ долга", email="officialdebt@example.com",
                     role="admin", is_active=True, is_system_admin=True)
    db.add(admin)
    db.commit()
    return admin


def _official(position, salary=SALARY):
    set_effective_from(position, TERMS_BEGINNING)
    position.is_official = True
    position.official_salary = salary


def _row(db_session, employee, year=YEAR, month=MONTH):
    statement = build_payroll_statement(db_session, [employee], [], year, month)
    return statement.rows[0]


@pytest.fixture
def worked_first_half(db_session, gbr_place, rodionov):
    """Пример заказчика: 15 смен в первой половине, оклад официальный 30 000."""
    _official(rodionov.primary_position)
    create_assignment(
        db_session, year=YEAR, month=MONTH, place=gbr_place,
        position=rodionov.primary_position, days=FIRST_HALF,
    )
    db_session.commit()
    return rodionov


class TestCustomerExample:
    """Приёмка 1: первый месяц — касса 60 000, второй — 45 000, долг переходит."""

    def test_first_month_pays_the_working_half_in_full(
        self, db_session, worked_first_half
    ):
        row = _row(db_session, worked_first_half)
        assert row.accrued_total == Decimal("75000")
        assert row.deductions == Decimal("30000"), "банк платит обе половины"
        # 1-я половина 75 000 − 15 000 = 60 000; 2-я — ничего, долг уехал дальше.
        assert row.net_payout == Decimal("60000")
        assert row.official_debt_after == Decimal("15000")

    def test_next_month_repays_the_debt(
        self, db_session, gbr_place, worked_first_half
    ):
        create_assignment(
            db_session, year=NEXT_YEAR, month=NEXT_MONTH, place=gbr_place,
            position=worked_first_half.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        assert row.official_debt_before == Decimal("15000")
        # 75 000 − 15 000 (банк) − 15 000 (долг) = 45 000.
        assert row.net_payout == Decimal("45000")
        assert row.official_debt_repaid == Decimal("15000")
        # Вторая половина второго месяца снова нерабочая — и снова даёт долг
        # 15 000: в ровном цикле 15/15 переплата «в пути» постоянно.
        assert row.official_debt_after == Decimal("15000")

    def test_accrual_and_tax_do_not_depend_on_the_debt(
        self, db_session, gbr_place, worked_first_half
    ):
        """Приёмка 7: база распределения и налог от переноса не зависят."""
        before = _row(db_session, worked_first_half)
        create_assignment(
            db_session, year=NEXT_YEAR, month=NEXT_MONTH, place=gbr_place,
            position=worked_first_half.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        after = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        # Месяц с долгом и месяц без него считают начисление и налог одинаково.
        assert after.accrued_total == before.accrued_total
        assert after.guard_tax_amount == before.guard_tax_amount
        assert sum(a.amount for a in after.distribution) == sum(
            a.amount for a in before.distribution
        )


class TestMonthWithoutShifts:
    """Приёмка 2: смен нет, выплата есть, касса ноль, долг равен выплате."""

    def test_idle_month_on_post(self, db_session, gbr_place, rodionov):
        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=set(),
        )
        db_session.commit()
        row = _row(db_session, rodionov)
        assert row.accrued_total == _ZERO
        assert row.deductions == Decimal("30000")
        assert row.net_payout == _ZERO, "в минус касса не выдаёт"
        assert row.official_debt_after == Decimal("30000")


class TestMonthWithoutPost:
    """Приёмка 3: сняли с поста, место действует — выплата видна, долг копится."""

    def test_month_after_the_post_still_pays_through_the_bank(
        self, db_session, gbr_place, worked_first_half
    ):
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        # Строк вахты в этом месяце нет вовсе, но банк заплатил.
        assert row.deductions == Decimal("30000")
        assert row.accrued_total == _ZERO, "начисленное не появляется из воздуха"
        assert row.net_payout == _ZERO
        # Долг прошлой половины (15 000) плюс обе половины этого месяца.
        assert row.official_debt_after == Decimal("45000")

    def test_tax_is_charged_on_every_official_payout(
        self, db_session, worked_first_half
    ):
        """Решение заказчика: налог начисляется на ВСЕ официальные выплаты.

        Разносится он ОБЫЧНЫМ КАСКАДОМ: места в этом месяце нет, процентов
        вахты спросить не у кого, и строка идёт общим путём ведомости. Здесь у
        сотрудника каскада нет вовсе, поэтому весь налог оседает в
        нераспределённом остатке; с дефолтом отдела он разошёлся бы по юрлицам
        (на деве так и происходит) — это проверяет соседний тест.
        """
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        assert row.guard_tax_amount > _ZERO
        assert sum(a.amount for a in row.distribution) == _ZERO
        assert row.unallocated_remainder == row.guard_tax_amount

    def test_tax_follows_the_cascade_when_the_department_has_shares(
        self, db_session, companies, guard_dept, worked_first_half
    ):
        """С дефолтом отдела налог межвахтового месяца расходится по юрлицам."""
        from app.models.company_shares import DepartmentCompanyShare

        db_session.add(DepartmentCompanyShare(
            department_id=guard_dept.id, company_id=companies["ZMO"].id,
            percent=Decimal("100"),
        ))
        db_session.commit()
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        assert row.guard_tax_amount > _ZERO
        assert sum(a.amount for a in row.distribution) > _ZERO
        assert row.distribution_source == "department"


class TestPartialRepayment:
    """Приёмка 4: начисления не хватило — гасим сколько есть, остаток дальше."""

    def test_debt_is_repaid_partially(self, db_session, gbr_place, worked_first_half):
        assignment = create_assignment(
            db_session, year=NEXT_YEAR, month=NEXT_MONTH, place=gbr_place,
            position=worked_first_half.primary_position, days={1, 2, 3, 4},
        )
        assert assignment is not None
        db_session.commit()
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        # 1-я половина: 20 000 − 15 000 − 15 000 = −10 000 → касса 0, долг 10 000.
        # 2-я: смен нет, ещё 15 000 сверху.
        assert row.net_payout == _ZERO
        assert row.official_debt_after == Decimal("25000")
        assert row.official_debt_repaid == Decimal("5000")


class TestClosedPeriod:
    """Приёмка 5: долг закрытой половины — факт снимка, пересчёт его не меняет."""

    def test_debt_of_a_closed_month_comes_from_the_snapshot(
        self, db_session, gbr_place, worked_first_half
    ):
        from app.services.timesheet_periods import close_period, get_or_create_period

        admin = _admin(db_session)
        period = get_or_create_period(
            db_session, worked_first_half.primary_position.department_id, YEAR, MONTH
        )
        period.status = "pending_review"
        db_session.commit()
        close_period(db_session, period, admin)
        db_session.commit()

        snapshot_debt = _row(db_session, worked_first_half).official_debt_after
        assert snapshot_debt == Decimal("15000")

        # Меняем условия задним числом: живой пересчёт дал бы другой долг,
        # но закрытый месяц читается из снимка и не двигается.
        worked_first_half.primary_position.official_salary = Decimal("60000")
        db_session.commit()
        assert _row(db_session, worked_first_half).official_debt_after == snapshot_debt

    def test_next_month_starts_from_the_snapshot_debt(
        self, db_session, gbr_place, worked_first_half
    ):
        from app.services.timesheet_periods import close_period, get_or_create_period

        admin = _admin(db_session)
        period = get_or_create_period(
            db_session, worked_first_half.primary_position.department_id, YEAR, MONTH
        )
        period.status = "pending_review"
        db_session.commit()
        close_period(db_session, period, admin)
        db_session.commit()
        # Оклад задним числом вырос — но долг закрытого месяца это не двигает,
        # поэтому следующий месяц гасит ровно 15 000 из снимка.
        worked_first_half.primary_position.official_salary = Decimal("60000")
        create_assignment(
            db_session, year=NEXT_YEAR, month=NEXT_MONTH, place=gbr_place,
            position=worked_first_half.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        assert row.official_debt_before == Decimal("15000")


class TestNoOfficialSalary:
    def test_unofficial_place_is_untouched(self, db_session, gbr_place, rodionov):
        """Неофициальное место долга не имеет — ведомость как раньше."""
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        row = _row(db_session, rodionov)
        assert row.deductions == _ZERO
        assert row.official_debt_after == _ZERO
        assert row.net_payout == Decimal("75000")


class TestDismissal:
    """Приёмка 6: увольнение с долгом — остаток зафиксирован, виден с датой."""

    def test_status_shows_the_frozen_debt(self, db_session, worked_first_half):
        from datetime import date

        from app.services.official_debt_history import debt_status

        rows = debt_status(db_session, worked_first_half, YEAR, MONTH)
        assert len(rows) == 1
        assert rows[0]["debt"] == Decimal("15000")
        assert rows[0]["closed_on"] is None and rows[0]["is_final"] is False

        worked_first_half.primary_position.dismissal_date = date(YEAR, MONTH, 20)
        db_session.commit()
        closed = debt_status(db_session, worked_first_half, YEAR, MONTH)[0]
        assert closed["is_final"] is True
        assert closed["closed_on"] == date(YEAR, MONTH, 20)
        # Уволенному банк во второй половине платит уже не весь период, поэтому
        # и долг меньше полной половины — важно, что он НЕ обнуляется.
        assert closed["debt"] > _ZERO

    def test_status_is_empty_without_posts(self, db_session, rodionov):
        from app.services.official_debt_history import debt_status

        _official(rodionov.primary_position)
        db_session.commit()
        assert debt_status(db_session, rodionov, YEAR, MONTH) == []


class TestReplacementWithinMonth:
    """Две строки одного места (замена на посту) не удваивают выплату.

    Состояние долга — на РАБОЧЕЕ МЕСТО, а строк у него в месяце бывает две.
    Показать в обеих одну и ту же сумму значит удвоить её в итогах зоны,
    подвала и Excel — они складывают строки (нашло ревью).
    """

    def test_screen_rows_split_the_place_payout(
        self, db_session, gbr_place, guard_post, rodionov
    ):
        from app.services.guard_month import build_guard_month

        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days={1, 2, 3, 4, 5},
        )
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=guard_post,
            position=rodionov.primary_position, days={6, 7, 8, 9, 10},
        )
        db_session.commit()
        month = build_guard_month(db_session, _admin(db_session), YEAR, MONTH)
        rows = [r for z in month.zones for c in z.cards for r in c.rows]
        assert len(rows) == 2
        # Сумма строк равна выплате МЕСТА, а не удвоенной выплате.
        statement_row = _row(db_session, rodionov)
        assert sum((r.net_payout or _ZERO) for r in rows) == statement_row.net_payout
        assert month.total_net_payout == statement_row.net_payout

    def test_halves_are_split_too(
        self, db_session, gbr_place, guard_post, rodionov
    ):
        from app.services.guard_month import build_guard_month

        _official(rodionov.primary_position)
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days={1, 2, 3},
        )
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=guard_post,
            position=rodionov.primary_position, days={20, 21, 22},
        )
        db_session.commit()
        admin = _admin(db_session)
        month_rows = {
            r.id: r for z in build_guard_month(db_session, admin, YEAR, MONTH).zones
            for c in z.cards for r in c.rows
        }
        for half in (1, 2):
            month = build_guard_month(db_session, admin, YEAR, MONTH, half=half)
            rows = [r for z in month.zones for c in z.cards for r in c.rows]
            # Сверяем не с собственной суммой подвала (это было бы X == X), а с
            # тем, что строка показывает в режиме месяца: доля строки за месяц
            # обязана быть суммой её долей по половинам.
            for row in rows:
                half_value = next(
                    h.net_payout for h in month_rows[row.id].halves if h.half == half
                )
                assert row.net_payout == half_value
        for row_id, row in month_rows.items():
            assert row.net_payout == sum((h.net_payout or _ZERO) for h in row.halves), (
                f"строка {row_id}: месяц не равен сумме половин"
            )


class TestMasking:
    """Табельщику долг не отдаётся: по нему восстанавливается оф. зарплата."""

    def test_debt_is_masked_for_timekeeper(self, db_session, worked_first_half):
        from app.services.finance_masking import mask_payroll_summary
        from app.services.payroll_statement import build_payroll_summary

        summary = build_payroll_summary(db_session, [worked_first_half], [], YEAR, MONTH)
        assert summary.employees[0].official_debt_after == Decimal("15000")
        masked = mask_payroll_summary(summary)
        row = masked.employees[0]
        assert row.official_debt_after == _ZERO
        assert row.official_debt_before == _ZERO
        assert row.official_debt_repaid == _ZERO

    def test_screen_gives_timekeeper_no_debt(self, db_session, worked_first_half, users):
        from app.services.guard_month import build_guard_month

        month = build_guard_month(db_session, users["timekeeper"], YEAR, MONTH)
        rows = [r for z in month.zones for c in z.cards for r in c.rows]
        assert rows, "табельщик видит смены"
        assert all(r.official_debt_after is None for r in rows)


class TestKopecksAndVersions:
    def test_odd_salary_halves_do_not_drift(self, db_session, gbr_place, rodionov):
        """Оклад, не делящийся пополам: долг за месяц без смен равен ему ровно.

        Проверяется ЧЕРЕЗ реальное деление оклада (`official_half_payout`), а не
        на заранее подготовленных половинах.
        """
        _official(rodionov.primary_position, Decimal("30001"))
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=set(),
        )
        db_session.commit()
        row = _row(db_session, rodionov)
        assert row.deductions == Decimal("30001.00")
        assert row.official_debt_after == Decimal("30001.00")

    def test_salary_change_inside_the_month_is_counted_by_segments(
        self, db_session, gbr_place, rodionov
    ):
        """Оф. зарплата версионируется: половина со сменой считается отрезками."""
        from datetime import date

        from app.services.position_terms import set_effective_from

        position = rodionov.primary_position
        _official(position, Decimal("30000"))
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=position, days=set(),
        )
        db_session.commit()
        before = _row(db_session, rodionov).official_debt_after
        # С 20-го числа оклад вдвое больше — долг обязан вырасти, но не вдвое.
        set_effective_from(position, date(YEAR, MONTH, 20))
        position.official_salary = Decimal("60000")
        db_session.commit()
        after = _row(db_session, rodionov).official_debt_after
        assert before < after < before * 2


class TestIdleMonthCapacity:
    """Чем месяц БЕЗ поста может гасить долг — ровно тем, что выдаёт касса."""

    def _premium(self, db_session, employee, position_id, amount="40000"):
        from app.models.employee_adjustments import EmployeeAdjustment

        db_session.add(EmployeeAdjustment(
            employee_id=employee.id, position_id=position_id,
            year=NEXT_YEAR, month=NEXT_MONTH, kind="premium",
            amount=Decimal(amount), reason="наследство демо-данных",
        ))
        db_session.commit()

    def test_premium_without_position_counts_too(
        self, db_session, worked_first_half
    ):
        """Запись с position_id IS NULL ведомость относит к основной позиции.

        Значит и гасить долг она обязана: иначе касса деньги выдала, а долг
        вырос на всю банковскую выплату (нашло ревью).
        """
        self._premium(db_session, worked_first_half, None)
        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        assert row.premium_amount == Decimal("40000")
        assert row.official_debt_repaid > _ZERO
        # Долг прошлой половины (15 000) + две половины месяца (30 000) минус
        # то, что реально погашено премией.
        assert row.official_debt_after < Decimal("45000")

    def test_loan_deduction_lowers_what_can_repay_the_debt(
        self, db_session, worked_first_half
    ):
        """Заём на охранном месте (наследство) тоже уменьшает кассу."""
        from datetime import date

        position = worked_first_half.primary_position
        self._premium(db_session, worked_first_half, position.id)
        worked_first_half.loan_amount = Decimal("120000")
        worked_first_half.loan_term_months = 12
        worked_first_half.loan_start_date = date(YEAR, MONTH, 1)
        worked_first_half.loan_position_id = position.id
        db_session.commit()

        row = _row(db_session, worked_first_half, NEXT_YEAR, NEXT_MONTH)
        # «Аванс/Удержано» строки = банковская выплата 30 000 + доля займа.
        assert row.deductions > Decimal("30000"), "заём удерживается общим путём"
        loan = row.deductions - Decimal("30000")
        # Погашено долга не больше, чем осталось в кассе после займа.
        assert row.official_debt_repaid <= row.premium_amount - loan
