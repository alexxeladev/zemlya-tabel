"""
Модуль «Вахта»: справочник (зоны, объекты, посты, экипажи), табель и мутации.

Здесь живут ВСЕ изменения данных вахты. Расчёт денег сюда не лезет — он в
`app.services.guard_payroll` (чистые функции); этот модуль только ходит в базу.

Ключевые правила поведения, из ТЗ и образцов заказчика:

* **Охранник — позиция сотрудника из общего справочника.** Своего справочника
  охранников нет и не должно быть: один человек оказался бы в двух местах.
  Быстрый найм (`quick_hire`) заводит обычного `Employee` с позицией в отделе
  охраны, поэтому он же виден в разделе «Сотрудники».
* **Дни по умолчанию отмечены все.** Охранник обычно отрабатывает весь срок,
  снимаются исключения — а не наоборот.
* **Замена на посту создаёт ВТОРУЮ строку** на то же место, дни делятся с
  выбранного числа. Если прежнему не остаётся ни одного дня (замена с первого
  числа) — второй строки нет, просто меняется человек.
* **Место работы у строки одно из двух** — пост объекта или выездной экипаж
  ГБР; отсюда же берутся проценты распределения (у поста — от объекта, у
  экипажа — от него самого).
"""
from __future__ import annotations

import datetime
import re
from calendar import monthrange
from decimal import Decimal

from sqlalchemy import func as sa_func
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models.departments import Department
from app.models.employees import Employee
from app.models.guard_assignments import GuardAssignment, GuardShift
from app.models.guard_posts import (
    GUARD_KIND_LABELS,
    GuardCrew,
    GuardCrewShare,
    GuardPost,
    GuardSite,
    GuardSiteShare,
    GuardZone,
)
from app.models.guard_settings import DEFAULT_EMPLOYER_TAX_PERCENT, GuardSettings
from app.models.positions import PAY_TYPE_PER_SHIFT, EmployeePosition
from app.services.org_access import can_access_department, is_department_scoped

_ZERO = Decimal("0")

#: Формат автоматического табельного номера быстрого найма. Нумерация ОБЩАЯ с
#: остальными сотрудниками (T-0001, T-0002, …): человек заводится в общий
#: справочник, отдельной серии для охраны быть не должно.
TAB_NUMBER_PREFIX = "T-"
TAB_NUMBER_DIGITS = 4
_TAB_NUMBER_RE = re.compile(rf"^{re.escape(TAB_NUMBER_PREFIX)}(\d+)$")


class GuardError(Exception):
    """Ошибка модуля вахты; роутер переводит её в 422."""


# ── Настройки вахты ───────────────────────────────────────────────────────────

def employer_tax_percent(db: Session) -> Decimal:
    """Ставка налога на официальную часть выплаты, в процентах (task_vahta_taxes).

    Единственное место, откуда расчёт вахты берёт ставку. Строки настроек нет
    (база без миграции) — действует ставка по умолчанию.
    """
    settings = db.query(GuardSettings).order_by(GuardSettings.id).first()
    if settings is None:
        return DEFAULT_EMPLOYER_TAX_PERCENT
    return Decimal(str(settings.employer_tax_percent))


def set_employer_tax_percent(db: Session, percent: Decimal) -> GuardSettings:
    """Задать ставку налога. Строки настроек нет — заводится. Коммит снаружи."""
    if percent < 0 or percent > 100:
        raise GuardError("Ставка налога должна быть от 0 до 100 %")
    settings = db.query(GuardSettings).order_by(GuardSettings.id).first()
    if settings is None:
        settings = GuardSettings(employer_tax_percent=percent)
        db.add(settings)
    else:
        settings.employer_tax_percent = percent
    db.flush()
    return settings


# ── Отделы охраны ─────────────────────────────────────────────────────────────

