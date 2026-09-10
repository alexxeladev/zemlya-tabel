"""
Сборка экрана «Табель вахты» за месяц (task_vahta).

Поверх `guard_duty` (данные) и `guard_payroll` (чистый расчёт): группирует
строки по МЕСТАМ РАБОТЫ и считает итоги месяца, половин и юрлиц.

**Верхний уровень группировки — ЗОНА ОБСЛУЖИВАНИЯ**, как в табеле заказчика
(колонка A). Внутри зоны карточек два вида, и это разные вещи:

* **ЭКИПАЖ ГБР** — выездная бригада зоны. Люди стоят в самом экипаже, проценты
  распределения у него свои;
* **ОБЪЕКТ** — что охраняем. Внутри посты, на постах люди. Проценты общие для
  всех строк объекта и берутся от него.

Экипажи идут первыми: руководитель охраны смотрит сначала «кто на выезде»,
потом объекты зоны.

**Деньги вычищаются здесь же, а не в UI.** Табельщик с доступом к отделу охраны
ведёт смены, но сумм не видит: прямой запрос к API тоже вернёт `null`. Правило
«кто видит деньги» берётся из `org_access.can_see_finances` — второго списка
ролей заводить нельзя.
"""
from __future__ import annotations

from calendar import monthrange
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.employees import Employee
from app.models.guard_assignments import (
    FIRST_HALF_LAST_DAY,
    GUARD_HALVES,
    GuardAssignment,
)
from app.models.guard_posts import GuardCrew, GuardSite, GuardZone
from app.schemas.guard import (
    GuardCardRead,
    GuardCompanyTotal,
    GuardHalfRead,
    GuardHalfTotal,
    GuardMonthRead,
    GuardRowRead,
    GuardZoneCardRead,
)
from app.services.company_order import company_order_by
from app.services.guard_duty import (
    guard_department_ids,
    list_assignments,
    list_zones,
    marked_days,
    shares_map,
)
from app.services.guard_payroll import (
    calculate_guard_row,
    distribute_guard_amount,
    half_bounds,
)
from app.services.org_access import can_see_finances

_ZERO = Decimal("0")

def calculate_assignment(assignment: GuardAssignment):
    """Расчёт одной строки табеля из её данных."""
    return calculate_guard_row(
        kind=assignment.kind,
        rate=Decimal(str(assignment.rate)),
        year=assignment.year,
        month=assignment.month,
        days=marked_days(assignment),
        premium={h: assignment.premium(h) for h in GUARD_HALVES},
        penalty={h: assignment.penalty(h) for h in GUARD_HALVES},
        official={h: assignment.official_payout(h) for h in GUARD_HALVES},
    )


def assignment_distribution(assignment: GuardAssignment) -> dict[int, Decimal]:
    """Разбивка «Итого начислено» строки по юрлицам её МЕСТА РАБОТЫ.

    У поста проценты берутся от объекта, у экипажа — от него самого; решает это
    один `shares_map`, чтобы «откуда проценты» не расползлось по коду.
    """
    result = calculate_assignment(assignment)
    return distribute_guard_amount(result.accrued, shares_map(assignment.place))


def _row_read(assignment: GuardAssignment, with_money: bool) -> GuardRowRead:
    result = calculate_assignment(assignment)
    position = assignment.position
    employee = position.employee if position else None

    halves = []
    for half in GUARD_HALVES:
        first, last = half_bounds(assignment.year, assignment.month, half)
        h = result.halves[half]
        halves.append(
            GuardHalfRead(
                half=half,
                first_day=first,
                last_day=last,
                shifts=h.shifts,
                salary=h.salary if with_money else None,
                premium=h.premium if with_money else None,
                penalty=h.penalty if with_money else None,
                official_payout=h.official_payout if with_money else None,
                accrued=h.accrued if with_money else None,
                net_payout=h.net_payout if with_money else None,
            )
        )

    site = assignment.post.site if assignment.post is not None else None
    zone = site.zone if site is not None else (
        assignment.crew.zone if assignment.crew is not None else None
    )
    return GuardRowRead(
        id=assignment.id,
        post_id=assignment.post_id,
        crew_id=assignment.crew_id,
        post_name=assignment.place_name,
        site_id=site.id if site else None,
        site_name=site.name if site else None,
        zone_id=zone.id if zone else None,
        zone_name=zone.name if zone else None,
        department_id=assignment.department_id or 0,
        kind=assignment.kind,
        kind_label=assignment.kind_label,
        employee_id=employee.id if employee else None,
        position_id=assignment.position_id,
        employee_name=employee.full_name if employee else None,
        tab_number=employee.tab_number if employee else None,
        # Ставка — денежное поле: табельщику её не отдаём, иначе «смены × ставка»
        # он посчитает в уме, и маскирование сумм ничего не закроет.
        rate=Decimal(str(assignment.rate)) if with_money else None,
        is_official=assignment.is_official,
        note=assignment.note,
        days=sorted(marked_days(assignment)),
        shifts=result.shifts,
        fact_hours=result.fact_hours,
        halves=halves,
        salary=result.salary if with_money else None,
        premium=result.premium if with_money else None,
        penalty=result.penalty if with_money else None,
        official_payout=result.official_payout if with_money else None,
        accrued=result.accrued if with_money else None,
        net_payout=result.net_payout if with_money else None,
        distribution=(
            {
                cid: amount
                for cid, amount in distribute_guard_amount(
                    result.accrued, shares_map(assignment.place)
                ).items()
            }
            if with_money
            else None
        ),
    )


