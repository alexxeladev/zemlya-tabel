"""
ЕДИНЫЙ источник округления и распределения по компаниям (task_distribution_v2 ч.1).

Здесь и только здесь решается, как сумма (или 100%) делится между юрлицами.
Используется ведомостью на экране (`/statement`), Excel-выгрузкой и кнопкой
«Разнести поровну». Фронт зеркалит этот же алгоритм в
`frontend/src/utils/distribution.ts` — при правке менять оба файла.

Алгоритм (метод наибольшего остатка в варианте «остаток — основной компании»):
  1. точная доля каждой компании = total × вес / Σвесов (без округления);
  2. каждая доля округляется до шага (рубли для сумм, сотые для процентов);
  3. нераспределённый остаток = total − Σ округлённых долей;
  4. остаток целиком относится на ОСНОВНУЮ компанию сотрудника (default_company);
     если её нет в наборе — на компанию с наибольшей долей (тай-брейк по id).
Итог: сумма частей РОВНО равна total.

Примеры (из ТЗ):
  350000 на 6 равных долей → 5 × 58333 + основная 58335 = 350000
  100% на 6 компаний       → 5 × 16.67 + основная 16.65 = 100.00
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

#: Шаг округления сумм — целые рубли (как и весь расчёт ЗП).
RUBLE = Decimal("1")
#: Шаг округления процентов — сотые доли процента.
PERCENT_STEP = Decimal("0.01")


def _as_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _remainder_key(weights: dict[int, Decimal], main_key: int | None) -> int:
    """Кому отдать нераспределённый остаток: основной компании, иначе —
    компании с наибольшей долей (при равенстве — с наименьшим id)."""
    if main_key is not None and main_key in weights:
        return main_key
    return min(weights, key=lambda k: (-weights[k], k))


def distribute(
    total: Decimal,
    weights: dict[int, Decimal],
    main_key: int | None = None,
    step: Decimal = RUBLE,
) -> dict[int, Decimal]:
    """Разложить `total` по ключам пропорционально `weights` так, чтобы сумма
    частей была РОВНО равна `total` (с точностью до `step`).

    weights — проценты, часы или любые другие веса; нулевые и отрицательные
    отбрасываются. Если Σвесов ≠ 100, доли нормализуются (распределяется всё).
    """
    positive = {k: _as_decimal(w) for k, w in weights.items()}
    positive = {k: w for k, w in positive.items() if w > _ZERO}
    if not positive:
        return {}

    total = _as_decimal(total).quantize(step, rounding=ROUND_HALF_EVEN)
    weight_sum = sum(positive.values(), _ZERO)
    if total == _ZERO or weight_sum <= _ZERO:
        return {k: _ZERO for k in positive}

    parts = {
        k: (total * w / weight_sum).quantize(step, rounding=ROUND_HALF_EVEN)
        for k, w in positive.items()
    }
    leftover = total - sum(parts.values(), _ZERO)
    if leftover != _ZERO:
        parts[_remainder_key(positive, main_key)] += leftover
    return parts


def split_equally(
    company_ids: list[int] | set[int],
    main_key: int | None = None,
    total: Decimal = _HUNDRED,
    step: Decimal = PERCENT_STEP,
) -> dict[int, Decimal]:
    """«Разнести поровну» (ч.2): поделить `total` (по умолчанию 100%) поровну
    между выбранными компаниями. Сумма долей ровно `total`, остаток — основной
    компании. Проценты ФИКСИРУЮТСЯ: новые компании в справочнике на уже
    сохранённое распределение не влияют.
    """
    ids = sorted(set(company_ids))
    if not ids:
        return {}
    return distribute(total, {cid: Decimal("1") for cid in ids}, main_key, step)


# ── Округление распределения до тысяч (task_it_arm_distribution ч.3) ──────────

#: Шаг округления СУММ РАСПРЕДЕЛЕНИЯ по юрлицам — тысяча рублей (требование
#: финдиректора: затраты по юрлицам круглые). Совпадение шага с округлением
#: «К выплате» (`payout.PAYOUT_ROUNDING_STEP`) — случайность: базой распределения
#: служит «Итого начислено», а оно кратным тысяче не бывает, поэтому Σ долей
#: НЕ равна базе — разница остаётся нераспределённым остатком.
THOUSAND = Decimal("1000")


def distribute_largest_remainder(
    total: Decimal,
    weights: dict[int, Decimal],
    step: Decimal = THOUSAND,
    order: dict[int, int] | None = None,
) -> dict[int, Decimal]:
    """Разложить `total` по весам с округлением ВНИЗ до `step` методом
    «floor + раздача недостающих шагов по наибольшим хвостам».

    Σ долей = floor(total / step) × step, то есть **не больше** `total` и всегда
    кратна шагу. Разница `total − Σ` (0…step−1) — НЕРАСПРЕДЕЛЁННЫЙ ОСТАТОК: он
    не приписывается ни одной компании, его считает и показывает вызывающий
    (`payroll_statement.unallocated_remainder`).

    Только floor, никогда не математическое округление: доли — это затраты
    юрлиц, и переразнесение (приписать больше, чем начислено) недопустимо.
    На проверочном примере (57000 по 104 АРМ) независимое математическое
    округление даёт 58000 — лишнюю тысячу затрат из воздуха.

    Отличается от `distribute` не только шагом, но и способом добора: там
    остаток целиком уходит ОДНОЙ компании (основной), здесь недостающие шаги
    раздаются по одному тем, у кого больше отброшенный хвост.

    Тай-брейк при равных хвостах — `order` (настроенный порядок юрлиц), затем
    id: результат обязан быть одинаковым при каждом пересчёте, иначе суммы
    «плавают» между открытиями ведомости.

    `total <= 0` (нулевое или отрицательное начисление) шага в тысячу не терпит:
    округлять долг в любую сторону одинаково неверно, поэтому такие суммы
    делятся с точностью до рубля обычным `distribute` и остатка не оставляют.
    """
    positive = {k: _as_decimal(w) for k, w in weights.items()}
    positive = {k: w for k, w in positive.items() if w > _ZERO}
    if not positive:
        return {}

    total = _as_decimal(total)
    if total <= _ZERO:
        return distribute(total, positive, None, RUBLE)

    weight_sum = sum(positive.values(), _ZERO)
    exact = {k: total * w / weight_sum for k, w in positive.items()}
    parts = {k: (v / step).to_integral_value(rounding=ROUND_FLOOR) * step
             for k, v in exact.items()}
    remainders = {k: exact[k] - parts[k] for k in positive}

    order = order or {}
    tail = len(order)
    ranked = sorted(
        positive, key=lambda k: (-remainders[k], order.get(k, tail), k)
    )
    # Раздаём только ЦЕЛЫЕ шаги и только те, что помещаются в total целиком:
    # доступно к раздаче floor(total/step)×step − Σfloor-ов. Хвост меньше шага
    # (total не кратен тысяче) не раздаётся никому — это и есть
    # нераспределённый остаток.
    distributable = (total / step).to_integral_value(rounding=ROUND_FLOOR) * step
    full_steps = int((distributable - sum(parts.values(), _ZERO)) / step)
    for k in ranked[:full_steps]:
        parts[k] += step
    return parts
