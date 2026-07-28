"""
Рабочий день по ГРАФИКУ сотрудника (task_schedule_based_pay).

Точка отсчёта для оплаты — график сотрудника, а не производственный календарь:
  - работа в рабочий день ГРАФИКА (в том числе в календарный праздник) → оклад;
  - выход в свой выходной ПО ГРАФИКУ → отдельная категория «вне графика»
    (коэффициент / фикс-ставка per-employee).

Здесь только определение «рабочий/выходной по графику». Деньги считает
`app.services.payroll`, месячная норма часов — по-прежнему календарная
(`app.services.calendar`), она этим модулем не затрагивается.

Виды графиков:
  - **weekday** (`schedule_type != "shift"`: 5/2, 6/1, …) — рабочие дни недели.
    Явного поля с днями недели в модели Schedule нет, поэтому набор выводится
    из имени «N/M» (N рабочих дней подряд с понедельника, только если N+M=7);
    имя не разобрали → Пн–Пт. Плюс переносы: день, объявленный календарём
    рабочим («рабочая суббота»), считается рабочим днём графика.
  - **cyclic** (`schedule_type == "shift"`: 2/2, 3/3) — цикл от стартовой даты,
    календарь не влияет. Модель Schedule пока не хранит дату старта цикла
    (задача сменных графиков не сделана), поэтому без якоря функция возвращает
    None = «определить нельзя». Payroll для сменных графиков всё равно
    возвращает is_calculable=False, так что в деньги None не попадает.
"""
from __future__ import annotations

import re
from datetime import date

from app.services.calendar import is_workday

# Пн–Пт: набор по умолчанию, если из графика ничего не выводится.
DEFAULT_WORK_WEEKDAYS = frozenset({0, 1, 2, 3, 4})

_PATTERN_RE = re.compile(r"(\d+)\s*[/\\]\s*(\d+)")


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
    """Сменный (цикличный) график — 2/2, 3/3 и т.п."""
    return getattr(schedule, "schedule_type", "standard") == "shift"


def work_weekdays(schedule) -> frozenset[int]:
    """
    Рабочие дни недели weekday-графика (0=Пн … 6=Вс).

    Порядок источников: явное поле `work_weekdays` (появится в задаче графиков) →
    имя вида «N/M» при N+M=7 (N дней подряд с понедельника) → Пн–Пт.
    """
    explicit = getattr(schedule, "work_weekdays", None)
    if explicit:
        return frozenset(int(x) for x in explicit)

    pattern = parse_pattern(getattr(schedule, "name", None))
    if pattern is not None:
        work, rest = pattern
        if 1 <= work <= 7 and work + rest == 7:
            return frozenset(range(work))
    return DEFAULT_WORK_WEEKDAYS


def cycle_start_date(schedule) -> date | None:
    """Дата начала цикла сменного графика. Поля в модели пока нет → None."""
    return getattr(schedule, "cycle_start_date", None)


def is_schedule_work_day(
    schedule,
    work_date: date,
    calendar_data: dict | None = None,
) -> bool | None:
    """
    True — день рабочий по графику сотрудника, False — выходной по графику,
    None — определить нельзя (нет графика / сменный график без даты старта цикла).

    weekday-график: рабочий, если день недели входит в рабочие дни графика ИЛИ
    производственный календарь объявил день рабочим (перенос выходного —
    «рабочая суббота»). Календарный праздник, попавший на рабочий день недели,
    остаётся рабочим днём графика — это и есть суть задачи: такие часы идут
    по окладу, а не ×1,5.

    cyclic-график: рабочий, если по циклу от стартовой даты это смена;
    календарь не учитывается (у сменщика свои выходные).
    """
    if schedule is None:
        return None

    if is_cyclic_schedule(schedule):
        pattern = parse_pattern(getattr(schedule, "name", None))
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
