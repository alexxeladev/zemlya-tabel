"""Ограничение попыток входа и журнал неудачных входов (task_stage2_access п.2.6).

Решение заказчика: лимит — на УЧЁТКУ (email), с любых адресов:
`LOGIN_MAX_FAILURES` неудач за `LOGIN_LOCK_MINUTES` — вход в неё закрыт до
конца окна. Раньше может снять админ («Снять блокировку» или сброс пароля).

Состояние блокировки в БД не хранится — оно считается по журналу
`login_failures` за окно от точки отсчёта:
  точка = max(начало окна, последний успешный вход, снятие блокировки админом).
Попытки ВО ВРЕМЯ блокировки пишутся в журнал, но в порог не идут.

Единственное место этого правила: вход (`routers/auth.login`) и пометка в
списке сотрудников берут его отсюда.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.employees import Employee
from app.models.login_failures import REASON_LOCKED, LoginFailure


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _naive_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """Время из БД или из кода — к naive UTC. `last_login_at` пишется aware,
    колонка naive, и драйверы возвращают его по-разному."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def serialize_attempts(db: Session, email: str) -> None:
    """Попытки входа в ОДНУ учётку — строго по очереди (Postgres).

    Счётчик читается до проверки пароля, а неудача пишется после bcrypt (сотни
    миллисекунд): пачка параллельных запросов прошла бы проверку целиком до
    первой записи, и за окно пролезало бы не 5 попыток, а 5 + размер пачки.
    Транзакционная advisory-блокировка по email держится до коммита отказа или
    входа. Разные учётки друг друга не ждут. SQLite (тесты) — без блокировки.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "login:" + email_key(email)},
    )


def email_key(email: str) -> str:
    return (email or "").strip().lower()


def client_ip(request: Request) -> str | None:
    """Адрес клиента. На препроде бэкенд доступен только через nginx
    (порт наружу не проброшен), nginx ставит `X-Real-IP $remote_addr` — его
    клиент подделать не может. Без прокси (dev) — адрес соединения."""
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:64]
    return request.client.host if request.client else None


def _window() -> datetime.timedelta:
    return datetime.timedelta(minutes=settings.LOGIN_LOCK_MINUTES)


def _count_from(employee: Employee | None, now: datetime.datetime) -> datetime.datetime:
    points = [now - _window()]
    if employee is not None:
        points += [
            p for p in (
                _naive_utc(employee.last_login_at),
                _naive_utc(employee.login_unlocked_at),
            ) if p is not None
        ]
    return max(points)


def _locked_until(failure_times: list[datetime.datetime]) -> datetime.datetime | None:
    """По временам засчитанных неудач (от новых к старым) — до какого момента
    вход закрыт. Все они внутри окна, поэтому старейшая из последних N + окно
    всегда в будущем."""
    limit = settings.LOGIN_MAX_FAILURES
    if len(failure_times) < limit:
        return None
    return failure_times[limit - 1] + _window()


def login_locked_until(
    db: Session, email: str, employee: Employee | None,
    now: datetime.datetime | None = None,
) -> datetime.datetime | None:
    now = now or _utcnow()
    times = [
        _naive_utc(t) for (t,) in (
            db.query(LoginFailure.created_at)
            .filter(
                LoginFailure.email == email_key(email),
                LoginFailure.reason != REASON_LOCKED,
                LoginFailure.created_at > _count_from(employee, now),
            )
            .order_by(LoginFailure.created_at.desc())
            .limit(settings.LOGIN_MAX_FAILURES)
            .all()
        )
    ]
    return _locked_until(times)


def locked_until_by_employee(
    db: Session, employees: Iterable[Employee],
) -> dict[int, datetime.datetime]:
    """Пометка «вход заблокирован до …» для списка сотрудников — ОДНИМ запросом
    за окно, а не запросом на строку."""
    now = _utcnow()
    by_email = {email_key(e.email): e for e in employees if e.email}
    if not by_email:
        return {}
    rows = (
        db.query(LoginFailure.email, LoginFailure.created_at)
        .filter(
            LoginFailure.email.in_(list(by_email)),
            LoginFailure.reason != REASON_LOCKED,
            LoginFailure.created_at > now - _window(),
        )
        .order_by(LoginFailure.created_at.desc())
        .all()
    )
    times: dict[str, list[datetime.datetime]] = defaultdict(list)
    for email, created in rows:
        times[email].append(_naive_utc(created))
    result: dict[int, datetime.datetime] = {}
    for key, emp in by_email.items():
        start = _count_from(emp, now)
        until = _locked_until([t for t in times.get(key, []) if t > start])
        if until is not None:
            result[emp.id] = until
    return result


def record_failure(
    db: Session, email: str, ip: str | None, reason: str,
    employee: Employee | None = None,
) -> None:
    """Запись в журнал. Коммит — снаружи (вход коммитит перед отказом)."""
    db.add(LoginFailure(
        email=email_key(email)[:255],
        ip=ip,
        reason=reason,
        employee_id=employee.id if employee is not None else None,
        created_at=_utcnow(),
    ))


def unlock_login(employee: Employee) -> None:
    """Снять блокировку: неудачи до этого момента в порог больше не идут.
    Журнал не трогается — история попыток остаётся."""
    employee.login_unlocked_at = _utcnow()
