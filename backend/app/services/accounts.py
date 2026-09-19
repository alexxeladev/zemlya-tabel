"""Логин учётки (решение заказчика, 19.09.2026).

Отдельного поля «логин» НЕТ: логин — часть почты до «@». Входить можно и по
нему («victim»), и по полной почте, регистр не важен: Victim, victim и
VICTIM@example.com — одна учётка. Пользователи уже заведены по почте, объяснять
им новое поле заказчик счёл непродуктивным.

Отсюда два инварианта, которые держит этот модуль (единственное место правила):
  * почта хранится в нижнем регистре и уникальна без учёта регистра
    (в БД — уникальный индекс по lower(email));
  * часть до «@» уникальна среди всех учёток — иначе «ivanov» с двумя доменами
    вёл бы в две учётки. Проверяется при заведении доступа; миграция отказывает,
    если в базе уже есть такие пары.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.employees import Employee


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def login_name(email: str | None) -> str:
    """Логин — часть почты до «@», в нижнем регистре."""
    return normalize_email(email).split("@", 1)[0]


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_accounts(db: Session, identifier: str) -> list[Employee]:
    """Учётки по тому, что ввели в поле входа: полная почта или логин.
    Больше одной — только если инвариант нарушен в обход приложения."""
    key = normalize_email(identifier)
    if not key:
        return []
    email_lower = func.lower(Employee.email)
    if "@" in key:
        cond = email_lower == key
    else:
        cond = email_lower.like(_like_escape(key) + "@%", escape="\\")
    return db.query(Employee).filter(Employee.email.is_not(None), cond).limit(2).all()


def find_account(db: Session, identifier: str) -> Employee | None:
    found = find_accounts(db, identifier)
    return found[0] if len(found) == 1 else None


def account_conflict(db: Session, email: str, exclude_id: int | None = None) -> str | None:
    """Почему доступ с такой почтой завести нельзя, или None: та же почта в
    другом регистре или тот же логин (часть до «@») у другой учётки."""
    email = normalize_email(email)
    name = login_name(email)
    q = db.query(Employee).filter(
        Employee.email.is_not(None),
        func.lower(Employee.email).like(_like_escape(name) + "@%", escape="\\"),
    )
    if exclude_id is not None:
        q = q.filter(Employee.id != exclude_id)
    other = q.first()
    if other is None:
        return None
    if normalize_email(other.email) == email:
        return "Email already registered"
    return (
        f"Логин «{name}» уже занят: {other.email} ({other.full_name}). "
        "Логин — часть почты до «@», он должен быть уникальным."
    )
