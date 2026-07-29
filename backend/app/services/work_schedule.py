"""
График сотрудника — единственный источник правды о том, какие дни рабочие,
сколько часов длится смена и какая у сотрудника месячная норма
(task_schedule_based_pay + task_shift_schedules).

Два вида графиков (`Schedule.schedule_type`):

  - **weekday** («по дням недели», legacy-значение `standard`): 5/2, 6/1,
    вс–чт, вт–сб. Рабочие дни определяются днём недели (`work_weekdays`);
    производственный календарь убирает праздники и добавляет переносы
    («рабочая суббота»).
  - **cyclic** («скользящий цикл», legacy-значение `shift`): 2/2, 3/3.
    Рабочие дни определяются циклом от стартовой даты `cycle_start_date`
    (`cycle_work_days` смен подряд + `cycle_off_days` выходных подряд).
    Производственный календарь на цикл НЕ влияет — смена в праздник это
    обычная смена. «Смена 1» и «Смена 2» — два графика с разными стартовыми
    датами (сдвиг фазы).

Ключевое различие двух понятий «рабочий день»:

  - `is_schedule_work_day` — **оплата**. Праздник, попавший на рабочий день
    недели, остаётся рабочим днём графика (часы по окладу, а не ×1,5);
    выход в свой выходной по графику → категория «вне графика».
  - `is_planned_work_day` — **план**: норма часов, автозаполнение, превью.
    Здесь праздник производственного календаря рабочим днём НЕ является.

Деньги считает `app.services.payroll`, он берёт норму отсюда.
"""
from __future__ import annotations

import calendar as _cal
import re
from datetime import date
from decimal import Decimal

from app.services.calendar import is_public_holiday, is_short_day, is_workday

# ── Типы графиков ─────────────────────────────────────────────────────────────
SCHEDULE_TYPE_WEEKDAY = "weekday"
SCHEDULE_TYPE_CYCLIC = "cyclic"
SCHEDULE_TYPES = (SCHEDULE_TYPE_WEEKDAY, SCHEDULE_TYPE_CYCLIC)

# Значения до task_shift_schedules — принимаются на входе и нормализуются.
_LEGACY_TYPES = {"standard": SCHEDULE_TYPE_WEEKDAY, "shift": SCHEDULE_TYPE_CYCLIC}

# Пн–Пт: набор по умолчанию, если из графика ничего не выводится.
DEFAULT_WORK_WEEKDAYS = frozenset({0, 1, 2, 3, 4})

_ZERO = Decimal("0")
_ONE = Decimal("1")

_PATTERN_RE = re.compile(r"(\d+)\s*[/\\]\s*(\d+)")


def normalize_schedule_type(value: str | None) -> str:
    """`standard`/`weekday` → weekday, `shift`/`cyclic` → cyclic, иначе weekday."""
    if not value:
        return SCHEDULE_TYPE_WEEKDAY
    value = str(value).strip().lower()
    if value in SCHEDULE_TYPES:
        return value
    return _LEGACY_TYPES.get(value, SCHEDULE_TYPE_WEEKDAY)


def parse_pattern(name: str | None) -> tuple[int, int] | None:
    """«5/2» → (5, 2); «2/2» → (2, 2). Не разобрали → None."""
    if not name:
        return None
    match = _PATTERN_RE.search(str(name))
    if match is None:
        return None
    work, rest = int(match.group(1)), int(match.group(2))
    if work <= 0 or rest < 0:
        return None
    return work, rest


def is_cyclic_schedule(schedule) -> bool:
    """Сменный (скользящий) график — 2/2, 3/3 и т.п."""
    if schedule is None:
        return False
    return normalize_schedule_type(getattr(schedule, "schedule_type", None)) == (
        SCHEDULE_TYPE_CYCLIC
    )


def work_weekdays(schedule) -> frozenset[int]:
    """
    Рабочие дни недели weekday-графика (0=Пн … 6=Вс).

    Порядок источников: явное поле `work_weekdays` → имя вида «N/M» при N+M=7
    (N дней подряд с понедельника: 5/2 → Пн–Пт, 6/1 → Пн–Сб) → Пн–Пт.
    """
    explicit = getattr(schedule, "work_weekdays", None)
    if explicit:
        days = frozenset(int(x) for x in explicit if 0 <= int(x) <= 6)
        if days:
            return days

    pattern = parse_pattern(getattr(schedule, "name", None))
    if pattern is not None:
        work, rest = pattern
        if 1 <= work <= 7 and work + rest == 7:
            return frozenset(range(work))
    return DEFAULT_WORK_WEEKDAYS


