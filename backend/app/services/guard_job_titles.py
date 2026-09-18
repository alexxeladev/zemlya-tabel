"""Справочник должностей охраны: чтение, правка, подбор по умолчанию.

Единственное место, которое знает, как строка табеля и рабочее место охраны
получают должность. Расчёту (`guard_payroll`) должность не нужна — ему нужен
только её `pay_type`, и он получает его готовым числом.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.guard_job_titles import (
    DEFAULT_GUARD_JOB_TITLES,
    GUARD_PAY_TYPES,
    GuardJobTitle,
)
from app.models.positions import PAY_TYPE_SALARY, EmployeePosition
from app.services.guard_duty import GuardError


class GuardJobTitleError(GuardError):
    """Ошибка справочника должностей; роутер отвечает 422, как на любую ошибку вахты."""


def list_job_titles(db: Session, *, include_inactive: bool = False) -> list[GuardJobTitle]:
    q = db.query(GuardJobTitle)
    if not include_inactive:
        q = q.filter(GuardJobTitle.is_active == True)  # noqa: E712
    return q.order_by(GuardJobTitle.sort_order, GuardJobTitle.id).all()


def get_job_title(db: Session, job_title_id: int | None) -> GuardJobTitle:
    """Должность по id; неизвестная или снятая — отказ с понятной причиной."""
    title = db.get(GuardJobTitle, job_title_id) if job_title_id is not None else None
    if title is None:
        raise GuardJobTitleError("Должность не найдена в справочнике вахты")
    if not title.is_active:
        raise GuardJobTitleError(
            f"Должность «{title.name}» снята — выберите другую или верните её в справочнике"
        )
    return title


def default_job_title(db: Session, place) -> GuardJobTitle:
    """Должность по умолчанию для места работы: своя у поста и у экипажа ГБР.

    Ровно одна помечена на каждый вид (держит `save_job_title`). Ни одной —
    справочник сломан руками в базе; тогда берём первую активную, а не падаем
    посреди постановки на пост.
    """
    flag = GuardJobTitle.default_for_crew if getattr(place, "is_crew", False) else GuardJobTitle.default_for_post
    title = (
        db.query(GuardJobTitle)
        .filter(flag == True, GuardJobTitle.is_active == True)  # noqa: E712
        .order_by(GuardJobTitle.sort_order, GuardJobTitle.id)
        .first()
    )
    if title is None:
        title = db.query(GuardJobTitle).filter(GuardJobTitle.is_active == True).order_by(GuardJobTitle.id).first()  # noqa: E712
    if title is None:
        raise GuardJobTitleError("В справочнике вахты нет ни одной должности")
    return title


def resolve_job_title(db: Session, job_title_id: int | None, place) -> GuardJobTitle:
    """Явно выбранная должность либо обычная для этого места."""
    if job_title_id is None:
        return default_job_title(db, place)
    return get_job_title(db, job_title_id)


def job_title_of_position(db: Session, position: EmployeePosition) -> GuardJobTitle | None:
    """Должность охранного рабочего места — по названию, как её пишет вахта.

    Отдельной колонки у позиции нет: `title` и есть имя должности. Название
    переименовали или снесли — подбираем по типу оплаты (оклад → первая
    окладная, иначе первая посменная), чтобы штат не остался без должности.
    """
    name = (position.title or "").strip().lower()
    if name:
        # Сравнение без регистра — в Python: SQL-`lower()` в SQLite не знает
        # кириллицу, а справочник — десяток строк.
        found = next((t for t in db.query(GuardJobTitle).all() if t.name.lower() == name), None)
        if found is not None:
            return found
    wanted = PAY_TYPE_SALARY if position.pay_type == PAY_TYPE_SALARY else "per_shift"
    return (
        db.query(GuardJobTitle)
        .filter(GuardJobTitle.pay_type == wanted, GuardJobTitle.is_active == True)  # noqa: E712
        .order_by(GuardJobTitle.sort_order, GuardJobTitle.id)
        .first()
    )


# ── Правка справочника ────────────────────────────────────────────────────────

def _check_name_free(db: Session, name: str, exclude_id: int | None) -> None:
    for other in db.query(GuardJobTitle).all():
        if other.id != exclude_id and other.name.lower() == name.lower():
            raise GuardJobTitleError(f"Должность «{name}» уже есть в справочнике")


def _clear_default(db: Session, flag_name: str, keep: GuardJobTitle) -> None:
    for other in db.query(GuardJobTitle).filter(getattr(GuardJobTitle, flag_name) == True).all():  # noqa: E712
        if other is not keep:
            setattr(other, flag_name, False)


def save_job_title(db: Session, title: GuardJobTitle | None, data: dict) -> GuardJobTitle:
    """Создать или поправить должность. Коммит снаружи.

    Инварианты: название непустое и уникально без учёта регистра; способ оплаты
    — один из двух; «по умолчанию для поста» и «для экипажа» — ровно у одной
    активной должности каждое (флаг переезжает, а не дублируется); снять
    должность по умолчанию нельзя — сначала назначьте другую.
    """
    if title is None:
        title = GuardJobTitle()
        # Новая должность встаёт В КОНЕЦ списка, а не вклинивается первой.
        last = db.query(GuardJobTitle).order_by(GuardJobTitle.sort_order.desc()).first()
        title.sort_order = (last.sort_order if last else 0) + 1
        db.add(title)
    if "name" in data or title.name is None:
        name = (data.get("name") or "").strip()
        if len(name) < 2:
            raise GuardJobTitleError("Укажите название должности")
        _check_name_free(db, name, title.id)
        title.name = name
    if "pay_type" in data or title.pay_type is None:
        pay_type = data.get("pay_type") or "per_shift"
        if pay_type not in GUARD_PAY_TYPES:
            raise GuardJobTitleError("Способ оплаты: «ставка за смену» или «оклад за месяц»")
        title.pay_type = pay_type
    if "sort_order" in data and data["sort_order"] is not None:
        title.sort_order = int(data["sort_order"])
    if "is_active" in data and data["is_active"] is not None:
        if data["is_active"] is False and (title.default_for_post or title.default_for_crew):
            raise GuardJobTitleError(
                "Это должность по умолчанию — сначала назначьте по умолчанию другую"
            )
        title.is_active = bool(data["is_active"])
    db.flush()
    for flag in ("default_for_post", "default_for_crew"):
        if data.get(flag) is True:
            if not title.is_active:
                raise GuardJobTitleError("Снятая должность не может быть должностью по умолчанию")
            setattr(title, flag, True)
            _clear_default(db, flag, title)
        elif data.get(flag) is False and getattr(title, flag):
            raise GuardJobTitleError(
                "Снять «по умолчанию» можно только назначив по умолчанию другую должность"
            )
    db.flush()
    return title


def job_title_usage(db: Session, title: GuardJobTitle) -> int:
    """Сколько строк табеля вахты стоят на этой должности (за все месяцы)."""
    from app.models.guard_assignments import GuardAssignment

    return db.query(GuardAssignment).filter(GuardAssignment.job_title_id == title.id).count()


def delete_job_title(db: Session, title: GuardJobTitle) -> str:
    """Удалить неиспользуемую должность; использованную — только снять."""
    if title.default_for_post or title.default_for_crew:
        raise GuardJobTitleError("Это должность по умолчанию — сначала назначьте другую")
    if job_title_usage(db, title):
        title.is_active = False
        db.flush()
        return "deactivated"
    db.delete(title)
    db.flush()
    return "deleted"


def seed_default_job_titles(db: Session) -> list[GuardJobTitle]:
    """Четыре должности «из коробки» — для пустой базы (сиды, тесты). Идемпотентно."""
    existing = {t.name.lower(): t for t in db.query(GuardJobTitle).all()}
    out = []
    for order, (name, pay_type, for_post, for_crew) in enumerate(DEFAULT_GUARD_JOB_TITLES, start=1):
        title = existing.get(name.lower())
        if title is None:
            title = GuardJobTitle(
                name=name, pay_type=pay_type, default_for_post=for_post,
                default_for_crew=for_crew, sort_order=order,
            )
            db.add(title)
        out.append(title)
    db.flush()
    return out
