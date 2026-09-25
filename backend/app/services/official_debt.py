"""
Перенос переплаты по официальной выплате между половинами месяца (вахта,
task_official_payout_debt).

Официальная часть уходит через банк ДВУМЯ равными платежами в месяц независимо
от занятости — так платят по трудовому договору. Работа при этом идёт вахтовыми
циклами 15/15, и в нерабочую половину человеку платить нечем: начислено ноль, а
банк перечислил половину оклада. Возникает переплата, которую гасят из
следующих кассовых выплат:

    начислено  = смены × ставка + премия − штраф
    официальная= оф. ЗП / 2 × календарных дней на месте в половине / дней в половине
    к выплате  = начислено − официальная − долг прошлых половин   (не ниже нуля)
    долг после = сколько из этого не покрылось

Пример заказчика (оклад 30 000, смена 5 000, цикл 15/15):

| период        | смен | начислено | банк   | долг до | касса  | долг после |
|---------------|------|-----------|--------|---------|--------|------------|
| М1, 1–15      | 15   | 75 000    | 15 000 | 0       | 60 000 | 0          |
| М1, 16–30     | 0    | 0         | 15 000 | 0       | 0      | 15 000     |
| М2, 1–15      | 15   | 75 000    | 15 000 | 15 000  | 45 000 | 0          |

**Состояние НЕ хранится, а считается пересчётом истории** — та же конструкция,
что у займа (`payout.loan_month_state`): последовательность периодов от старта,
остаток до и после каждого, факты закрытых периодов сильнее пересчёта. Общей
функции с займом нет намеренно: у займа сумма и срок ЗАДАНЫ и делятся на доли, а
здесь долг рождается из самих выплат и заранее неизвестен; сведение их в одну
функцию дало бы набор взаимоисключающих параметров.

**Единица переноса — половина месяца**, а не месяц: выплат в месяце две, и долг
первой половины гасится уже во второй.

**Перенос трогает только «к выплате».** Начислено, налог и база распределения по
юрлицам не зависят от долга вовсе — иначе поехали бы затраты юрлиц за прошлые
месяцы (главное ограничение задачи).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")

#: Половина закрытого периода, у которого ЕСТЬ снимок: долг — факт из снимка,
#: пересчёт его не меняет (как `loan_facts` у займа).
STATUS_FACT = "fact"
#: Закрытый период БЕЗ снимка: половина пропускается — ни выплаты, ни долга.
#: Решение заказчика (25.09.2026): закрытый месяц не должен прирастать долгом
#: задним числом, бухгалтерия его уже закрыла.
STATUS_SKIP = "skip"
#: Обычная половина: считается живьём.
STATUS_OPEN = "open"


@dataclass(frozen=True)
class DebtPeriodInput:
    """Половина месяца на входе расчёта: что начислено и что ушло через банк."""

    year: int
    month: int
    half: int
    accrued: Decimal = _ZERO
    official: Decimal = _ZERO
    status: str = STATUS_OPEN
    #: Долг после этой половины, если он ФАКТ из снимка (`status=fact`).
    fact_debt_after: Decimal | None = None


@dataclass(frozen=True)
class DebtPeriod:
    """Состояние одной половины месяца."""

    year: int
    month: int
    half: int
    accrued: Decimal
    official: Decimal
    debt_before: Decimal
    #: Сколько реально выдаётся из кассы: не ниже нуля.
    payout: Decimal
    debt_after: Decimal
    status: str

    @property
    def repaid(self) -> Decimal:
        """Сколько долга погашено в этой половине."""
        return max(_ZERO, self.debt_before - self.debt_after)

    @property
    def is_fact(self) -> bool:
        return self.status == STATUS_FACT


@dataclass(frozen=True)
class OfficialDebtState:
    """История по половинам и долг на конец каждой из них."""

    periods: tuple[DebtPeriod, ...] = ()

    def at(self, year: int, month: int, half: int) -> DebtPeriod | None:
        for period in self.periods:
            if (period.year, period.month, period.half) == (year, month, half):
                return period
        return None

    def month_periods(self, year: int, month: int) -> tuple[DebtPeriod, ...]:
        return tuple(p for p in self.periods if (p.year, p.month) == (year, month))

    def debt_after(self, year: int, month: int) -> Decimal:
        """Долг на конец месяца — он же входящий для следующего."""
        months = self.month_periods(year, month)
        return months[-1].debt_after if months else _ZERO

    @property
    def debt_now(self) -> Decimal:
        """Долг на конец последней посчитанной половины."""
        return self.periods[-1].debt_after if self.periods else _ZERO


def _money(value: Decimal | None) -> Decimal:
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def official_debt_state(periods: Iterable[DebtPeriodInput]) -> OfficialDebtState:
    """Пересчитать историю долга по половинам.

    Периоды подаются ПО ВОЗРАСТАНИЮ, начиная с первого месяца на посту: до него
    человека в вахте нет и считать нечего (решение заказчика 25.09.2026).

    Закрытая половина со снимком (`STATUS_FACT`) не пересчитывается — её долг
    берётся фактом; закрытая без снимка (`STATUS_SKIP`) пропускается целиком:
    долг проходит через неё, не меняясь.
    """
    out: list[DebtPeriod] = []
    debt = _ZERO
    for item in periods:
        accrued, official = _money(item.accrued), _money(item.official)
        if item.status == STATUS_SKIP:
            out.append(DebtPeriod(
                year=item.year, month=item.month, half=item.half,
                accrued=accrued, official=official, debt_before=debt,
                payout=_ZERO, debt_after=debt, status=STATUS_SKIP,
            ))
            continue
        if item.status == STATUS_FACT and item.fact_debt_after is not None:
            fact = _money(item.fact_debt_after)
            out.append(DebtPeriod(
                year=item.year, month=item.month, half=item.half,
                accrued=accrued, official=official, debt_before=debt,
                payout=max(_ZERO, accrued - official - debt), debt_after=fact,
                status=STATUS_FACT,
            ))
            debt = fact
            continue
        debt_before = debt
        balance = accrued - official - debt_before
        payout = balance if balance > _ZERO else _ZERO
        debt = _ZERO if balance >= _ZERO else -balance
        out.append(DebtPeriod(
            year=item.year, month=item.month, half=item.half,
            accrued=accrued, official=official, debt_before=debt_before,
            payout=payout, debt_after=debt, status=STATUS_OPEN,
        ))
    return OfficialDebtState(periods=tuple(out))


@dataclass(frozen=True)
class DebtSummary:
    """Что долг сделал с «к выплате» за показанный период (месяц или половину)."""

    payout: Decimal = _ZERO
    #: Та же выплата ДО округления вверх до 500 ₽ по половине.
    payout_exact: Decimal = _ZERO
    debt_before: Decimal = _ZERO
    debt_after: Decimal = _ZERO
    repaid: Decimal = _ZERO
    #: Попадает ли период в историю места вообще. False — место ещё НИ РАЗУ не
    #: стояло на посту (или месяц раньше первого поста): долг не начинался, и
    #: официальную выплату за такой месяц начислять не с чего.
    covered: bool = False

    @property
    def rounding_tail(self) -> Decimal:
        """Точная минус округлённая: ≤ 0 — компания доплатила до 500 ₽."""
        return self.payout_exact - self.payout

    @property
    def has_debt(self) -> bool:
        return self.debt_after > _ZERO or self.repaid > _ZERO


def month_summary(
    state: OfficialDebtState | None,
    year: int,
    month: int,
    half: int | None = None,
    rounding=None,
) -> DebtSummary:
    """Сводка по месяцу или одной его половине.

    `rounding` — округление «к выплате» вахты (вверх до 500 ₽ по каждой
    половине, `guard_payroll.round_guard_payout`). Округляется ТОЛЬКО выдаваемая
    из кассы сумма; сам долг считается по точным суммам, иначе округление
    копилось бы в нём из месяца в месяц. Задача утверждала, что округления в
    вахте нет, — в коде оно есть с task_vahta_taxes и здесь не трогается.
    """
    if state is None:
        return DebtSummary()
    periods = [
        p for p in state.month_periods(year, month)
        if half is None or p.half == half
    ]
    if not periods:
        return DebtSummary()
    if all(p.status == STATUS_SKIP for p in periods):
        # Закрытый месяц без снимка НЕ ТРОГАЕМ вовсе: он не копит долг (решение
        # заказчика), но и «к выплате» в нём остаётся прежней. `covered=False`
        # говорит потребителю «считай как раньше»; обнулять выплату закрытого
        # месяца нельзя — это правка закрытого периода (нашло ревью).
        return DebtSummary(debt_before=periods[0].debt_before,
                           debt_after=periods[-1].debt_after)
    payout = sum(
        ((rounding(p.payout) if rounding else p.payout) for p in periods), _ZERO
    )
    return DebtSummary(
        payout=payout,
        payout_exact=sum((p.payout for p in periods), _ZERO),
        debt_before=periods[0].debt_before,
        debt_after=periods[-1].debt_after,
        repaid=sum((p.repaid for p in periods), _ZERO),
        covered=True,
    )


def debt_facts_of_month(
    state: OfficialDebtState, year: int, month: int
) -> dict[str, str]:
    """Факты долга месяца для снимка закрытого периода: {половина: долг после}.

    Хранится строками, как `loan_facts`: JSON снимка не знает Decimal, а копейки
    терять нельзя.
    """
    return {
        str(p.half): str(p.debt_after) for p in state.month_periods(year, month)
    }


def facts_from_snapshot(
    raw: Mapping[str, object] | None,
) -> dict[int, Decimal]:
    """Разобрать факты долга месяца из снимка: {половина: долг после}."""
    if not raw:
        return {}
    out: dict[int, Decimal] = {}
    for half, amount in raw.items():
        try:
            out[int(half)] = Decimal(str(amount))
        except (ValueError, ArithmeticError):
            continue
    return out