def cycle_pattern(schedule) -> tuple[int, int] | None:
    """
    Паттерн цикла сменного графика: (смен подряд, выходных подряд).

    Источники: явные поля `cycle_work_days`/`cycle_off_days` → имя «N/M».
    """
    work = getattr(schedule, "cycle_work_days", None)
    rest = getattr(schedule, "cycle_off_days", None)
    if work is not None and rest is not None and int(work) > 0 and int(rest) >= 0:
        return int(work), int(rest)
    return parse_pattern(getattr(schedule, "name", None))


def cycle_start_date(schedule) -> date | None:
    """Дата начала цикла сменного графика (анкер фазы)."""
    return getattr(schedule, "cycle_start_date", None)


def schedule_issue(schedule) -> str | None:
    """
    Почему по графику нельзя посчитать план/деньги. None — всё в порядке.
    Используют payroll (`reason_if_not_calculable`) и автозаполнение.
    """
    if schedule is None:
        return "Не задан график"
    if not is_cyclic_schedule(schedule):
        return None
    if cycle_start_date(schedule) is None:
        return "Не задана дата начала цикла сменного графика"
    pattern = cycle_pattern(schedule)
    if pattern is None or pattern[0] + pattern[1] <= 0:
        return "Не задан паттерн сменного графика"
    return None


# ── Рабочий день по графику (оплата) ──────────────────────────────────────────

def is_schedule_work_day(
    schedule,
    work_date: date,
    calendar_data: dict | None = None,
) -> bool | None:
    """
    True — день рабочий по графику сотрудника, False — выходной по графику,
    None — определить нельзя (нет графика / сменный график без анкера цикла).

    weekday-график: рабочий, если день недели входит в рабочие дни графика ИЛИ
    производственный календарь объявил день рабочим (перенос выходного —
    «рабочая суббота»). Календарный праздник, попавший на рабочий день недели,
    остаётся рабочим днём графика — такие часы идут по окладу, а не ×1,5.

    cyclic-график: рабочий, если по циклу от стартовой даты это смена;
    календарь не учитывается (у сменщика свои выходные).
    """
    if schedule is None:
        return None

    if is_cyclic_schedule(schedule):
        pattern = cycle_pattern(schedule)
        start = cycle_start_date(schedule)
        if pattern is None or start is None:
            return None
        work, rest = pattern
        cycle = work + rest
        if cycle <= 0:
            return None
        return ((work_date - start).days % cycle) < work

    if work_date.weekday() in work_weekdays(schedule):
        return True
    # Перенос выходного: календарь сделал этот день рабочим → он рабочий и по графику.
    if calendar_data is not None and is_workday(
        calendar_data, work_date.year, work_date.month, work_date.day
    ):
        return True
    return False


def is_off_schedule_day(
    schedule,
    work_date: date,
    calendar_data: dict | None = None,
) -> bool:
    """
    Выход вне графика: день ТОЧНО выходной по графику сотрудника.
    None («определить нельзя») трактуется как «не вне графика» — часы
    остаются обычными, лишней доплаты не возникает.
    """
    return is_schedule_work_day(schedule, work_date, calendar_data) is False


# ── Плановый рабочий день (норма, автозаполнение, превью) ─────────────────────

def is_planned_work_day(
    schedule,
    work_date: date,
    calendar_data: dict | None = None,
) -> bool:
    """
    День входит в ПЛАН сотрудника: месячную норму, автозаполнение, превью.

    В отличие от `is_schedule_work_day` праздник производственного календаря
    плановым рабочим днём НЕ является (в него не автозаполняем и в норму не
    считаем), хотя фактический выход в него оплачивается по окладу.

    cyclic: строго по циклу, календарь не влияет.

    weekday:
      - день недели графика → рабочий, кроме объявленных календарём нерабочими
        будних дней (праздники и переносы: 12 июня, 4 ноября, новогодние);
        обычная суббота/воскресенье календаря рабочим дням графика не мешает —
        у 6/1 суббота рабочая, у «вс–чт» воскресенье рабочее;
      - день не из графика → рабочий только если календарь объявил рабочим
        выходной день недели (перенос, «рабочая суббота»).
    """
    if schedule is None:
        return False

    if is_cyclic_schedule(schedule):
        return is_schedule_work_day(schedule, work_date, calendar_data) is True

    weekday = work_date.weekday()
    in_schedule = weekday in work_weekdays(schedule)

    if calendar_data is None:
        return in_schedule

    calendar_workday = is_workday(
        calendar_data, work_date.year, work_date.month, work_date.day
    )
    if in_schedule:
        # Нерабочий будний день календаря — это праздник или перенос: отдыхают все.
        # Нерабочая суббота/воскресенье — обычные выходные «пятидневки», для
        # 6/1 или «вс–чт» это их штатные рабочие дни.
        return calendar_workday or weekday >= 5
    # Не рабочий день недели графика: считаем только перенос на выходной.
    return calendar_workday and weekday >= 5