def guard_department_ids(db: Session, actor: Employee) -> list[int]:
    """Отделы охраны, доступные actor-у.

    Отдел попадает в модуль только по ФЛАГУ `is_guard_department` — по имени
    отдел искать нельзя, его переименуют и правило молча отвалится. Дальше
    работает обычная ролевая модель: manager и timekeeper видят свои отделы,
    admin и accountant — все.
    """
    ids = [
        d.id
        for d in db.query(Department)
        .filter(
            Department.is_guard_department == True,  # noqa: E712
            Department.is_active == True,  # noqa: E712
        )
        .order_by(Department.name)
        .all()
    ]
    if not is_department_scoped(actor):
        return ids
    return [i for i in ids if can_access_department(actor, i)]


def require_department_access(db: Session, actor: Employee, department_id: int) -> None:
    """Проверить, что actor вправе работать с этим отделом охраны."""
    if department_id not in guard_department_ids(db, actor):
        raise GuardError("Нет доступа к этому подразделению охраны")


# ── Справочник: зоны обслуживания ─────────────────────────────────────────────

def list_zones(db: Session, department_ids: list[int]) -> list[GuardZone]:
    """Зоны доступных отделов охраны — верхний уровень группировки табеля."""
    if not department_ids:
        return []
    return (
        db.query(GuardZone)
        .options(
            selectinload(GuardZone.crews).selectinload(GuardCrew.shares),
            selectinload(GuardZone.sites).selectinload(GuardSite.shares),
            selectinload(GuardZone.sites).selectinload(GuardSite.posts),
        )
        .filter(
            GuardZone.department_id.in_(department_ids),
            GuardZone.is_active == True,  # noqa: E712
        )
        .order_by(GuardZone.sort_order, GuardZone.id)
        .all()
    )


def create_zone(db: Session, data: dict) -> GuardZone:
    zone = GuardZone(
        name=data["name"],
        department_id=data["department_id"],
        sort_order=data.get("sort_order") or 0,
    )
    db.add(zone)
    db.flush()
    return zone


def update_zone(db: Session, zone: GuardZone, data: dict) -> GuardZone:
    for field in ("name", "sort_order", "is_active"):
        if field in data and data[field] is not None:
            setattr(zone, field, data[field])
    return zone


def delete_zone(db: Session, zone: GuardZone) -> str:
    """Снять зону. Объекты и экипажи внутри неё гасятся вместе с ней.

    Физически не удаляем: по её постам могли вести табель, и история строк
    осталась бы без места работы.
    """
    if zone.sites or zone.crews:
        zone.is_active = False
        for site in zone.sites:
            site.is_active = False
        for crew in zone.crews:
            crew.is_active = False
        return "deactivated"
    db.delete(zone)
    return "deleted"


# ── Справочник: экипажи ГБР ───────────────────────────────────────────────────

def list_crews(db: Session, department_ids: list[int]) -> list[GuardCrew]:
    if not department_ids:
        return []
    zone_ids = [z.id for z in list_zones(db, department_ids)]
    if not zone_ids:
        return []
    return (
        db.query(GuardCrew)
        .options(selectinload(GuardCrew.shares), selectinload(GuardCrew.zone))
        .filter(
            GuardCrew.zone_id.in_(zone_ids),
            GuardCrew.is_active == True,  # noqa: E712
        )
        .order_by(GuardCrew.sort_order, GuardCrew.id)
        .all()
    )


def create_crew(db: Session, data: dict) -> GuardCrew:
    zone = db.get(GuardZone, data["zone_id"])
    if zone is None:
        raise GuardError("Зона обслуживания не найдена")
    crew = GuardCrew(
        name=data["name"],
        zone_id=zone.id,
        shift_rate=data.get("shift_rate") or _ZERO,
        sort_order=data.get("sort_order") or 0,
    )
    db.add(crew)
    db.flush()
    return crew


def update_crew(db: Session, crew: GuardCrew, data: dict) -> GuardCrew:
    for field in ("name", "shift_rate", "sort_order", "is_active"):
        if field in data and data[field] is not None:
            setattr(crew, field, data[field])
    return crew


def set_crew_shares(db: Session, crew: GuardCrew, shares: list) -> None:
    """Переписать распределение ЭКИПАЖА по юрлицам целиком."""
    db.query(GuardCrewShare).filter(GuardCrewShare.crew_id == crew.id).delete(
        synchronize_session=False
    )
    for share in shares:
        db.add(GuardCrewShare(
            crew_id=crew.id, company_id=share.company_id, percent=share.percent,
        ))


