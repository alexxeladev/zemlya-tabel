"""Расчёт вахты: три типа оплаты, база распределения и отсутствие округления.

Проверочные примеры взяты из образцов заказчика (лист «Охрана» ведомости) и
продублированы в acceptance criteria задачи — они держат главные правила
модуля: база распределения включает премию, а суммы не округляются.
"""
from decimal import Decimal

import pytest

from app.services.guard_payroll import (
    GuardHalfResult,
    calculate_guard_row,
    distribute_guard_amount,
)


def _shifts(*days: int) -> set[int]:
    return set(days)


def _all_days(a: int, b: int) -> set[int]:
    return set(range(a, b + 1))


class TestPayTypes:
    """Три типа оплаты (acceptance criteria п.3)."""

    def test_guard_shifts_times_rate(self):
        """Охранник: 15 смен × 4 000 = 60 000."""
        row = calculate_guard_row(
            kind="guard", rate=Decimal("4000"), year=2026, month=8,
            days=_all_days(1, 15),
        )
        assert row.shifts == 15
        assert row.salary == Decimal("60000")

    def test_gbr_shifts_times_rate(self):
        """ГБР: тот же расчёт, ставка выше — 15 × 5 000 = 75 000."""
        row = calculate_guard_row(
            kind="gbr", rate=Decimal("5000"), year=2026, month=8,
            days=_all_days(1, 15),
        )
        assert row.salary == Decimal("75000")

    def test_chief_fixed_salary_half_per_half_month(self):
        """Начальник охраны: фикс-оклад, половина за полмесяца (135 000 / 2)."""
        row = calculate_guard_row(
            kind="chief", rate=Decimal("135000"), year=2026, month=8,
            days=_all_days(1, 15),
        )
        assert row.halves[1].salary == Decimal("67500")
        # Вторая половина не отмечена — за неё ничего.
        assert row.halves[2].salary == Decimal("0")
        assert row.salary == Decimal("67500")

    def test_chief_full_month_is_full_salary(self):
        row = calculate_guard_row(
            kind="chief", rate=Decimal("135000"), year=2026, month=8,
            days=_all_days(1, 31),
        )
        assert row.salary == Decimal("135000")

    def test_chief_partial_half_is_proportional(self):
        """Отмечено 10 дней из 15 → 67 500 × 10/15 = 45 000."""
        row = calculate_guard_row(
            kind="chief", rate=Decimal("135000"), year=2026, month=8,
            days=_all_days(1, 10),
        )
        assert row.halves[1].salary == Decimal("45000")

    def test_chief_second_half_length_differs(self):
        """Вторая половина августа — 16 дней, полная её отработка = половина оклада."""
        row = calculate_guard_row(
            kind="chief", rate=Decimal("135000"), year=2026, month=8,
            days=_all_days(16, 31),
        )
        assert row.halves[2].salary == Decimal("67500")

    def test_empty_slot_does_not_crash(self):
        """Пустой слот: ставка есть, смен нет — всё по нулям."""
        row = calculate_guard_row(
            kind="guard", rate=Decimal("5000"), year=2026, month=8, days=set(),
        )
        assert row.shifts == 0
        assert row.salary == Decimal("0")
        assert row.accrued == Decimal("0")


class TestAccruedAndPayout:
    """«Итого начислено» и «К выплате» (из образца заказчика)."""

    def test_accrued_is_salary_plus_premium_minus_penalty(self):
        row = calculate_guard_row(
            kind="gbr", rate=Decimal("5000"), year=2026, month=8,
            days=_all_days(1, 15),
            premium={1: Decimal("230")}, penalty={1: Decimal("100")},
        )
        assert row.accrued == Decimal("75130")

    def test_net_payout_is_accrued_minus_official(self):
        """Сторожев в образце: 67 500 + 115 − оф. 12 615 = 55 000."""
        row = calculate_guard_row(
            kind="chief", rate=Decimal("135000"), year=2026, month=8,
            days=_all_days(1, 15),
            premium={1: Decimal("115")}, official={1: Decimal("12615")},
        )
        assert row.accrued == Decimal("67615")
        assert row.net_payout == Decimal("55000")

    def test_halves_are_independent(self):
        """Премия и штраф свои у каждой расчётной половины."""
        row = calculate_guard_row(
            kind="guard", rate=Decimal("1000"), year=2026, month=8,
            days=_all_days(1, 31),
            premium={1: Decimal("500"), 2: Decimal("700")},
            penalty={2: Decimal("200")},
        )
        assert row.halves[1].shifts == 15
        assert row.halves[2].shifts == 16
        assert row.halves[1].accrued == Decimal("15500")
        assert row.halves[2].accrued == Decimal("16500")
        assert row.accrued == Decimal("32000")