# ── Категория дня для расчёта ─────────────────────────────────────────────────

DAY_PLANNED = "planned"          # плановый рабочий день → оклад + переработка сверх смены
DAY_HOLIDAY = "holiday"          # нерабочий праздничный день календаря → праздничные
DAY_OFF_SCHEDULE = "off_schedule"  # свой выходной по графику → вне графика


def day_category(
    schedule,
    work_date: date,
    calendar_data: dict | None = None,
) -> str:
    """
    К какой категории оплаты относятся часы этого дня. Единственный источник
    правды для payroll, ведомости и Excel — три категории не пересекаются.

      - `planned`      — день входит в план сотрудника: часы до дневной нормы
                         идут в оклад, превышение смены — в переработку;
      - `holiday`      — день в план не входит и является нерабочим праздничным
                         по производственному календарю: ВСЕ часы дня —
                         праздничные (ТК требует повышенной оплаты);
      - `off_schedule` — день в план не входит и праздником не является (свой
                         законный выходной): ВСЕ часы дня — «вне графика».

    Приоритет праздника над выходным намеренный: если сотрудник вышел 12 июня,
    это работа в праздник, а не «подработка в выходной», независимо от того,
    выходной это по его графику или нет.

    У сменщика праздник, попавший НА ЕГО СМЕНУ, остаётся `planned` — норма по
    циклу (180 ч) эту смену уже содержит, вынести её в праздничные значило бы
    урезать оклад (см. task_schedule_based_pay).

    График не задан → `planned`: противопоставить нечему, часы обычные.
    """
    if schedule is None:
        return DAY_PLANNED
    if is_planned_work_day(schedule, work_date, calendar_data):
        return DAY_PLANNED
    if is_public_holiday(calendar_data, work_date):
        return DAY_HOLIDAY
    return DAY_OFF_SCHEDULE


def planned_work_dates(
    schedule,
    year: int,
    month: int,
    calendar_data: dict | None = None,
) -> list[date]:
    """Плановые рабочие дни месяца по графику (для нормы, автозаполнения, превью)."""
    if schedule is None:
        return []
    days_in_month = _cal.monthrange(year, month)[1]
    all_dates = (date(year, month, day) for day in range(1, days_in_month + 1))
    return [d for d in all_dates if is_planned_work_day(schedule, d, calendar_data)]


def shift_hours_for_date(
    schedule,
    work_date: date,
    calendar_data: dict | None = None,
) -> Decimal:
    """
    Длительность смены в конкретный день: `hours_per_shift`, у предпраздничного
    (сокращённого) дня на час меньше.

    Сокращение — только для weekday-графиков: у сменщика смена 12 часов
    независимо от производственного календаря.
    """
    if schedule is None:
        return _ZERO
    hours = Decimal(str(schedule.hours_per_shift))
    if (
        not is_cyclic_schedule(schedule)
        and calendar_data is not None
        and is_short_day(calendar_data, work_date.month, work_date.day)
    ):
        hours -= _ONE
    return max(_ZERO, hours)


def norm_days_for_schedule(
    schedule,
    year: int,
    month: int,
    calendar_data: dict | None = None,
) -> int | None:
    """Плановых рабочих дней (смен) в месяце по графику. None — график не задан."""
    if schedule is None or schedule_issue(schedule) is not None:
        return None
    return len(planned_work_dates(schedule, year, month, calendar_data))


def norm_hours_for_schedule(
    schedule,
    year: int,
    month: int,
    calendar_data: dict | None = None,
) -> Decimal | None:
    """
    Месячная норма часов ПО ГРАФИКУ (task_shift_schedules).

      - weekday: Σ по плановым дням (смена, у сокращённого дня смена − 1).
        Для 5/2 совпадает с прежней календарной формулой
        `workdays × hours_per_shift − short_days`.
      - cyclic: смены месяца по циклу × часы смены; производственный календарь
        не участвует (2/2 по 12 ч в июне 2026 → 15 × 12 = 180).

    Без календаря weekday-график считается по «голым» дням недели (праздники
    не вычтены) — этого достаточно для превью и справочных колонок Excel, но
    payroll в таком случае деньги считать отказывается: норма была бы завышена.

    None — норму определить нельзя (нет графика, сменный без анкера цикла).
    """
    if schedule is None or schedule_issue(schedule) is not None:
        return None
    return sum(
        (
            shift_hours_for_date(schedule, d, calendar_data)
            for d in planned_work_dates(schedule, year, month, calendar_data)
        ),
        _ZERO,
    )
