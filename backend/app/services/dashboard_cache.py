"""Кэш помесячных итогов дашборда: чтение, запись, инвалидация.

Что кэшируется — см. `models/dashboard_cache.py`. Здесь три вещи:

1. `serialize_results` / `restore_results` — итоги месяца по рабочим местам
   туда и обратно. В строке ровно те поля, которые дашборд складывает
   (часы, норма, переработка, суммы по категориям, разрез по юрлицам,
   `is_calculable`) плюс `department_id` для фильтра видимости. Ни ФИО, ни
   ставок: это агрегат, а не карточка.
2. `current_versions` / `bump` — счётчики `data_versions`.
3. слушатель `after_flush` — единственное место, решающее, какие изменения
   обесценивают кэш. Модели с датой/месяцем бьют по своему месяцу, справочные
   — по всем (ключ `reference`). Новая модель, влияющая на расчёт, обязана
   попасть в `_MONTH_KEYED` или `_REFERENCE`, иначе дашборд покажет старое.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import event, func, text
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.company_shares import (
    CompanyShareOverride,
    DepartmentCompanyShare,
    EmployeeCompanyShare,
)
from app.models.dashboard_cache import DashboardMonthCache, DataVersion
from app.models.department_quantities import DepartmentQuantity
from app.models.departments import Department
from app.models.employee_absences import EmployeeAbsence
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.guard_assignments import GuardAssignment, GuardShift
from app.models.guard_job_titles import GuardJobTitle
from app.models.guard_posts import GuardCrew, GuardCrewShare, GuardPost, GuardSite, GuardSiteShare
from app.models.guard_settings import GuardSettings
from app.models.loan_deductions import LoanDeduction
from app.models.night_shifts import NightShift
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod

REFERENCE_KEY = "reference"


def month_key(year: int, month: int) -> str:
    return f"month:{year:04d}-{month:02d}"


# ── Что обесценивает кэш ──────────────────────────────────────────────────────

def _month_of(obj) -> tuple[int, int] | None:
    """(год, месяц) записи с датой/месяцем; None — это не помесячная запись."""
    d = getattr(obj, "work_date", None)
    if isinstance(d, date):
        return d.year, d.month
    y, m = getattr(obj, "year", None), getattr(obj, "month", None)
    if isinstance(y, int) and isinstance(m, int):
        return y, m
    return None


#: Помесячные записи: правка бьёт по СВОЕМУ месяцу.
_MONTH_KEYED: tuple[type, ...] = (
    TimesheetEntry, EmployeeAbsence, NightShift, EmployeeAdjustment, LoanDeduction,
    CompanyShareOverride, GuardAssignment, GuardShift, TimesheetPeriod, DepartmentQuantity,
)
#: Справочные данные: расчёт любого месяца читает их живьём — бьём по всем.
#: `Employee` — из-за займа (сумма/срок/старт) и дат приёма/увольнения.
_REFERENCE: tuple[type, ...] = (
    Employee, EmployeePosition, Department, Company, Schedule, ProductionCalendar,
    EmployeeCompanyShare, DepartmentCompanyShare, GuardJobTitle, GuardSettings,
    GuardCrew, GuardCrewShare, GuardSite, GuardSiteShare, GuardPost,
)


def _touched_keys(session: Session) -> set[str]:
    keys: set[str] = set()
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, (DashboardMonthCache, DataVersion)):
            continue
        if isinstance(obj, _MONTH_KEYED):
            ym = _month_of(obj)
            if ym is not None:
                keys.add(month_key(*ym))
                # Смена месяца у записи (сдвинули дату) — старый месяц тоже
                # поехал; истории атрибутов не разбираем, бьём по справочнику.
                if obj in session.dirty and _date_changed(obj):
                    keys.add(REFERENCE_KEY)
            else:
                keys.add(REFERENCE_KEY)
        elif isinstance(obj, _REFERENCE):
            keys.add(REFERENCE_KEY)
    return keys


def _date_changed(obj) -> bool:
    from sqlalchemy import inspect

    state = inspect(obj)
    for attr in ("work_date", "year", "month"):
        if attr in state.attrs and state.attrs[attr].history.has_changes():
            return True
    return False


_DIRTY_KEYS = "dashboard_dirty_keys"

_UPSERT_VERSION = text(
    "INSERT INTO data_versions (key, version) VALUES (:key, 1) "
    "ON CONFLICT (key) DO UPDATE SET version = data_versions.version + 1"
)


@event.listens_for(Session, "after_flush")
def _collect_dirty_keys(session: Session, flush_context) -> None:
    """Запомнить, какие ключи поменял этот flush; поднимаются они ПОСЛЕ коммита.

    Поднимать счётчик в самой транзакции нельзя: UPDATE строки `data_versions`
    держит на ней блокировку до коммита, и два табельщика одного месяца (разные
    отделы, `FOR SHARE` на разных периодах) встали бы друг за другом на одной
    строке версии — ровно то, что запрещает протокол блокировок
    (`test_pg_period_race::test_concurrent_writers_do_not_block_each_other`).
    """
    keys = _touched_keys(session)
    if keys:
        session.info.setdefault(_DIRTY_KEYS, set()).update(keys)


@event.listens_for(Session, "after_commit")
def _bump_after_commit(session: Session) -> None:
    """Поднять версии — отдельным соединением с автокоммитом, СТРОГО после того,
    как данные закоммичены. Порядок важен: дашборд читает версию ПЕРЕД данными,
    и раз версия поднимается позже данных, «старые данные под новой версией» в
    кэш попасть не могут; «новые данные под старой» — могут, но их следующая
    проверка версии отбросит. Откат транзакции ключи просто выбрасывает."""
    keys = session.info.pop(_DIRTY_KEYS, None)
    if not keys:
        return
    bump_versions(session.get_bind(), keys)


@event.listens_for(Session, "after_rollback")
@event.listens_for(Session, "after_soft_rollback")
def _forget_dirty_keys(session: Session, *_) -> None:
    session.info.pop(_DIRTY_KEYS, None)


def bump_versions(bind, keys: Iterable[str]) -> None:
    """Один UPSERT на ключ своим соединением; коммитится сразу."""
    with bind.connect() as conn:
        for key in sorted(keys):
            conn.execute(_UPSERT_VERSION, {"key": key})
        conn.commit()


# ── Версии ────────────────────────────────────────────────────────────────────

def current_versions(db: Session, year: int, month: int) -> tuple[int, int]:
    """(версия месяца, версия справочника); отсутствующий ключ — 0."""
    rows = {
        r.key: r.version
        for r in db.query(DataVersion).filter(DataVersion.key.in_([month_key(year, month), REFERENCE_KEY]))
    }
    return rows.get(month_key(year, month), 0), rows.get(REFERENCE_KEY, 0)


# ── Сериализация итогов ───────────────────────────────────────────────────────

_MONEY = ("total_amount", "base_amount", "overtime_amount", "off_schedule_amount", "holiday_amount")
_HOURS = ("total_hours", "overtime_hours", "norm_hours")


def serialize_results(
    results: Iterable[tuple],
    extras: dict[tuple[int, int | None], tuple[Decimal, Decimal]] | None = None,
) -> list[dict]:
    """`extras` — по (employee_id, position_id) хвост округления «к выплате» и
    нераспределённый остаток из ведомости; нет строки — нули."""
    rows = []
    for emp, position, p in results:
        pid = position.id if position is not None else None
        tail, rest = (extras or {}).get((emp.id, pid), (Decimal(0), Decimal(0)))
        rows.append({
            "e": emp.id,
            "p": pid,
            "d": position.department_id if position is not None else None,
            "rt": str(tail),
            "ur": str(rest),
            **{k: str(getattr(p, k)) for k in _MONEY},
            **{k: (None if getattr(p, k) is None else str(getattr(p, k))) for k in _HOURS},
            "c": bool(p.is_calculable),
            "bc": [[b.company_id, str(b.total)] for b in p.breakdown_by_company],
        })
    return rows


class CachedPayroll:
    """Итог одного рабочего места из кэша — те же поля, что читает дашборд."""

    __slots__ = ("total_amount", "base_amount", "overtime_amount", "off_schedule_amount",
                 "holiday_amount", "total_hours", "overtime_hours", "norm_hours",
                 "is_calculable", "breakdown_by_company", "rounding_tail", "unallocated_remainder")

    def __init__(self, row: dict) -> None:
        for k in _MONEY:
            setattr(self, k, Decimal(row[k]))
        for k in _HOURS:
            setattr(self, k, None if row[k] is None else Decimal(row[k]))
        self.is_calculable = row["c"]
        self.rounding_tail = Decimal(row.get("rt", "0"))
        self.unallocated_remainder = Decimal(row.get("ur", "0"))
        self.breakdown_by_company = tuple(_Breakdown(cid, Decimal(t)) for cid, t in row["bc"])


class _Breakdown:
    __slots__ = ("company_id", "total")

    def __init__(self, company_id: int, total: Decimal) -> None:
        self.company_id, self.total = company_id, total


class _Ref:
    """Заглушка (сотрудник / позиция) с полями, которые дашборд читает из них."""

    __slots__ = ("id", "department_id")

    def __init__(self, id_, department_id=None) -> None:
        self.id, self.department_id = id_, department_id


def restore_results(
    rows: list[dict],
    allowed_departments: set[int] | None,
    only_employee_id: int | None = None,
) -> list[tuple]:
    """Кортежи (сотрудник, позиция, итог) как у живого расчёта, суженные по
    видимости актора: None — видны все отделы (admin/accountant);
    `only_employee_id` — роль employee, видит только себя (все свои места)."""
    out = []
    for r in rows:
        if only_employee_id is not None and r["e"] != only_employee_id:
            continue
        if allowed_departments is not None and r["d"] not in allowed_departments:
            continue
        out.append((_Ref(r["e"]), _Ref(r["p"], r["d"]) if r["p"] is not None else None, CachedPayroll(r)))
    return out


# ── Чтение и запись ───────────────────────────────────────────────────────────

def load_month(db: Session, year: int, month: int) -> list[dict] | None:
    """Строки кэша, если он посчитан на ТЕКУЩИХ версиях; иначе None."""
    mv, rv = current_versions(db, year, month)
    row = db.query(DashboardMonthCache).filter_by(year=year, month=month).first()
    if row is None or row.month_version != mv or row.reference_version != rv:
        return None
    return row.rows


def store_month(db: Session, year: int, month: int, versions: tuple[int, int], rows: list[dict]) -> None:
    """Записать итоги на версиях, снятых ДО расчёта: правка, пришедшая во время
    расчёта, поднимет версию выше и кэш не примется. Коммитит сам — это не
    часть пользовательской транзакции, дашборд её не открывает.

    UPSERT, а не select+insert: два дашборда, открытые одновременно на промахе,
    оба досчитают месяц, и второй INSERT упёрся бы в unique (year, month).
    """
    from sqlalchemy.dialects import postgresql, sqlite

    dialect = db.get_bind().dialect.name
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    table = DashboardMonthCache.__table__
    stmt = insert(table).values(
        year=year, month=month, month_version=versions[0], reference_version=versions[1], rows=rows,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.year, table.c.month],
        set_={
            "month_version": stmt.excluded.month_version,
            "reference_version": stmt.excluded.reference_version,
            "rows": stmt.excluded.rows,
            "computed_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def drop_cache(db: Session) -> int:
    """Стереть все строки кэша. Для записей МИМО ORM (bulk-сиды, SQL-правки
    руками, миграции данных): слушатель `after_flush` их не видит, и версии
    не двинутся. Пустой кэш — просто промах при следующем открытии."""
    return db.query(DashboardMonthCache).delete(synchronize_session=False)
