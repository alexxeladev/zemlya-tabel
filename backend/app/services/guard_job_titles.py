"""Справочник должностей охраны: чтение, правка, подбор по умолчанию.

Единственное место, которое знает, как строка табеля и рабочее место охраны
получают должность. Расчёту (`guard_payroll`) должность не нужна — ему нужен
только её `pay_type`, и он получает его готовым числом.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.guard_job_titles import (
    DEFAULT_GUARD_JOB_TITLES,
    GUARD_PAY_PER_SHIFT,
    GUARD_PAY_TYPES,
    GuardJobTitle,
)
from app.models.positions import EmployeePosition
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


def title_index(db: Session) -> dict[str, GuardJobTitle]:
    """Справочник одним запросом: {имя в нижнем регистре: должность}. Для списков
    (штат) — чтобы не ходить в базу на каждую строку."""
    return {t.name.lower(): t for t in db.query(GuardJobTitle).all()}


def job_title_of_position(
    position: EmployeePosition, index: dict[str, GuardJobTitle] | None = None
) -> GuardJobTitle | None:
    """Должность охранного рабочего места.

    Связь — по ключу `EmployeePosition.job_title_id`. Позиции, заведённые до
    этой связи, узнаются по названию ОДИН раз (`index` — справочник по имени),
    и ссылка при этом проставляется; название ни с чем не совпало — `None`, и
    экран показывает настоящее `title` с пустой должностью. Подменять её
    «первой подходящей по типу оплаты» нельзя: следующее сохранение карточки
    молча переписало бы должность человека (нашло ревью).
    """
    if position.job_title is not None:
        return position.job_title
    if index is None:
        return None
    found = index.get((position.title or "").strip().lower())
    if found is not None:
        position.job_title_id = found.id
    return found


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
    from app.models.positions import EmployeePosition

    is_new = title is None
    # Всё проверяется ДО первой записи: иначе половина правок уже легла бы в
    # сессию к моменту отказа (ревью).
    name = None
    if "name" in data or is_new:
        name = (data.get("name") or "").strip()
        if len(name) < 2:
            raise GuardJobTitleError("Укажите название должности")
        _check_name_free(db, name, None if is_new else title.id)
    pay_type = None
    if "pay_type" in data or is_new:
        pay_type = data.get("pay_type") or GUARD_PAY_PER_SHIFT
        if pay_type not in GUARD_PAY_TYPES:
            raise GuardJobTitleError("Способ оплаты: «ставка за смену» или «оклад за месяц»")
        if not is_new and pay_type != title.pay_type:
            closed = closed_months_using(db, title)
            if closed:
                raise GuardJobTitleClosedMonths(title, closed)
    deactivate = not is_new and data.get("is_active") is False and title.is_active
    if deactivate:
        if title.default_for_post or title.default_for_crew:
            raise GuardJobTitleError(
                "Это должность по умолчанию — сначала назначьте по умолчанию другую"
            )
        others = [
            t for t in db.query(GuardJobTitle).filter(GuardJobTitle.is_active == True)  # noqa: E712
            if t.id != title.id and t.pay_type == title.pay_type
        ]
        if not others:
            raise GuardJobTitleError(
                f"Это последняя активная должность с оплатой «{title.pay_type_label.lower()}» — "
                "снять её нельзя"
            )
    for flag in ("default_for_post", "default_for_crew"):
        if data.get(flag) is True and (deactivate or (not is_new and not title.is_active and data.get("is_active") is not True)):
            raise GuardJobTitleError("Снятая должность не может быть должностью по умолчанию")
        if data.get(flag) is False and not is_new and getattr(title, flag):
            raise GuardJobTitleError(
                "Снять «по умолчанию» можно только назначив по умолчанию другую должность"
            )

    if is_new:
        title = GuardJobTitle()
        # Новая должность встаёт В КОНЕЦ списка, а не вклинивается первой.
        last = db.query(GuardJobTitle).order_by(GuardJobTitle.sort_order.desc()).first()
        title.sort_order = (last.sort_order if last else 0) + 1
        db.add(title)
    old_name = title.name
    if name is not None:
        title.name = name
    if pay_type is not None:
        title.pay_type = pay_type
    if "sort_order" in data and data["sort_order"] is not None:
        title.sort_order = int(data["sort_order"])
    if "is_active" in data and data["is_active"] is not None:
        title.is_active = bool(data["is_active"])
    db.flush()
    for flag in ("default_for_post", "default_for_crew"):
        if data.get(flag) is True:
            setattr(title, flag, True)
            _clear_default(db, flag, title)
    # Переименование доезжает до рабочих мест: `title` позиции — подпись в
    # табеле, ведомости и Excel, и она обязана совпадать со справочником.
    if old_name is not None and title.name != old_name:
        for position in db.query(EmployeePosition).filter(EmployeePosition.job_title_id == title.id).all():
            position.title = title.name
    db.flush()
    return title


class GuardJobTitleClosedMonths(GuardJobTitleError):
    """Смена способа оплаты задела бы закрытые месяцы."""

    def __init__(self, title: GuardJobTitle, months: list[tuple[int, int]]) -> None:
        self.months = months
        listed = ", ".join(f"{m:02d}.{y}" for y, m in months)
        super().__init__(
            f"Способ оплаты должности «{title.name}» менять нельзя: она стоит в строках "
            f"закрытых месяцев ({listed}), и расчёт этих месяцев изменился бы задним "
            "числом. Заведите новую должность с нужной оплатой и переведите людей на неё"
        )


def closed_months_using(db: Session, title: GuardJobTitle) -> list[tuple[int, int]]:
    """(год, месяц) закрытых периодов, где есть строки вахты с этой должностью.

    Снапшота расчёта в системе нет: способ оплаты читается живьём, и его смена
    переписала бы ведомость, которую бухгалтерия уже видела. Закрытость — как у
    ячеек: статус периода табеля отдела места работы, `closed`.
    """
    from app.models.guard_assignments import GuardAssignment
    from app.models.timesheet_periods import TimesheetPeriod

    months: set[tuple[int, int]] = set()
    rows = db.query(GuardAssignment).filter(GuardAssignment.job_title_id == title.id).all()
    if not rows:
        return []
    keys = {(a.department_id, a.year, a.month) for a in rows}
    closed = db.query(TimesheetPeriod).filter(TimesheetPeriod.status == "closed").all()
    for period in closed:
        if (period.department_id, period.year, period.month) in keys:
            months.add((period.year, period.month))
    return sorted(months)


def job_title_usage(db: Session, title: GuardJobTitle) -> int:
    """Сколько строк табеля вахты стоят на этой должности (за все месяцы)."""
    from app.models.guard_assignments import GuardAssignment

    return db.query(GuardAssignment).filter(GuardAssignment.job_title_id == title.id).count()


def usage_counts(db: Session) -> dict[int, tuple[int, int, int]]:
    """{id должности: (строк табеля, из них в закрытых месяцах, рабочих мест)}
    одним проходом — для списка справочника, без COUNT на строку."""
    from sqlalchemy import func

    from app.models.guard_assignments import GuardAssignment
    from app.models.positions import EmployeePosition
    from app.models.timesheet_periods import TimesheetPeriod

    rows = {r[0]: r[1] for r in db.query(GuardAssignment.job_title_id, func.count()).group_by(GuardAssignment.job_title_id)}
    positions = {r[0]: r[1] for r in db.query(EmployeePosition.job_title_id, func.count()).group_by(EmployeePosition.job_title_id)}
    closed_keys = {
        (p.department_id, p.year, p.month)
        for p in db.query(TimesheetPeriod).filter(TimesheetPeriod.status == "closed")
    }
    closed: dict[int, int] = {}
    if closed_keys:
        for a in db.query(GuardAssignment).all():
            if (a.department_id, a.year, a.month) in closed_keys:
                closed[a.job_title_id] = closed.get(a.job_title_id, 0) + 1
    ids = set(rows) | set(positions) | set(closed)
    return {i: (rows.get(i, 0), closed.get(i, 0), positions.get(i, 0)) for i in ids if i is not None}


def delete_job_title(db: Session, title: GuardJobTitle) -> str:
    """Удалить неиспользуемую должность; использованную — только снять."""
    if title.default_for_post or title.default_for_crew:
        raise GuardJobTitleError("Это должность по умолчанию — сначала назначьте другую")
    from app.models.positions import EmployeePosition

    used_by_staff = db.query(EmployeePosition).filter(EmployeePosition.job_title_id == title.id).count()
    if job_title_usage(db, title) or used_by_staff:
        # Рабочие места штата тоже ссылка: физически удалить — оставить людей
        # без должности (ревью).
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