def delete_crew(db: Session, crew: GuardCrew) -> str:
    """Снять экипаж; объекты его зоны это никак не затрагивает."""
    crew.is_active = False
    return "deactivated"


# ── Справочник: объекты ───────────────────────────────────────────────────────

def list_sites(db: Session, department_ids: list[int]) -> list[GuardSite]:
    if not department_ids:
        return []
    zone_ids = [z.id for z in list_zones(db, department_ids)]
    if not zone_ids:
        return []
    return (
        db.query(GuardSite)
        .options(
            selectinload(GuardSite.shares),
            selectinload(GuardSite.posts),
            selectinload(GuardSite.zone).selectinload(GuardZone.crews),
        )
        .filter(
            GuardSite.zone_id.in_(zone_ids),
            GuardSite.is_active == True,  # noqa: E712
        )
        .order_by(GuardSite.sort_order, GuardSite.id)
        .all()
    )


def create_site(db: Session, data: dict) -> GuardSite:
    zone = db.get(GuardZone, data["zone_id"])
    if zone is None:
        raise GuardError("Зона обслуживания не найдена")
    site = GuardSite(
        name=data["name"],
        zone_id=zone.id,
        shift_rate=data.get("shift_rate") or _ZERO,
        sort_order=data.get("sort_order") or 0,
    )
    db.add(site)
    db.flush()
    return site


def update_site(db: Session, site: GuardSite, data: dict) -> GuardSite:
    for field in ("name", "shift_rate", "sort_order", "is_active"):
        if field in data:
            setattr(site, field, data[field])
    # Перенос объекта в другую зону — законная операция: зоны перекраивают.
    if data.get("zone_id"):
        if db.get(GuardZone, data["zone_id"]) is None:
            raise GuardError("Зона обслуживания не найдена")
        site.zone_id = data["zone_id"]
    return site


def set_site_shares(db: Session, site: GuardSite, shares: list) -> None:
    """Переписать распределение ОБЪЕКТА по юрлицам целиком.

    Набор заменяется, а не дополняется: «убрать юрлицо» иначе было бы нечем.
    Своих процентов у постов нет — они наследуют объект (см. модель).
    """
    db.query(GuardSiteShare).filter(GuardSiteShare.site_id == site.id).delete(
        synchronize_session=False
    )
    for share in shares:
        db.add(GuardSiteShare(
            site_id=site.id, company_id=share.company_id, percent=share.percent,
        ))


def delete_site(db: Session, site: GuardSite) -> str:
    """Удалить объект, а если по его постам вели табель — деактивировать."""
    post_ids = [p.id for p in site.posts]
    used = post_ids and (
        db.query(sa_func.count(GuardAssignment.id))
        .filter(GuardAssignment.post_id.in_(post_ids))
        .scalar()
    )
    if used:
        site.is_active = False
        for post in site.posts:
            post.is_active = False
        return "deactivated"
    db.query(GuardSiteShare).filter(GuardSiteShare.site_id == site.id).delete(
        synchronize_session=False
    )
    db.query(GuardPost).filter(GuardPost.site_id == site.id).delete(
        synchronize_session=False
    )
    db.delete(site)
    return "deleted"


def shares_map(place) -> dict[int, Decimal]:
    """{company_id: percent} места работы — веса для распределения начислений.

    У поста берутся проценты его ОБЪЕКТА, у экипажа — свои. Одна функция на оба
    случая, чтобы «откуда проценты» решалось в одном месте.
    """
    if isinstance(place, GuardPost):
        source = place.site.shares if place.site else []
    else:
        source = place.shares if place is not None else []
    return {s.company_id: Decimal(str(s.percent)) for s in source if s.percent > _ZERO}


# ── Справочник: посты ─────────────────────────────────────────────────────────

