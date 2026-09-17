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

**Режим отображения** (task_vahta_taxes): месяц целиком, первая половина (1–15)
или вторая (16–конец). В режиме половины ВСЕ суммы и смены — строк, карточек,
зон, подвала и юрлиц — считаются только за неё; налог — от официальной выплаты
этой половины. Поле `days` строки при этом остаётся полным набором отметок
месяца: это данные, а не итог, и фронт отправляет набор дней строки целиком —
урезанный набор стёр бы смены второй половины при первой же отметке.
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
    employer_tax_percent,
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
from app.services.timesheet_periods import is_month_closed

_ZERO = Decimal("0")

def calculate_assignment(assignment: GuardAssignment, *, tax_percent: Decimal):
    """Расчёт одной строки табеля из её данных.

    `tax_percent` обязателен и без значения по умолчанию: забытая ставка молча
    дала бы нулевой налог и заниженную базу разнесения. Берётся из
    `guard_duty.employer_tax_percent`.
    """
    return calculate_guard_row(
        kind=assignment.kind,
        rate=Decimal(str(assignment.rate)),
        year=assignment.year,
        month=assignment.month,
        days=marked_days(assignment),
        premium={h: assignment.premium(h) for h in GUARD_HALVES},
        penalty={h: assignment.penalty(h) for h in GUARD_HALVES},
        official={h: assignment.official_payout(h) for h in GUARD_HALVES},
        tax_percent=tax_percent,
    )


def assignment_distribution(
    assignment: GuardAssignment, *, tax_percent: Decimal
) -> dict[int, Decimal]:
    """Разбивка затрат строки (начислено + налог) по юрлицам её МЕСТА РАБОТЫ.

    У поста проценты берутся от объекта, у экипажа — от него самого; решает это
    один `shares_map`, чтобы «откуда проценты» не расползлось по коду.
    """
    result = calculate_assignment(assignment, tax_percent=tax_percent)
    return distribute_guard_amount(
        result.distribution_base, shares_map(assignment.place)
    )


def _row_read(
    assignment: GuardAssignment,
    with_money: bool,
    tax_percent: Decimal,
    half: int | None = None,
) -> GuardRowRead:
    result = calculate_assignment(assignment, tax_percent=tax_percent)
    if half is not None:
        # Режим половины: суммы строки — только за неё (см. шапку модуля).
        result = result.only_half(half)
    position = assignment.position
    employee = position.employee if position else None

    halves = []
    for number in sorted(result.halves):
        first, last = half_bounds(assignment.year, assignment.month, number)
        h = result.halves[number]
        halves.append(
            GuardHalfRead(
                half=number,
                first_day=first,
                last_day=last,
                shifts=h.shifts,
                salary=h.salary if with_money else None,
                premium=h.premium if with_money else None,
                penalty=h.penalty if with_money else None,
                official_payout=h.official_payout if with_money else None,
                accrued=h.accrued if with_money else None,
                net_payout=h.net_payout if with_money else None,
                net_payout_exact=h.net_payout_exact if with_money else None,
                tax=h.tax if with_money else None,
                distribution_base=h.distribution_base if with_money else None,
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
        net_payout_exact=result.net_payout_exact if with_money else None,
        tax=result.tax if with_money else None,
        distribution_base=result.distribution_base if with_money else None,
        # База разнесения — затраты: начислено + налог на официальную часть.
        distribution=(
            distribute_guard_amount(
                result.distribution_base, shares_map(assignment.place)
            )
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
    half: int | None = None,
) -> GuardMonthRead:
    """Табель вахты за месяц, СГРУППИРОВАННЫЙ ПО ЗОНАМ обслуживания.

    `half` — режим отображения: None — месяц целиком, 1 или 2 — расчётная
    половина; суммы и смены тогда считаются только за неё.
    """
    if half is not None and half not in GUARD_HALVES:
        raise ValueError(f"Неизвестная половина месяца: {half}")
    department_ids = guard_department_ids(db, actor)
    if department_id is not None:
        department_ids = [d for d in department_ids if d == department_id]

    with_money = can_see_finances(actor)
    tax_percent = employer_tax_percent(db)
    # Закрытый месяц: бэк отклонит любую правку назначения, поэтому экран гасит
    # управление заранее. Охранное подразделение обычно одно; если их несколько
    # и закрыто хотя бы одно — экран «все подразделения» только для чтения, а
    # открытое правится через выбор своего подразделения.
    period_closed = any(is_month_closed(db, d, year, month) for d in department_ids)
    assignments = list_assignments(db, year, month, department_ids)
    zones = list_zones(db, department_ids)

    rows_by_crew: dict[int, list[GuardRowRead]] = {}
    rows_by_site: dict[int, list[GuardRowRead]] = {}
    for assignment in assignments:
        row = _row_read(assignment, with_money, tax_percent, half)
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
    shown_halves = GUARD_HALVES if half is None else (half,)
    half_totals = []
    for number in shown_halves:
        per_half = [h for r in all_rows for h in r.halves if h.half == number]
        half_totals.append(
            GuardHalfTotal(
                half=number,
                shifts=sum(h.shifts for h in per_half),
                accrued=(
                    sum((h.accrued or _ZERO for h in per_half), _ZERO)
                    if with_money else None
                ),
                net_payout=(
                    sum((h.net_payout or _ZERO for h in per_half), _ZERO)
                    if with_money else None
                ),
                tax=(
                    sum((h.tax or _ZERO for h in per_half), _ZERO)
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

    if half is None:
        first_day, last_day = 1, monthrange(year, month)[1]
    else:
        first_day, last_day = half_bounds(year, month, half)

    def _money_sum(values) -> Decimal | None:
        return sum((v or _ZERO for v in values), _ZERO) if with_money else None

    return GuardMonthRead(
        year=year,
        month=month,
        view_half=half,
        first_day=first_day,
        last_day=last_day,
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
        total_tax=_money_sum(r.tax for r in all_rows),
        total_distribution_base=_money_sum(r.distribution_base for r in all_rows),
        total_distribution=_money_sum(t.amount for t in company_totals),
        employer_tax_percent=tax_percent if with_money else None,
        halves=half_totals,
        company_totals=company_totals,
        can_edit=actor.role in ("admin", "manager", "timekeeper") and not period_closed,
        period_closed=period_closed,
        can_see_money=with_money,
    )
