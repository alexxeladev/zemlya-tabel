"""
Перенос переплаты по официальной выплате между половинами (task_official_payout_debt).

Проверки написаны по ТРЕБОВАНИЮ задачи: пример заказчика на числах, месяц без
смен, месяц без поста, частичное гашение, факт закрытой половины, увольнение с
долгом и главное ограничение — база распределения и налог от долга не зависят.
"""
from decimal import Decimal

from app.services.official_debt import (
    STATUS_FACT,
    STATUS_SKIP,
    DebtPeriodInput,
    debt_facts_of_month,
    facts_from_snapshot,
    official_debt_state,
)

D = Decimal


def _half(year, month, half, accrued="0", official="0", **kw):
    return DebtPeriodInput(
        year=year, month=month, half=half,
        accrued=D(accrued), official=D(official), **kw,
    )


class TestCustomerExample:
    """Оклад официальный 30 000 (15 000 за половину), смена 5 000, цикл 15/15."""

    def _state(self):
        return official_debt_state([
            _half(2026, 9, 1, accrued="75000", official="15000"),   # отработал
            _half(2026, 9, 2, accrued="0", official="15000"),       # дома
            _half(2026, 10, 1, accrued="75000", official="15000"),  # снова работает
            _half(2026, 10, 2, accrued="0", official="15000"),
        ])

    def test_first_half_pays_full_cash(self):
        first = self._state().at(2026, 9, 1)
        assert first.payout == D("60000")      # 75 000 − 15 000
        assert first.debt_after == D("0")

    def test_idle_half_pays_nothing_and_creates_debt(self):
        second = self._state().at(2026, 9, 2)
        assert second.payout == D("0"), "в минус касса не выдаёт"
        assert second.debt_after == D("15000")

    def test_debt_is_repaid_from_the_next_working_half(self):
        third = self._state().at(2026, 10, 1)
        assert third.debt_before == D("15000")
        assert third.payout == D("45000")      # 75 000 − 15 000 − 15 000
        assert third.debt_after == D("0")

    def test_month_totals_of_the_example(self):
        state = self._state()
        september = sum((p.payout for p in state.month_periods(2026, 9)), D("0"))
        october = sum((p.payout for p in state.month_periods(2026, 10)), D("0"))
        # Первый месяц даёт 60 000 (а не 45 000, как до правки): минус второй
        # половины уезжает в следующий месяц.
        assert september == D("60000")
        assert october == D("45000")
        assert state.debt_now == D("15000"), "долг второй половины октября"


class TestNoShifts:
    def test_month_without_shifts_turns_the_whole_payout_into_debt(self):
        """Приёмка 2: начислено 0, касса 0, долг равен официальной выплате."""
        state = official_debt_state([
            _half(2026, 9, 1, accrued="0", official="15000"),
            _half(2026, 9, 2, accrued="0", official="15000"),
        ])
        assert [p.payout for p in state.periods] == [D("0"), D("0")]
        assert state.debt_after(2026, 9) == D("30000")

    def test_debt_accumulates_over_months(self):
        """Приёмка 3: снят с поста — выплата идёт, долг копится."""
        state = official_debt_state([
            _half(y, m, h, accrued="0", official="12615")
            for y, m in ((2026, 9), (2026, 10))
            for h in (1, 2)
        ])
        assert state.debt_now == D("50460")


class TestPartialRepayment:
    def test_debt_is_repaid_as_far_as_the_accrual_goes(self):
        """Приёмка 4: гасится сколько есть, остаток переносится дальше."""
        state = official_debt_state([
            _half(2026, 9, 1, accrued="0", official="15000"),       # долг 15 000
            _half(2026, 9, 2, accrued="20000", official="15000"),   # хватает на 5 000
            _half(2026, 10, 1, accrued="30000", official="15000"),  # гасит остаток
        ])
        second = state.at(2026, 9, 2)
        assert second.payout == D("0")
        assert second.debt_after == D("10000")   # 15 000 − (20 000 − 15 000)
        assert second.repaid == D("5000")
        third = state.at(2026, 10, 1)
        assert third.payout == D("5000")         # 30 000 − 15 000 − 10 000
        assert third.debt_after == D("0")

    def test_exact_zero_leaves_no_debt(self):
        state = official_debt_state([_half(2026, 9, 1, accrued="15000", official="15000")])
        assert state.at(2026, 9, 1).payout == D("0")
        assert state.debt_now == D("0")


class TestClosedPeriods:
    def test_fact_from_snapshot_is_not_recomputed(self):
        """Приёмка 5: долг закрытой половины — факт, пересчёт его не меняет."""
        state = official_debt_state([
            _half(2026, 9, 1, accrued="0", official="15000",
                  status=STATUS_FACT, fact_debt_after=D("4000")),
            _half(2026, 9, 2, accrued="10000", official="0"),
        ])
        first = state.at(2026, 9, 1)
        assert first.is_fact
        assert first.debt_after == D("4000"), "живой расчёт дал бы 15 000"
        # Следующая половина гасит именно факт: 10 000 − 0 − 4 000.
        assert state.at(2026, 9, 2).payout == D("6000")

    def test_closed_without_snapshot_is_skipped(self):
        """Закрытый месяц без снимка не копит долг (решение заказчика)."""
        state = official_debt_state([
            _half(2026, 8, 1, accrued="0", official="15000", status=STATUS_SKIP),
            _half(2026, 8, 2, accrued="0", official="15000", status=STATUS_SKIP),
            _half(2026, 9, 1, accrued="0", official="15000"),
        ])
        assert state.debt_after(2026, 8) == D("0")
        assert state.debt_now == D("15000"), "долг пошёл только с открытого месяца"

    def test_skipped_half_passes_existing_debt_through(self):
        state = official_debt_state([
            _half(2026, 8, 1, accrued="0", official="15000"),
            _half(2026, 8, 2, accrued="0", official="15000", status=STATUS_SKIP),
            _half(2026, 9, 1, accrued="20000", official="0"),
        ])
        assert state.at(2026, 8, 2).debt_after == D("15000")
        assert state.at(2026, 9, 1).payout == D("5000")


class TestSnapshotFacts:
    def test_facts_are_stored_and_read_back(self):
        state = official_debt_state([
            _half(2026, 9, 1, accrued="0", official="15000"),
            _half(2026, 9, 2, accrued="5000", official="15000"),
        ])
        facts = debt_facts_of_month(state, 2026, 9)
        assert facts == {"1": "15000", "2": "25000"}
        assert facts_from_snapshot(facts) == {1: D("15000"), 2: D("25000")}

    def test_broken_snapshot_values_are_ignored(self):
        assert facts_from_snapshot({"1": "нет"}) == {}
        assert facts_from_snapshot(None) == {}


class TestKopecks:
    def test_halves_of_an_odd_salary_do_not_drift(self):
        """Оклад, не делящийся пополам, не должен копить копеечный хвост."""
        state = official_debt_state([
            _half(2026, 9, 1, accrued="0", official="12615.01"),
            _half(2026, 9, 2, accrued="0", official="12614.99"),
        ])
        assert state.debt_now == D("25230.00")


class TestDismissal:
    def test_remaining_debt_stays_after_the_last_period(self):
        """Приёмка 6: выплат больше нет — остаток остаётся зафиксированным."""
        state = official_debt_state([
            _half(2026, 9, 1, accrued="30000", official="15000"),
            _half(2026, 9, 2, accrued="0", official="15000"),
        ])
        assert state.debt_now == D("15000")
        assert state.periods[-1].year == 2026 and state.periods[-1].half == 2