def list_posts(db: Session, department_ids: list[int]) -> list[GuardPost]:
    if not department_ids:
        return []
    zone_ids = [z.id for z in list_zones(db, department_ids)]
    if not zone_ids:
        return []
    return (
        db.query(GuardPost)
        .join(GuardSite, GuardPost.site_id == GuardSite.id)
        .options(selectinload(GuardPost.site).selectinload(GuardSite.zone))
        .filter(
            GuardSite.zone_id.in_(zone_ids),
            GuardPost.is_active == True,  # noqa: E712
            GuardSite.is_active == True,  # noqa: E712
        )
        .order_by(GuardSite.sort_order, GuardSite.id, GuardPost.sort_order, GuardPost.id)
        .all()
    )


def create_post(db: Session, data: dict) -> GuardPost:
    site = db.get(GuardSite, data["site_id"])
    if site is None:
        raise GuardError("Объект не найден")
    post = GuardPost(
        site_id=site.id,
        name=data["name"],
        kind=data.get("kind") or "guard",
        # NULL — ставка берётся у объекта; заполнено — переопределяет её.
        shift_rate=data.get("shift_rate"),
        sort_order=data.get("sort_order") or 0,
    )
    db.add(post)
    db.flush()
    return post


def update_post(db: Session, post: GuardPost, data: dict) -> GuardPost:
    for field in ("name", "kind", "shift_rate", "sort_order", "is_active"):
        if field in data:
            setattr(post, field, data[field])
    return post


def delete_post(db: Session, post: GuardPost) -> str:
    """Удалить пост, а если по нему уже вели табель — деактивировать.

    Физическое удаление стёрло бы историю смен, поэтому used-пост только
    гасится: то же правило, что у позиций сотрудника.
    """
    used = (
        db.query(sa_func.count(GuardAssignment.id))
        .filter(GuardAssignment.post_id == post.id)
        .scalar()
    )
    if used:
        post.is_active = False
        return "deactivated"
    db.delete(post)
    return "deleted"


# ── Табель: назначения и смены ────────────────────────────────────────────────

def month_days(year: int, month: int) -> list[datetime.date]:
    return [
        datetime.date(year, month, d) for d in range(1, monthrange(year, month)[1] + 1)
    ]


def list_assignments(
    db: Session, year: int, month: int, department_ids: list[int]
) -> list[GuardAssignment]:
    """Строки табеля месяца по доступным отделам охраны.

    Отдел строки берётся у её МЕСТА РАБОТЫ: у поста — через объект, у выездного
    экипажа — у него самого. Join здесь не сделать одним куском (места два и
    они в разных таблицах), поэтому берём id мест доступных отделов заранее и
    фильтруем по ним — запросов всё равно константа.
    """
    if not department_ids:
        return []
    zone_ids = [z.id for z in list_zones(db, department_ids)]
    if not zone_ids:
        return []
    post_ids = [
        pid for (pid,) in db.query(GuardPost.id)
        .join(GuardSite, GuardPost.site_id == GuardSite.id)
        .filter(GuardSite.zone_id.in_(zone_ids))
    ]
    crew_ids = [
        cid for (cid,) in db.query(GuardCrew.id)
        .filter(GuardCrew.zone_id.in_(zone_ids))
    ]
    if not post_ids and not crew_ids:
        return []
    return (
        db.query(GuardAssignment)
        .options(
            selectinload(GuardAssignment.shifts),
            selectinload(GuardAssignment.post)
            .selectinload(GuardPost.site)
            .selectinload(GuardSite.shares),
            selectinload(GuardAssignment.post)
            .selectinload(GuardPost.site)
            .selectinload(GuardSite.zone),
            selectinload(GuardAssignment.crew).selectinload(GuardCrew.shares),
            selectinload(GuardAssignment.crew).selectinload(GuardCrew.zone),
            selectinload(GuardAssignment.position).selectinload(
                EmployeePosition.employee
            ),
        )
        .filter(
            GuardAssignment.year == year,
            GuardAssignment.month == month,
            or_(
                GuardAssignment.post_id.in_(post_ids or [0]),
                GuardAssignment.crew_id.in_(crew_ids or [0]),
            ),
        )
        .order_by(GuardAssignment.sort_order, GuardAssignment.id)
        .all()
    )


def marked_days(assignment: GuardAssignment) -> set[int]:
    """Числа месяца, отмеченные в строке."""
    return {s.work_date.day for s in assignment.shifts}