class TestDistribution:
    """База распределения — «Итого начислено», ВКЛЮЧАЯ премию (п.4, п.6)."""

    def test_rodionov_example(self):
        """Караулов: 15 × 5 000 = 75 000, премия 230, итого 75 230.

        Расклад поста ЗМО 35 / Эксплуатация 60 / Секьюрити 5 →
        26 330,50 / 45 138,00 / 3 761,50. Цифры из листа «Охрана» образца.
        """
        row = calculate_guard_row(
            kind="gbr", rate=Decimal("5000"), year=2026, month=8,
            days=_all_days(1, 15), premium={1: Decimal("230")},
        )
        assert row.salary == Decimal("75000")
        assert row.accrued == Decimal("75230")

        amounts = distribute_guard_amount(
            row.accrued,
            {1: Decimal("35"), 2: Decimal("60"), 3: Decimal("5")},
        )
        assert amounts[1] == Decimal("26330.50")
        assert amounts[2] == Decimal("45138.00")
        assert amounts[3] == Decimal("3761.50")
        assert sum(amounts.values()) == Decimal("75230")

    def test_semencov_example(self):
        """Дозоров: 15 × 4 500 = 67 500, премия 115, итого 67 615, одно юрлицо."""
        row = calculate_guard_row(
            kind="gbr", rate=Decimal("4500"), year=2026, month=8,
            days=_all_days(1, 15), premium={1: Decimal("115")},
        )
        assert row.accrued == Decimal("67615")

        amounts = distribute_guard_amount(row.accrued, {5: Decimal("100")})
        assert amounts == {5: Decimal("67615.00")}

    def test_premium_is_in_the_base(self):
        """Без премии в базе Караулов дал бы 26 250 — так распределять нельзя."""
        amounts = distribute_guard_amount(
            Decimal("75230"), {1: Decimal("35"), 2: Decimal("60"), 3: Decimal("5")}
        )
        assert amounts[1] != Decimal("26250")

    def test_penalty_reduces_the_base(self):
        row = calculate_guard_row(
            kind="guard", rate=Decimal("1000"), year=2026, month=8,
            days=_all_days(1, 10), penalty={1: Decimal("500")},
        )
        assert row.accrued == Decimal("9500")
        amounts = distribute_guard_amount(row.accrued, {1: Decimal("100")})
        assert amounts[1] == Decimal("9500.00")

    def test_no_shares_gives_no_distribution(self):
        assert distribute_guard_amount(Decimal("1000"), {}) == {}


class TestNoRounding:
    """Начисления и распределение НЕ округляются; «к выплате» — вверх до 500
    по каждой половине (правка заказчика)."""

    def test_payout_rounds_up_to_500_accrued_keeps_kopecks(self):
        row = calculate_guard_row(
            kind="guard", rate=Decimal("3333.33"), year=2026, month=8,
            days=_all_days(1, 3), premium={1: Decimal("0.55")},
        )
        assert row.salary == Decimal("9999.99")
        assert row.accrued == Decimal("10000.54")
        assert row.net_payout_exact == Decimal("10000.54")
        # Вверх до 500, а не к ближайшей тысяче, как в основной системе.
        assert row.net_payout == Decimal("10500")
        assert row.rounding_tail == Decimal("-499.46")

    def test_distribution_sum_equals_base_exactly(self):
        amounts = distribute_guard_amount(
            Decimal("10000.54"),
            {1: Decimal("33.33"), 2: Decimal("33.33"), 3: Decimal("33.34")},
        )
        assert sum(amounts.values()) == Decimal("10000.54")

    def test_distribution_is_not_floored_to_thousand(self):
        """В основной системе доли режутся вниз до 1000 — здесь до копейки."""
        amounts = distribute_guard_amount(Decimal("75230"), {1: Decimal("100")})
        assert amounts[1] == Decimal("75230.00")


class TestHalfResultShape:
    def test_halves_cover_the_whole_month(self):
        row = calculate_guard_row(
            kind="guard", rate=Decimal("100"), year=2026, month=2,
            days=_all_days(1, 28),
        )
        assert isinstance(row.halves[1], GuardHalfResult)
        assert row.halves[1].shifts == 15
        assert row.halves[2].shifts == 13
        assert row.shifts == 28

    @pytest.mark.parametrize("month,days_in_month", [(2, 28), (4, 30), (8, 31)])
    def test_month_length_respected(self, month, days_in_month):
        row = calculate_guard_row(
            kind="chief", rate=Decimal("30000"), year=2026, month=month,
            days=_all_days(1, days_in_month),
        )
        assert row.salary == Decimal("30000")
