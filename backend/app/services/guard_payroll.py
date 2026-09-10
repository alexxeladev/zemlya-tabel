"""
Расчёт вахты (task_vahta). Чистые функции, Decimal, БЕЗ обращения к БД.

Три правила, которые отличают вахту от основного расчёта и которые нельзя
«унифицировать» обратно:

1. **База распределения по юрлицам — «Итого начислено», включая премию.**
   Караулов: 15 смен × 5 000 = 75 000, премия 230 → 75 230, и расклад поста
   35/60/5 даёт 26 330,50 / 45 138,00 / 3 761,50. Разносить одну зарплату
   (75 000) неверно — цифры разойдутся с ведомостью заказчика.
2. **Суммы НЕ округляются.** Ни «к выплате», ни доли распределения: считаем до
   копейки. В основной системе «к выплате» округляется до ближайшей тысячи
   (`app.services.payout.round_to_payout_step`) — сюда это правило НЕ тащить: в
   вахте до круглого добивают ПРЕМИЕЙ вручную, и округление съело бы ровно ту
   добивку, ради которой премию и ставили.
3. **Проценты берутся от ПОСТА, а не от человека** (`GuardPostShare`). Каскад
   основной системы (месяц → карточка → отдел → часы) к строкам вахты не
   применяется вовсе: платит объект, а не рабочее место.

Месяц ведётся целиком, но денег в нём две выплаты — по расчётным половинам
1–15 и 16–конец месяца, поэтому премия, штраф и официальная выплата свои у
каждой половины, а месячные значения складываются из них.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.guard_assignments import (
    FIRST_HALF_LAST_DAY,
    GUARD_HALVES,
    HALF_FIRST,
    half_of_day,
)
from app.models.guard_posts import GUARD_KIND_CHIEF
from app.services.distribution import distribute

_ZERO = Decimal("0")

#: Шаг сумм вахты — КОПЕЙКА, а не рубль: 75 230 × 35 % = 26 330,50, и округление
#: до рубля здесь уже было бы искажением. См. правило 2 в шапке модуля.
KOPECK = Decimal("0.01")

#: Смена охраны равна суткам — отсюда норма 744 часа при 31 дне и 360 часов
#: факта при 15 сменах (ведомость заказчика). Часов у смены не хранится, число
#: производное.
HOURS_PER_SHIFT = 24


def half_bounds(year: int, month: int, half: int) -> tuple[int, int]:
    """Первое и последнее число расчётной половины месяца."""
    days_in_month = monthrange(year, month)[1]
    if half == HALF_FIRST:
        return 1, min(FIRST_HALF_LAST_DAY, days_in_month)
    return FIRST_HALF_LAST_DAY + 1, days_in_month


def half_length(year: int, month: int, half: int) -> int:
    """Сколько календарных дней в расчётной половине (вторая длиннее первой)."""
    first, last = half_bounds(year, month, half)
    return max(0, last - first + 1)


@dataclass(frozen=True)
class GuardHalfResult:
    """Итог одной расчётной половины месяца."""

    half: int
    shifts: int
    salary: Decimal
    premium: Decimal
    penalty: Decimal
    official_payout: Decimal

    @property
    def accrued(self) -> Decimal:
        """«Итого начислено» половины: зарплата + премия − штраф."""
        return self.salary + self.premium - self.penalty

    @property
    def net_payout(self) -> Decimal:
        """«К выплате» — остаток из кассы после официальной (банковской) части."""
        return self.accrued - self.official_payout


@dataclass(frozen=True)
class GuardRowResult:
    """Итог строки табеля вахты за месяц (сумма двух половин)."""

    halves: dict[int, GuardHalfResult] = field(default_factory=dict)

    @property
    def shifts(self) -> int:
        return sum(h.shifts for h in self.halves.values())

    @property
    def salary(self) -> Decimal:
        return sum((h.salary for h in self.halves.values()), _ZERO)

    @property
    def premium(self) -> Decimal:
        return sum((h.premium for h in self.halves.values()), _ZERO)

    @property
    def penalty(self) -> Decimal:
        return sum((h.penalty for h in self.halves.values()), _ZERO)

    @property
    def official_payout(self) -> Decimal:
        return sum((h.official_payout for h in self.halves.values()), _ZERO)

    @property
    def accrued(self) -> Decimal:
        """База распределения по юрлицам — и только она (правило 1)."""
        return sum((h.accrued for h in self.halves.values()), _ZERO)

    @property
    def net_payout(self) -> Decimal:
        return sum((h.net_payout for h in self.halves.values()), _ZERO)

    @property
    def fact_hours(self) -> int:
        """Факт часов = смены × 24: круглосуточный пост, смена равна суткам."""
        return self.shifts * HOURS_PER_SHIFT


def _money(value: Decimal | None) -> Decimal:
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _half_salary(
    kind: str, rate: Decimal, year: int, month: int, half: int, shifts: int
) -> Decimal:
    """Зарплата половины по типу оплаты поста.

    Охранник и ГБР считаются одинаково — смены × ставка; различие только в
    величине ставки и подписи должности. Начальник охраны получает фикс-оклад,
    половина за полмесяца, ПРОПОРЦИОНАЛЬНО отмеченным дням половины: отмечено
    10 дней из 15 → 135 000 / 2 × 10/15 = 45 000.
    """
    if kind != GUARD_KIND_CHIEF:
        return (rate * shifts).quantize(KOPECK)

    length = half_length(year, month, half)
    if length <= 0 or shifts <= 0:
        return _ZERO
    return (rate / 2 * Decimal(shifts) / Decimal(length)).quantize(KOPECK)


def calculate_guard_row(
    *,
    kind: str,
    rate: Decimal,
    year: int,
    month: int,
    days: set[int],
    premium: dict[int, Decimal] | None = None,
    penalty: dict[int, Decimal] | None = None,
    official: dict[int, Decimal] | None = None,
) -> GuardRowResult:
    """Посчитать строку табеля вахты за месяц.

    `days` — числа месяца, в которые человек выходил (пустое множество — это
    незанятый пост, законное состояние: считается по нулям и не падает).
    `premium` / `penalty` / `official` — суммы ПО ПОЛОВИНАМ, ключ 1 или 2.
    """
    rate = _money(rate)
    premium = premium or {}
    penalty = penalty or {}
    official = official or {}

    days_in_month = monthrange(year, month)[1]
    valid_days = {d for d in days if 1 <= d <= days_in_month}

    halves: dict[int, GuardHalfResult] = {}
    for half in GUARD_HALVES:
        shifts = sum(1 for d in valid_days if half_of_day(d) == half)
        halves[half] = GuardHalfResult(
            half=half,
            shifts=shifts,
            salary=_half_salary(kind, rate, year, month, half, shifts),
            premium=_money(premium.get(half)),
            penalty=_money(penalty.get(half)),
            official_payout=_money(official.get(half)),
        )
    return GuardRowResult(halves=halves)


def distribute_guard_amount(
    amount: Decimal, shares: dict[int, Decimal]
) -> dict[int, Decimal]:
    """Разнести сумму по юрлицам согласно процентам ПОСТА, без округления.

    Тот же `distribute` из `app.services.distribution`, что и везде в системе,
    но с шагом в КОПЕЙКУ: суммы вахты не округляются (правило 2). Сумма долей
    ровно равна `amount`; остаток последней копейки уходит юрлицу с наибольшей
    долей — основной компании сотрудника здесь нет, распределение принадлежит
    посту, а не человеку.
    """
    if not shares:
        return {}
    return distribute(_money(amount), shares, main_key=None, step=KOPECK)


def norm_hours_for_month(year: int, month: int) -> int:
    """Норма часов круглосуточного поста — ВСЕ часы месяца (744 при 31 дне)."""
    return monthrange(year, month)[1] * HOURS_PER_SHIFT


def norm_days_for_month(year: int, month: int) -> int:
    """Норма дней — календарные дни месяца (пост работает без выходных)."""
    return monthrange(year, month)[1]