def default_rate(place) -> Decimal:
    """Ставка места работы по умолчанию: у поста — своя или объекта, у экипажа — своя."""
    if isinstance(place, GuardPost):
        return place.effective_rate
    return Decimal(str(place.shift_rate)) if place is not None else _ZERO


def create_assignment(
    db: Session,
    *,
    year: int,
    month: int,
    place,
    position: EmployeePosition | None,
    rate: Decimal | None = None,
    kind: str | None = None,
    fill_days: bool = True,
    days: set[int] | None = None,
) -> GuardAssignment:
    """Поставить человека на МЕСТО РАБОТЫ (пост объекта или экипаж ГБР).

    По умолчанию отмечаются ВСЕ дни месяца: охранник обычно отрабатывает весь
    срок, снимаются исключения. Ставка берётся от места, если не задана явно.

    Должность — у СТРОКИ: на одном посту стоят люди разных должностей. Не
    задана — берётся та, что для этого места обычна: у экипажа ГБР, у поста
    охранник.
    """
    is_post = isinstance(place, GuardPost)
    assignment = GuardAssignment(
        year=year,
        month=month,
        post_id=place.id if is_post else None,
        crew_id=None if is_post else place.id,
        position_id=position.id if position else None,
        kind=kind or place.default_kind,
        rate=rate if rate is not None else default_rate(place),
        sort_order=_next_sort_order(db, year, month, place),
    )
    db.add(assignment)
    db.flush()

    if days is not None:
        set_days(db, assignment, days)
    elif fill_days:
        set_days(db, assignment, {d.day for d in month_days(year, month)})
    return assignment


def _next_sort_order(db: Session, year: int, month: int, place) -> int:
    """Следующий порядковый номер строки в пределах МЕСТА РАБОТЫ.

    Раньше считался по отделу целиком, из-за чего строки одного поста могли
    оказаться не подряд. Место — та единица, внутри которой люди перечисляются
    в карточке, по ней и нумеруем.
    """
    is_post = isinstance(place, GuardPost)
    query = db.query(sa_func.max(GuardAssignment.sort_order)).filter(
        GuardAssignment.year == year, GuardAssignment.month == month,
    )
    query = query.filter(
        GuardAssignment.post_id == place.id if is_post
        else GuardAssignment.crew_id == place.id
    )
    return (query.scalar() or 0) + 1


def employment_allows(assignment: GuardAssignment, day: int) -> bool:
    """Входит ли день в период работы человека этой строки (task_employment_period).

    Пустой слот (`position_id IS NULL`) — место без человека, ограничивать
    нечем. Даты берутся с ПОЗИЦИИ строки и пересекаются с датами человека тем же
    `services.employment_period`, что и в обычном табеле: второй копии правила
    быть не должно, иначе вахта и табель разойдутся.
    """
    from app.services.employment_period import is_within_employment

    position = assignment.position
    if position is None:
        return True
    work_date = datetime.date(assignment.year, assignment.month, day)
    return is_within_employment(position.employee, position, work_date)


def set_days(db: Session, assignment: GuardAssignment, days: set[int]) -> None:
    """Задать набор отмеченных дней строки целиком (снятые дни удаляются).

    Дни вне периода работы человека НЕ проставляются (task_employment_period).
    Здесь они отбрасываются молча — как автозаполнение в обычном табеле: сюда
    приходят и «отметить все дни месяца» при постановке на пост, и копирование
    состава прошлого периода, и замена на посту. Уронить их целиком из-за того,
    что охранник принят пятнадцатого, нельзя.

    Уже отмеченные дни за границей сохраняются, если их просят оставить, и
    снимаются, если просят снять: запрет касается ЗАПОЛНЕНИЯ, иначе смену,
    оставшуюся за новой границей, было бы нечем убрать.

    Явный клик по запрещённому дню — другое дело: он должен получить внятный
    отказ, а не тихо ничего не сделать, поэтому его ловит `toggle_day`.
    """
    last_day = monthrange(assignment.year, assignment.month)[1]
    wanted = {d for d in days if 1 <= d <= last_day}
    current = {s.work_date.day: s for s in assignment.shifts}
    wanted = {d for d in wanted if d in current or employment_allows(assignment, d)}

    for day, shift in current.items():
        if day not in wanted:
            assignment.shifts.remove(shift)
            db.delete(shift)
    for day in sorted(wanted - set(current)):
        assignment.shifts.append(
            GuardShift(
                assignment_id=assignment.id,
                work_date=datetime.date(assignment.year, assignment.month, day),
            )
        )
    db.flush()


