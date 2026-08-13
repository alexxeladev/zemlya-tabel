from __future__ import annotations

import calendar as _cal
from datetime import date, datetime
from functools import lru_cache

import httpx
from sqlalchemy.orm import Session

from app.models.production_calendars import ProductionCalendar

_XMLCALENDAR_URL = "https://xmlcalendar.ru/data/ru/{year}/calendar.json"


class CalendarFetchError(Exception):
    """Не удалось получить календарь с xmlcalendar.ru"""


# ── Pure parsing functions ────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def parse_days_string(days: str) -> tuple[frozenset[int], frozenset[int]]:
    """
    Парсит строку '1,2,3,8*,9+,10' →
      ({1,2,3,9,10}, {8})  # (нерабочие_дни, сокращённые_дни)

    Кэшируется: `is_workday` / `is_short_day` / `is_holiday` зовутся на каждый
    день каждого сотрудника, и при 200 сотрудниках эта строка разбиралась
    десятки тысяч раз за один запрос табеля (профиль показал здесь ~2 с).
    Ключ кэша — сама строка, поэтому устареть он не может: правка календаря
    даёт другую строку и другой ключ.

    Результат ИММУТАБЕЛЬНЫЙ (frozenset) — его нельзя менять на месте, иначе
    правка расползётся по всем, кто получил тот же кэшированный объект.
    Сравнение с обычным set-ом при этом работает как раньше.
    """
    if not days.strip():
        return frozenset(), frozenset()
    non_working: set[int] = set()
    short_days: set[int] = set()
    for token in days.split(","):
        token = token.strip()
        if not token:
            continue
        if token.endswith("*"):
            short_days.add(int(token[:-1]))
        elif token.endswith("+"):
            non_working.add(int(token[:-1]))
        else:
            non_working.add(int(token))
    return frozenset(non_working), frozenset(short_days)


def get_month_data(calendar_data: dict, month: int) -> dict | None:
    """Извлекает блок месяца из JSON календаря, или None."""
    for m in calendar_data.get("months", []):
        if m.get("month") == month:
            return m
    return None


def is_workday(calendar_data: dict, year: int, month: int, day: int) -> bool:
    """True если день рабочий (включая сокращённые)."""
    month_data = get_month_data(calendar_data, month)
    if month_data is None:
        return date(year, month, day).weekday() < 5
    non_working, _ = parse_days_string(month_data["days"])
    return day not in non_working


def is_short_day(calendar_data: dict, month: int, day: int) -> bool:
    """True если день сокращённый (минус час)."""
    month_data = get_month_data(calendar_data, month)
    if month_data is None:
        return False
    _, short_days = parse_days_string(month_data["days"])
    return day in short_days


def is_holiday(calendar_data: dict, month: int, day: int) -> bool:
    """True если день не рабочий."""
    month_data = get_month_data(calendar_data, month)
    if month_data is None:
        return False
    non_working, _ = parse_days_string(month_data["days"])
    return day in non_working


def is_public_holiday(calendar_data: dict | None, work_date: date) -> bool:
    """
    Нерабочий ПРАЗДНИЧНЫЙ день — в отличие от обычной субботы/воскресенья.

    xmlcalendar праздники отдельно не размечает: 12 июня 2026 стоит в строке
    месяца обычным числом, а маркер `+` означает перенесённый выходной, а не
    праздник. Поэтому признак выводим по дню недели: нерабочий БУДНИЙ день —
    это праздник или перенос (отдыхают все графики), нерабочая суббота или
    воскресенье — обычный выходной пятидневки.

    Краевой случай: праздник, выпавший на выходные (9 мая 2026 — суббота),
    праздником здесь не считается, зато считается компенсирующий перенос на
    будний день (11 мая). Итоговое число оплачиваемых по-праздничному дней
    сходится, сдвигается только конкретная дата.
    """
    if calendar_data is None:
        return False
    if work_date.weekday() >= 5:
        return False
    return is_holiday(calendar_data, work_date.month, work_date.day)


def workdays_in_month(calendar_data: dict, year: int, month: int) -> int:
    """Количество рабочих дней (включая сокращённые)."""
    total = _cal.monthrange(year, month)[1]
    month_data = get_month_data(calendar_data, month)
    if month_data is None:
        return sum(1 for d in range(1, total + 1) if date(year, month, d).weekday() < 5)
    non_working, _ = parse_days_string(month_data["days"])
    return total - len(non_working)


def short_days_in_month(calendar_data: dict, month: int) -> int:
    """Количество сокращённых дней."""
    month_data = get_month_data(calendar_data, month)
    if month_data is None:
        return 0
    _, short_days = parse_days_string(month_data["days"])
    return len(short_days)


def norm_hours_for_period(
    calendar_data: dict, year: int, month: int, hours_per_shift: int
) -> int:
    """Норма часов = workdays × hours_per_shift − short_days."""
    return workdays_in_month(calendar_data, year, month) * hours_per_shift - short_days_in_month(
        calendar_data, month
    )


# ── Network + DB functions ────────────────────────────────────────────────────

async def fetch_calendar_from_remote(year: int) -> dict:
    """Тянет JSON с xmlcalendar.ru. Таймаут 10 секунд."""
    url = _XMLCALENDAR_URL.format(year=year)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        raise CalendarFetchError(f"Не удалось загрузить календарь {year}: {exc}") from exc

    if "year" not in data or "months" not in data:
        raise CalendarFetchError(f"Неверный формат ответа от xmlcalendar.ru для {year}")

    return data


async def ensure_calendar(db: Session, year: int) -> ProductionCalendar:
    """Получить из БД или загрузить с remote и сохранить."""
    existing = db.query(ProductionCalendar).filter_by(year=year).first()
    if existing:
        return existing
    data = await fetch_calendar_from_remote(year)
    cal = ProductionCalendar(year=year, data=data, source="remote")
    db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal


async def reload_calendar(db: Session, year: int) -> ProductionCalendar:
    """Принудительная перезагрузка с remote."""
    data = await fetch_calendar_from_remote(year)
    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    if cal:
        cal.data = data
        cal.source = "remote"
        cal.loaded_at = datetime.utcnow()
    else:
        cal = ProductionCalendar(year=year, data=data, source="remote")
        db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal


def save_calendar_from_dict(
    db: Session, year: int, data: dict, source: str = "manual"
) -> ProductionCalendar:
    """Сохранение календаря из словаря. Upsert по year."""
    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    if cal:
        cal.data = data
        cal.source = source
        cal.loaded_at = datetime.utcnow()
    else:
        cal = ProductionCalendar(year=year, data=data, source=source)
        db.add(cal)
    db.commit()
    db.refresh(cal)
    return cal