def build_guard_month(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    department_id: int | None = None,
) -> GuardMonthRead:
    """Табель вахты за месяц, СГРУППИРОВАННЫЙ ПО ЗОНАМ обслуживания."""
    department_ids = guard_department_ids(db, actor)
    if department_id is not None:
        department_ids = [d for d in department_ids if d == department_id]

    with_money = can_see_finances(actor)
    assignments = list_assignments(db, year, month, department_ids)
    zones = list_zones(db, department_ids)

    rows_by_crew: dict[int, list[GuardRowRead]] = {}
    rows_by_site: dict[int, list[GuardRowRead]] = {}
    for assignment in assignments:
        row = _row_read(assignment, with_money)
        if assignment.crew_id is not None:
            rows_by_crew.setdefault(assignment.crew_id, []).append(row)
        elif row.site_id is not None:
            rows_by_site.setdefault(row.site_id, []).append(row)

    def _total(rows: list[GuardRowRead]) -> Decimal | None:
        if not with_money:
            return None
        return sum((r.accrued or _ZERO for r in rows), _ZERO)

    def _crew_card(crew: GuardCrew) -> GuardCardRead:
        rows = rows_by_crew.get(crew.id, [])
        return GuardCardRead(
            kind="crew", id=crew.id, name=crew.name,
            department_id=crew.department_id or 0,
            # У экипажа «объекты» — объекты его зоны: куда он выезжает.
            objects=[s.name for s in crew.zone.sites if s.is_active] if crew.zone else [],
            rows=rows, total_accrued=_total(rows),
        )

    def _site_card(site: GuardSite) -> GuardCardRead:
        rows = rows_by_site.get(site.id, [])
        return GuardCardRead(
            kind="site", id=site.id, name=site.name,
            department_id=site.department_id or 0,
            # У объекта «объекты» — его ПОСТЫ: что именно тут закрывают.
            objects=[p.name for p in site.posts if p.is_active],
            rows=rows, total_accrued=_total(rows),
        )

    def _zone_card(zone: GuardZone) -> GuardZoneCardRead:
        # Экипажи первыми: сначала «кто на выезде», потом объекты зоны.
        cards = [_crew_card(c) for c in zone.crews if c.is_active]
        cards += [_site_card(s) for s in zone.sites if s.is_active]
        rows = [r for card in cards for r in card.rows]
        return GuardZoneCardRead(
            zone_id=zone.id, zone_name=zone.name,
            department_id=zone.department_id,
            cards=cards,
            total_accrued=_total(rows),
            total_shifts=sum(r.shifts for r in rows),
        )

    zone_cards = [_zone_card(z) for z in zones]
    all_rows = [r for zc in zone_cards for card in zc.cards for r in card.rows]

    # Итоги по расчётным половинам — «выплата дважды» видна прямо в подвале.
    half_totals = []
    for half in GUARD_HALVES:
        per_half = [h for r in all_rows for h in r.halves if h.half == half]
        half_totals.append(
            GuardHalfTotal(
                half=half,
                shifts=sum(h.shifts for h in per_half),
                accrued=(
                    sum((h.accrued or _ZERO for h in per_half), _ZERO)
                    if with_money else None
                ),
                net_payout=(
                    sum((h.net_payout or _ZERO for h in per_half), _ZERO)
                    if with_money else None
                ),
            )
        )

    company_totals: list[GuardCompanyTotal] = []
    if with_money:
        totals: dict[int, Decimal] = {}
        for row in all_rows:
            for cid, amount in (row.distribution or {}).items():
                totals[cid] = totals.get(cid, _ZERO) + amount
        # Порядок юрлиц — настроенный в справочнике, как везде в системе.
        ordered = (
            db.query(Company.id)
            .filter(Company.id.in_(list(totals) or [0]))
            .order_by(*company_order_by())
            .all()
        )
        company_totals = [
            GuardCompanyTotal(company_id=cid, amount=totals[cid]) for (cid,) in ordered
        ]

    return GuardMonthRead(
        year=year,
        month=month,
        days_in_month=monthrange(year, month)[1],
        first_half_last_day=FIRST_HALF_LAST_DAY,
        departments=department_ids,
        zones=zone_cards,
        total_shifts=sum(r.shifts for r in all_rows),
        total_accrued=_total(all_rows),
        total_net_payout=(
            sum((r.net_payout or _ZERO for r in all_rows), _ZERO)
            if with_money else None
        ),
        halves=half_totals,
        company_totals=company_totals,
        can_edit=actor.role in ("admin", "manager", "timekeeper"),
        can_see_money=with_money,
    )