def toggle_day(db: Session, assignment: GuardAssignment, day: int, value: bool) -> None:
    """Поставить или снять один день. Идемпотентно: двойной клик не ломает.

    День вне периода работы охранника отмечать нельзя — отказ внятный, чтобы
    клик не выглядел «не сработавшим» (task_employment_period). Снятие
    разрешено всегда.
    """
    if value and not employment_allows(assignment, day):
        from app.services.employment_period import employment_reason

        position = assignment.position
        reason = employment_reason(
            position.employee if position else None,
            position,
            datetime.date(assignment.year, assignment.month, day),
        )
        raise GuardError(f"День вне периода работы: {reason}")

    days = marked_days(assignment)
    if value:
        days.add(day)
    else:
        days.discard(day)
    set_days(db, assignment, days)


def delete_assignment(db: Session, assignment: GuardAssignment) -> None:
    """Убрать строку из табеля. Сотрудник из справочника НЕ удаляется —
    состав вахты между периодами меняется, это норма."""
    db.delete(assignment)


# ── Замена на посту ───────────────────────────────────────────────────────────

def replace_on_post(
    db: Session,
    assignment: GuardAssignment,
    *,
    position: EmployeePosition | None,
    from_day: int,
    rate: Decimal | None = None,
) -> tuple[GuardAssignment, GuardAssignment | None]:
    """Сменщик выходит на тот же пост с числа `from_day`.

    Дни с этого числа уходят ему ОТДЕЛЬНОЙ строкой, прежний остаётся со своими.
    Если прежнему не остаётся ни одного дня (замена с первого числа) — второй
    строки не создаётся, у существующей просто меняется человек: две строки, из
    которых одна пустая, читались бы как ошибка ввода.

    Возвращает (строка прежнего, строка сменщика или None).
    """
    last_day = monthrange(assignment.year, assignment.month)[1]
    if not 1 <= from_day <= last_day:
        raise GuardError("Число замены вне месяца")

    days = marked_days(assignment)
    kept = {d for d in days if d < from_day}
    moved = {d for d in days if d >= from_day}

    if not kept:
        assignment.position_id = position.id if position else None
        if rate is not None:
            assignment.rate = rate
        db.flush()
        return assignment, None

    set_days(db, assignment, kept)
    successor = create_assignment(
        db,
        year=assignment.year,
        month=assignment.month,
        place=assignment.place,
        position=position,
        rate=rate if rate is not None else assignment.rate,
        # Сменщик встаёт на ту же должность, что и прежний.
        kind=assignment.kind,
        days=moved,
    )
    return assignment, successor


# ── Копирование состава прошлого периода ──────────────────────────────────────

def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def copy_previous_period(
    db: Session, year: int, month: int, department_ids: list[int]
) -> int:
    """Перенести состав, посты и ставки предыдущего месяца.

    Дни проставляются ЗАНОВО, все отмеченными: копируется состав, а не факт
    выходов — иначе прошлые прогулы переехали бы в новый месяц.

    Месяц, в котором уже что-то есть, не трогаем: копирование поверх
    заполненного табеля затёрло бы работу табельщика.
    """
    if not department_ids:
        return 0
    if list_assignments(db, year, month, department_ids):
        raise GuardError("В этом месяце уже есть строки — копирование отменено")

    prev_year, prev_month = previous_month(year, month)
    source = list_assignments(db, prev_year, prev_month, department_ids)
    copied = 0
    for old in source:
        # Место работы, снятое после прошлого месяца, не воскрешаем.
        place = old.place
        if place is None or not place.is_active:
            continue
        new = GuardAssignment(
            year=year,
            month=month,
            post_id=old.post_id,
            crew_id=old.crew_id,
            position_id=old.position_id,
            kind=old.kind,
            rate=old.rate,
            is_official=old.is_official,
            sort_order=old.sort_order,
        )
        db.add(new)
        db.flush()
        set_days(db, new, {d.day for d in month_days(year, month)})
        copied += 1
    return copied


