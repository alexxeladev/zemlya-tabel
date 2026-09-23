"""
Снимок расчёта закрытого периода (task_stage3_historicity, часть 2).

Единственное место записи и чтения снимков.

**Запись.** `take_snapshot` зовёт закрытие периода (`close_period`) в той же
транзакции, что и смену статуса: снимок и статус «закрыт» коммитятся вместе.
Считается ЖИВЫМ расчётом ровно тем кодом, что и открытый месяц, в области
admin по отделу периода: `/payroll`, ведомость с распределением, строки кэша
дашборда, экран вахты (месяц и обе половины). Переоткрытие снимок удаляет
(`drop_snapshot`), повторное закрытие создаёт новый. Истории нет.

**Чтение.** Не по статусу периода, а по НАЛИЧИЮ снимка: позиция, чья строка
лежит в снимке месяца, берётся оттуда, а живой расчёт для неё не делается —
ни в `/payroll`, ни в ведомости, ни в дашборде. Поэтому позиция, перешедшая
после закрытия в другой отдел, не попадает в месяц дважды.

**Снимок и кэш дашборда.** `PeriodSnapshot` — помесячная модель кэша
(`dashboard_cache._MONTH_KEYED`): создание и удаление снимка поднимают версию
месяца, кэш месяца пересчитывается, а при пересчёте строки закрытых отделов
берутся из снимка (`substitute_dashboard_rows`). Своего источника для закрытых
отделов у кэша нет — разойтись им негде.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import Session, load_only

from app.models.departments import Department
from app.models.period_snapshots import PeriodSnapshot
from app.models.timesheet_periods import TimesheetPeriod
from app.services.positions import NO_DEPARTMENT, DepartmentFilter


def admin_scope():
    """Область admin для расчёта снимка: все позиции отдела, все деньги."""
    return SimpleNamespace(
        role="admin", id=None, managed_departments=[], managed_department_ids=[],
        full_name="снимок периода",
    )


# ── Чтение ────────────────────────────────────────────────────────────────────

class MonthSnapshots:
    """Снимки одного месяца, разложенные по позициям.

    Полные строки — только у снимков отделов запроса; у прочих известен лишь
    список позиций (`elsewhere`): по нему живой расчёт не посчитает дважды
    позицию, лежащую в снимке другого отдела, а тяжёлые JSON не читаются.
    """

    def __init__(self, snapshots: list, others: list | None = None) -> None:
        self.snapshots = snapshots
        #: position_id → отдел снимка
        self.dept_of: dict[int, int | None] = {}
        self.payroll: dict[int, dict] = {}
        self.statement: dict[int, dict] = {}
        self.dashboard: list[dict] = []
        self.elsewhere: set[int] = set()
        for snap in snapshots:
            for row in snap.payroll_rows:
                pid = row.get("position_id")
                if pid is not None:
                    self.payroll[pid] = row
                    self.dept_of[pid] = snap.department_id
            for row in snap.statement_rows:
                pid = row.get("position_id")
                if pid is not None:
                    self.statement[pid] = row
        for snap in others or []:
            self.elsewhere.update(int(p) for p in snap.position_ids or [])

    def __bool__(self) -> bool:
        return bool(self.snapshots) or bool(self.elsewhere)

    def has(self, position_id: int | None) -> bool:
        return position_id is not None and (
            position_id in self.payroll or position_id in self.elsewhere
        )

    def all_positions(self) -> set[int]:
        return set(self.payroll) | self.elsewhere

    def rows_in_scope(self, departments: set | None, employee_ids: set[int]) -> list[int]:
        """Позиции снимков, видимые в запросе: отдел в области и человек в
        выдаче (сотрудник видит только себя, менеджер — свои отделы)."""
        return [
            pid for pid, row in self.payroll.items()
            if (departments is None or self.dept_of[pid] in departments)
            and row.get("employee_id") in employee_ids
        ]


def load_month_snapshots(
    db: Session, year: int, month: int, departments: set | None = None,
) -> MonthSnapshots:
    """Снимки месяца. `departments` — область запроса: полные строки грузятся
    только у этих отделов, у прочих — лишь список позиций. None — все полностью."""
    heavy = load_only(
        PeriodSnapshot.department_id, PeriodSnapshot.year, PeriodSnapshot.month,
        PeriodSnapshot.position_ids, PeriodSnapshot.payroll_rows,
        PeriodSnapshot.statement_rows,
    )
    light = load_only(PeriodSnapshot.department_id, PeriodSnapshot.position_ids)
    base = db.query(PeriodSnapshot).filter(
        PeriodSnapshot.year == year, PeriodSnapshot.month == month
    )
    if departments is None:
        return MonthSnapshots(base.options(heavy).all())
    full, others = [], []
    for snap in base.options(light).all():
        (full if snap.department_id in departments else others).append(snap)
    if full:
        full = (
            db.query(PeriodSnapshot).options(heavy)
            .filter(PeriodSnapshot.id.in_([s.id for s in full])).all()
        )
    return MonthSnapshots(full, others)


def snapshot_position_ids(db: Session, year: int, month: int) -> set[int]:
    """Все позиции в снимках месяца — без чтения строк."""
    return load_month_snapshots(db, year, month, set()).all_positions()


def scope_departments(actor, department_id: DepartmentFilter) -> set | None:
    """Отделы, которые видит запрос; None — все."""
    from app.services.org_access import accessible_department_ids, is_department_scoped

    if department_id is NO_DEPARTMENT:
        return {None}
    if department_id is not None:
        return {department_id}
    if actor is not None and is_department_scoped(actor):
        return set(accessible_department_ids(actor, None))
    return None


def loan_facts(db: Session) -> dict[int, dict[tuple[int, int], Decimal]]:
    """{position_id: {(год, месяц): удержано}} — удержания займа закрытых
    месяцев. Решение заказчика: остаток займа в открытых месяцах считается с
    этими суммами как с фактом, даже если условия займа потом поменяли."""
    result: dict[int, dict[tuple[int, int], Decimal]] = {}
    for snap in db.query(PeriodSnapshot).options(load_only(
        PeriodSnapshot.year, PeriodSnapshot.month, PeriodSnapshot.loan_facts,
    )):
        for pid, amount in (snap.loan_facts or {}).items():
            result.setdefault(int(pid), {})[(snap.year, snap.month)] = Decimal(str(amount))
    return result


def guard_snapshot_view(
    db: Session, department_id: int, year: int, month: int, half: int | None,
):
    """Экран вахты отдела из снимка, если месяц закрыт; иначе None."""
    from app.schemas.guard import GuardMonthRead

    snap = (
        db.query(PeriodSnapshot)
        .options(load_only(PeriodSnapshot.guard_views))
        .filter(
            PeriodSnapshot.department_id == department_id,
            PeriodSnapshot.year == year, PeriodSnapshot.month == month,
        )
        .first()
    )
    if snap is None or not snap.guard_views:
        return None
    view = snap.guard_views.get(str(half or 0))
    return GuardMonthRead.model_validate(view) if view is not None else None


def substitute_dashboard_rows(db: Session, year: int, month: int, rows: list[dict]) -> list[dict]:
    """Строки кэша дашборда: у позиций закрытых отделов — из снимка."""
    snaps = (
        db.query(PeriodSnapshot)
        .options(load_only(PeriodSnapshot.position_ids, PeriodSnapshot.dashboard_rows))
        .filter(PeriodSnapshot.year == year, PeriodSnapshot.month == month)
        .all()
    )
    if not snaps:
        return rows
    closed = {int(p) for snap in snaps for p in snap.position_ids or []}
    kept = [r for r in rows if r.get("p") not in closed]
    return kept + [row for snap in snaps for row in snap.dashboard_rows]


# ── Запись ────────────────────────────────────────────────────────────────────

def _department_filter(period: TimesheetPeriod) -> DepartmentFilter:
    return NO_DEPARTMENT if period.department_id is None else period.department_id


def build_snapshot(db: Session, period: TimesheetPeriod) -> PeriodSnapshot:
    """Посчитать снимок периода живым расчётом (ничего не пишет)."""
    from app.services.dashboard import compute_results_for
    from app.services.dashboard_cache import serialize_results
    from app.services.payroll_statement import build_payroll_statement_with_summary
    from app.services.position_terms import terms_snapshot
    from app.services.timesheet import get_month_entries, visible_employees_for_actor

    year, month = period.year, period.month
    scope = admin_scope()
    dept_filter = _department_filter(period)
    employees = visible_employees_for_actor(db, scope, dept_filter, year=year, month=month)
    entries = get_month_entries(db, employees, year, month)
    summary, statement = build_payroll_statement_with_summary(
        db, employees, entries, year, month, scope, dept_filter,
    )
    position_ids = {p.position_id for p in summary.employees if p.position_id is not None}
    positions = {
        pos.id: pos for emp in employees for pos in emp.positions if pos.id in position_ids
    }

    results = compute_results_for(db, employees, entries, year, month, position_ids)
    extras = {
        (r.employee_id, r.position_id): (r.rounding_tail, r.unallocated_remainder)
        for r in statement.rows
    }
    dashboard_rows = serialize_results(results, extras)

    guard_views = None
    dept = db.get(Department, period.department_id) if period.department_id else None
    if dept is not None and dept.is_guard_department:
        from app.services.guard_month import build_guard_month_live

        guard_views = {
            str(half or 0): build_guard_month_live(
                db, scope, year, month, [dept.id], half
            ).model_dump(mode="json")
            for half in (None, 1, 2)
        }

    return PeriodSnapshot(
        period_id=period.id,
        department_id=period.department_id,
        year=year,
        month=month,
        position_ids=sorted(position_ids),
        payroll_rows=[p.model_dump(mode="json") for p in summary.employees],
        statement_rows=[r.model_dump(mode="json") for r in statement.rows],
        dashboard_rows=dashboard_rows,
        terms_used={
            str(pid): terms_snapshot(pos, year, month) for pid, pos in positions.items()
        },
        loan_facts={
            str(p.position_id): str(p.loan_deduction)
            for p in summary.employees
            if p.position_id is not None and _is_loan_position(employees, p)
        },
        guard_views=guard_views,
    )


def _is_loan_position(employees, row) -> bool:
    from app.services.guard_staff import loan_position

    emp = next((e for e in employees if e.id == row.employee_id), None)
    if emp is None or emp.loan_amount is None:
        return False
    pos = loan_position(emp)
    return pos is not None and pos.id == row.position_id


def take_snapshot(db: Session, period: TimesheetPeriod, actor=None) -> PeriodSnapshot:
    """Снять снимок периода и положить в сессию (коммит — снаружи, вместе со
    сменой статуса). Прежний снимок периода, если был, заменяется."""
    drop_snapshot(db, period)
    snapshot = build_snapshot(db, period)
    snapshot.created_by_id = getattr(actor, "id", None)
    db.add(snapshot)
    db.flush()
    return snapshot


def drop_snapshot(db: Session, period: TimesheetPeriod) -> bool:
    """Аннулировать снимок периода (переоткрытие). Коммит — снаружи."""
    existing = db.query(PeriodSnapshot).filter(PeriodSnapshot.period_id == period.id).first()
    if existing is None:
        return False
    db.delete(existing)
    db.flush()
    return True


# ── Снимки для ранее закрытых периодов (часть 3) ──────────────────────────────

def closed_without_snapshot(db: Session) -> list[TimesheetPeriod]:
    """Закрытые периоды, у которых снимка нет (закрыты до этапа 3)."""
    have = {pid for (pid,) in db.query(PeriodSnapshot.period_id)}
    return [
        p for p in db.query(TimesheetPeriod)
        .filter(TimesheetPeriod.status == "closed")
        .order_by(TimesheetPeriod.year, TimesheetPeriod.month, TimesheetPeriod.department_id)
        if p.id not in have
    ]


def snapshot_totals(snapshot: PeriodSnapshot) -> dict:
    """Суммы, которые снимок зафиксирует, — для отчёта перед применением."""
    rows = snapshot.statement_rows
    money = lambda key: sum((Decimal(str(r.get(key) or 0)) for r in rows), Decimal("0"))  # noqa: E731
    return {
        "rows": len(rows),
        "people": len({r["employee_id"] for r in rows}),
        "accrued": money("accrued_total"),
        "net_payout": money("net_payout"),
        "distributed": money("distribution_total"),
        "unallocated": money("unallocated_remainder"),
        "guard_tax": money("guard_tax_amount"),
        "not_calculable": sum(1 for r in rows if not r.get("is_calculable", True)),
        "negative_payout": sum(1 for r in rows if Decimal(str(r.get("net_payout") or 0)) < 0),
        "guard_views": bool(snapshot.guard_views),
    }


def backfill_snapshots(db: Session, apply: bool = False) -> list[dict]:
    """Снимки по ТЕКУЩИМ данным для всех закрытых периодов без снимка.

    `apply=False` — только отчёт: снимки считаются и отбрасываются, в базе
    ничего не меняется. `apply=True` — снимки записываются одной транзакцией
    (решение заказчика: отдельной пометки нет, они выглядят как обычные).
    Операция необратима в том смысле, что после неё закрытые месяцы перестают
    следовать за справочниками — поэтому сначала отчёт заказчику.
    """
    report = []
    for period in closed_without_snapshot(db):
        snapshot = build_snapshot(db, period)
        dept = db.get(Department, period.department_id) if period.department_id else None
        report.append({
            "period_id": period.id,
            "year": period.year,
            "month": period.month,
            "department": dept.name if dept else "Без отдела",
            **snapshot_totals(snapshot),
        })
        if apply:
            db.add(snapshot)
            db.flush()
    if apply:
        db.commit()
    else:
        db.rollback()
    return report