# ── Быстрый найм ──────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    return re.sub(r"[^а-яёa-z]", "", (name or "").lower())


def find_similar_employees(db: Session, full_name: str, limit: int = 5) -> list[Employee]:
    """Похожие сотрудники по ФИО — против дублей при срочном найме.

    Ищем по фамилии и по первым буквам имени: в вахте фамилию пишут то полностью
    («Караулов Олег Петрович»), то сокращённо («Караулов О.»), и точное
    сравнение строк такие пары не поймает.
    """
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if not parts:
        return []
    surname = parts[0]
    if len(surname) < 3:
        return []
    candidates = (
        db.query(Employee)
        .filter(Employee.full_name.ilike(f"%{surname}%"))
        .order_by(Employee.full_name)
        .limit(50)
        .all()
    )
    wanted = _normalize_name(full_name)
    exact = [c for c in candidates if _normalize_name(c.full_name) == wanted]
    rest = [c for c in candidates if c not in exact]
    return (exact + rest)[:limit]


def next_tab_number(db: Session) -> str:
    """Следующий свободный табельный номер общей нумерации (T-0001, T-0002 …)."""
    biggest = 0
    for (value,) in db.query(Employee.tab_number).filter(
        Employee.tab_number.isnot(None)
    ):
        match = _TAB_NUMBER_RE.match((value or "").strip())
        if match:
            biggest = max(biggest, int(match.group(1)))
    return f"{TAB_NUMBER_PREFIX}{biggest + 1:0{TAB_NUMBER_DIGITS}d}"


def quick_hire(
    db: Session, *, full_name: str, place, rate: Decimal | None = None,
    kind: str | None = None,
) -> tuple[Employee, EmployeePosition]:
    """Оформить нового охранника из трёх полей: ФИО, пост, ставка.

    Создаётся ОБЫЧНЫЙ сотрудник общего справочника с одной позицией в отделе
    охраны — отдельного справочника охранников нет. Кадровые поля (доступ,
    даты, коэффициенты) дозаполняются позже в карточке, если понадобятся.
    """
    full_name = (full_name or "").strip()
    if len(full_name) < 3:
        raise GuardError("Укажите ФИО")

    title = GUARD_KIND_LABELS.get(kind or place.default_kind, "Охранник")
    employee = Employee(
        full_name=full_name,
        tab_number=next_tab_number(db),
        position=title,
        is_active=True,
    )
    db.add(employee)
    db.flush()

    position = employee.ensure_primary_position()
    position.title = title
    position.department_id = place.department_id
    # Тип оплаты — посменный: это ближайшее к вахте из общих типов, и карточка
    # такого сотрудника не выглядит окладной. Сам расчёт вахты берёт ставку из
    # строки табеля, а не отсюда.
    position.pay_type = PAY_TYPE_PER_SHIFT
    position.shift_rate = rate if rate is not None else default_rate(place)
    position.rate = None
    position.hour_rate = None
    db.flush()
    return employee, position


def add_position_for_guard(
    db: Session, employee: Employee, place, rate: Decimal | None = None,
    kind: str | None = None,
) -> EmployeePosition:
    """Завести человеку ещё одно рабочее место под место работы охраны.

    Нужно для совместительства: в образце один человек стоит на двух местах со
    ставками 5 000 и 3 500 под одним табельным номером. Каждое место — своя
    позиция, иначе расчёт по ним не разделить.
    """
    position = EmployeePosition(
        employee_id=employee.id,
        title=GUARD_KIND_LABELS.get(kind or place.default_kind, "Охранник"),
        department_id=place.department_id,
        pay_type=PAY_TYPE_PER_SHIFT,
        shift_rate=rate if rate is not None else default_rate(place),
        is_primary=not employee.positions,
    )
    db.add(position)
    db.flush()
    return position
