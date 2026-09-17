"""
Период работы рабочего места — границы заполнения табеля (task_employment_period).

**Единственное место, решающее, можно ли заполнить день.** Табель ведётся только
в границах работы ПОЗИЦИИ: от даты приёма до даты увольнения ВКЛЮЧИТЕЛЬНО.
Пустая дата означает отсутствие границы с этой стороны, поэтому сотрудник без
дат (таких большинство) заполняется ровно как раньше.

Границы позиции — ПЕРЕСЕЧЕНИЕ её собственных дат с датами человека
(`Employee.hire_date` / `.dismissal_date`). Даты человека всегда внешняя
граница: кадровик увольняет сотрудника один раз, и это закрывает все его
рабочие места разом. Хранить даты только на позиции было нельзя — правка на
человеке не дошла бы до позиций, табель продолжил бы заполняться, и ошибка
всплыла бы в ведомости через месяц.

Даты берутся с ПОЗИЦИИ, а не с человека, потому что у совместителя одна работа
может быть закрыта, а вторая продолжаться.

Правило применяется во всех четырёх точках мутации табеля:

- `services.timesheet.upsert_cell` / `upsert_cells_batch` — часы;
- `services.absences.set_absence` — коды ОТ/ДО/Б/Н;
- `services.night_shifts.set_night_shift` — выходы в ночь;
- `services.timesheet.build_autofill_preview` — автозаполнение по графику.

**Снятие отметки не блокируется никогда.** Запрет касается ЗАПОЛНЕНИЯ: иначе
часы, оставшиеся за новой границей, нельзя было бы убрать — ни руками, ни
автоматической очисткой при смене даты.

Зеркало правила на фронте — `frontend/src/utils/employment.ts` (оно красит и
блокирует дни). Правишь одно — правь второе, иначе экран разойдётся с бэком.

Дата увольнения позиции НЕ трогает `is_active`: тот про снятие рабочего места с
учёта, и `visible_positions` по нему убирает строку из табеля целиком — вместе с
днями, которые человек до увольнения отработал. Закрытая позиция остаётся
строкой, а дни после даты просто заблокированы.
"""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.timesheet_entries import TimesheetEntry


class OutsideEmploymentPeriod(Exception):
    """День вне периода работы рабочего места — заполнять его нельзя.

    Роутеры переводят в 422 с этим же текстом: причина адресована пользователю,
    а не разработчику.
    """


def _fmt(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def employment_bounds(
    employee: Employee | None, position: EmployeePosition | None
) -> tuple[date | None, date | None]:
    """Границы работы рабочего места: (с какого дня, по какой ВКЛЮЧИТЕЛЬНО).

    Пересечение дат человека и позиции — берётся более поздняя дата приёма и
    более ранняя дата увольнения. `None` с любой стороны означает, что границы
    нет: у человека без дат и позиции без дат вернётся `(None, None)`.
    """
    starts = [
        d
        for d in (
            getattr(employee, "hire_date", None),
            getattr(position, "hire_date", None),
        )
        if d is not None
    ]
    ends = [
        d
        for d in (
            getattr(employee, "dismissal_date", None),
            getattr(position, "dismissal_date", None),
        )
        if d is not None
    ]
    return (max(starts) if starts else None, min(ends) if ends else None)


def is_within_employment(
    employee: Employee | None, position: EmployeePosition | None, work_date: date
) -> bool:
    """Входит ли день в период работы. Границы ВКЛЮЧИТЕЛЬНЫЕ: уволен
    пятнадцатого — пятнадцатое ещё рабочий день."""
    start, end = employment_bounds(employee, position)
    if start is not None and work_date < start:
        return False
    if end is not None and work_date > end:
        return False
    return True


def employment_reason(
    employee: Employee | None, position: EmployeePosition | None, work_date: date
) -> str | None:
    """Человекочитаемая причина отказа или `None`, если день в периоде.

    Один текст на API и на подсказку в табеле — расходиться им незачем.
    """
    start, end = employment_bounds(employee, position)
    if start is not None and work_date < start:
        return f"{_fmt(work_date)} — до приёма на работу ({_fmt(start)})"
    if end is not None and work_date > end:
        return f"{_fmt(work_date)} — после увольнения ({_fmt(end)})"
    return None


def is_within_any_employment(employee: Employee, work_date: date) -> bool:
    """Работает ли человек в этот день ХОТЬ ГДЕ-ТО.

    Код отсутствия (ОТ/ДО/Б/Н) ставится на человека целиком, а не на рабочее
    место, поэтому и границы у него общие: день годится, пока открыта хотя бы
    одна позиция. У совместителя с одной закрытой работой вторая продолжается —
    отметить отсутствие он по-прежнему может.
    """
    positions = employee.active_positions or [employee.primary_position]
    return any(is_within_employment(employee, p, work_date) for p in positions)


def check_any_employment_period(
    db: Session, employee_id: int, work_date: date
) -> None:
    """Бросает `OutsideEmploymentPeriod`, если человек в этот день не работает
    ни на одном рабочем месте."""
    emp = db.get(Employee, employee_id)
    if emp is None:
        return
    if is_within_any_employment(emp, work_date):
        return
    # Причина берётся от основной позиции: границы у всех уже вышли, и текст
    # про приём/увольнение у них один и тот же.
    reason = employment_reason(emp, emp.primary_position, work_date)
    raise OutsideEmploymentPeriod(reason or f"{_fmt(work_date)} — вне периода работы")


def check_employment_period(
    db: Session, employee_id: int, work_date: date, position_id: int | None = None
) -> None:
    """Бросает `OutsideEmploymentPeriod`, если день вне периода работы.

    Ячейка без `position_id` (заведена до появления позиций) относится к
    ОСНОВНОЙ — так же, как её читает `_check_period_lock`.
    """
    emp = db.get(Employee, employee_id)
    if emp is None:
        return  # сотрудника нет — пусть отработает проверка внешнего ключа
    position = emp.position_by_id(position_id)
    reason = employment_reason(emp, position, work_date)
    if reason is not None:
        raise OutsideEmploymentPeriod(reason)


# ── Очистка часов при изменении дат ───────────────────────────────────────────
#
# Смена даты приёма или увольнения сдвигает границы, и часы, оказавшиеся за
# ними, надо убрать. Делается это ТОЛЬКО после подтверждения пользователем и
# ТОЛЬКО там, где период открыт для правки: закрытый месяц бухгалтерия уже
# видела, и трогать его нельзя даже ради согласованности.


@dataclass
class ClearingReport:
    """Что будет удалено при смене дат — числа для предупреждения."""

    days: int = 0
    hours: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    # Часы за границей, которые останутся: их период закрыт для правки.
    locked_days: int = 0
    locked_hours: Decimal = Decimal("0")
    locked_months: list[str] = field(default_factory=list)
    entry_ids: list[int] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.entry_ids) or self.locked_days > 0

    def as_dict(self) -> dict:
        return {
            "days": self.days,
            "hours": str(self.hours),
            "amount": str(self.amount),
            "locked_days": self.locked_days,
            "locked_hours": str(self.locked_hours),
            "locked_months": self.locked_months,
        }


_MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def _month_label(year: int, month: int) -> str:
    return f"{_MONTH_NAMES[month - 1]} {year}"


def bounds_snapshot(employee: Employee) -> dict[int, tuple[date | None, date | None]]:
    """Границы всех рабочих мест сотрудника — чтобы понять, сдвинулись ли они.

    Обычное сохранение карточки дат не трогает, а отчёт об очистке читает ВСЕ
    часы сотрудника. Сравнение снимков «до» и «после» оставляет этот запрос
    только там, где границы действительно поехали.
    """
    return {p.id: employment_bounds(employee, p) for p in employee.positions}


def entries_outside_employment(db: Session, employee: Employee) -> list[TimesheetEntry]:
    """Часы сотрудника, оказавшиеся вне границ своих рабочих мест.

    Читает границы из ORM-объектов, поэтому вызывать надо ПОСЛЕ присвоения новых
    дат (до коммита): тогда видно, что именно уедет за новую границу.
    """
    entries = (
        db.query(TimesheetEntry)
        .filter(TimesheetEntry.employee_id == employee.id)
        .all()
    )
    outside: list[TimesheetEntry] = []
    for entry in entries:
        position = employee.position_by_id(entry.position_id)
        if not is_within_employment(employee, position, entry.work_date):
            outside.append(entry)
    return outside


def _editable_periods(
    db: Session, employee: Employee, entries: list[TimesheetEntry]
) -> set[tuple[int | None, int, int]]:
    """Какие (отдел, год, месяц) из затронутых открыты для правки.

    ОДНИМ запросом на весь отчёт, а не по запросу на ячейку: сдвиг даты приёма
    на год задевает сотни ячеек, и запрос на строку здесь превратился бы в
    сотни round-trip-ов (см. «Производительность» в CLAUDE.md).

    Периодов НЕ создаёт: отчёт — операция чтения, и заводить месяцы, в которые
    никто не заходил, он не вправе. Месяца в таблице нет — значит он ещё
    черновик, править можно.
    """
    from sqlalchemy import tuple_ as sa_tuple

    from app.models.timesheet_periods import TimesheetPeriod
    from app.services.timesheet_periods import can_edit_cells

    keys = {_period_key(employee, e) for e in entries}
    if not keys:
        return set()

    # NULL-отдел в кортежное сравнение не годится — его отбираем отдельно.
    with_dept = [k for k in keys if k[0] is not None]
    rows: list[TimesheetPeriod] = []
    if with_dept:
        rows += (
            db.query(TimesheetPeriod)
            .filter(
                sa_tuple(
                    TimesheetPeriod.department_id,
                    TimesheetPeriod.year,
                    TimesheetPeriod.month,
                ).in_(with_dept)
            )
            # Очистка часов — запись в месяц: строки периодов под разделяемой
            # блокировкой, как у любой записи (см. `lock_period`, п.1.5).
            .with_for_update(read=True, of=TimesheetPeriod)
            # Статус — по перечитанной под блокировкой строке, как в `lock_period`.
            .populate_existing()
            .all()
        )
    no_dept = [k for k in keys if k[0] is None]
    if no_dept:
        rows += (
            db.query(TimesheetPeriod)
            .filter(
                TimesheetPeriod.department_id.is_(None),
                sa_tuple(TimesheetPeriod.year, TimesheetPeriod.month).in_(
                    [(y, m) for _, y, m in no_dept]
                ),
            )
            .with_for_update(read=True, of=TimesheetPeriod)
            # Статус — по перечитанной под блокировкой строке, как в `lock_period`.
            .populate_existing()
            .all()
        )

    locked = {
        (r.department_id, r.year, r.month) for r in rows if not can_edit_cells(r)
    }
    return keys - locked


def _period_key(
    employee: Employee, entry: TimesheetEntry
) -> tuple[int | None, int, int]:
    """Период ячейки — отдел её ПОЗИЦИИ и месяц, как в `_check_period_lock`."""
    position = employee.position_by_id(entry.position_id)
    department_id = position.department_id if position is not None else None
    return (department_id, entry.work_date.year, entry.work_date.month)


def _amount_of(db: Session, employee: Employee, entries: list[TimesheetEntry]) -> Decimal:
    """На какую сумму уменьшится начисление, если убрать эти часы.

    Считается ОДНИМ И ТЕМ ЖЕ `build_payroll_summary`, что и табель с ведомостью,
    дважды за месяц: со всеми часами и без удаляемых. Своей формулы здесь нет и
    быть не должно — иначе предупреждение показывало бы не ту сумму, на которую
    реально поедет ведомость.
    """
    from app.services.payroll_statement import build_payroll_summary

    doomed = {e.id for e in entries}
    months = {(e.work_date.year, e.work_date.month) for e in entries}

    total = Decimal("0")
    for year, month in sorted(months):
        first = date(year, month, 1)
        last = date(year, month, _cal.monthrange(year, month)[1])
        month_entries = (
            db.query(TimesheetEntry)
            .filter(
                TimesheetEntry.employee_id == employee.id,
                TimesheetEntry.work_date >= first,
                TimesheetEntry.work_date <= last,
            )
            .all()
        )
        kept = [e for e in month_entries if e.id not in doomed]
        before = build_payroll_summary(db, [employee], month_entries, year, month)
        after = build_payroll_summary(db, [employee], kept, year, month)
        total += before.grand_total - after.grand_total
    return total


def clearing_report(db: Session, employee: Employee) -> ClearingReport:
    """Сколько дней и на какую сумму очистится при текущих (уже присвоенных)
    датах. Ничего не меняет."""
    report = ClearingReport()
    outside = entries_outside_employment(db, employee)
    if not outside:
        return report

    editable = _editable_periods(db, employee, outside)
    clearable: list[TimesheetEntry] = []
    locked: list[TimesheetEntry] = []
    for entry in outside:
        target = clearable if _period_key(employee, entry) in editable else locked
        target.append(entry)

    if clearable:
        report.days = len({e.work_date for e in clearable})
        report.hours = sum((e.hours for e in clearable), Decimal("0"))
        report.entry_ids = [e.id for e in clearable]
        report.amount = _amount_of(db, employee, clearable)

    if locked:
        report.locked_days = len({e.work_date for e in locked})
        report.locked_hours = sum((e.hours for e in locked), Decimal("0"))
        report.locked_months = [
            _month_label(y, m)
            for y, m in sorted({(e.work_date.year, e.work_date.month) for e in locked})
        ]
    return report


def clear_entries_outside(
    db: Session, actor: Employee, employee: Employee, report: ClearingReport
) -> int:
    """Удалить часы, попавшие за новые границы. Возвращает число удалённых ячеек.

    Удаляются только те, что отобрал `clearing_report` — закрытые периоды в него
    не попали. Каждое удаление пишется в audit log: очистка необратима, и по
    журналу должно быть видно, чья правка дат её вызвала.
    """
    from app.core.audit import log_action

    if not report.entry_ids:
        return 0

    entries = (
        db.query(TimesheetEntry).filter(TimesheetEntry.id.in_(report.entry_ids)).all()
    )
    for entry in entries:
        log_action(
            db, actor, "timesheet_entry", entry.id, "delete",
            before={
                "hours": str(entry.hours),
                "work_date": str(entry.work_date),
                "reason": "вне периода работы",
            },
        )
        db.delete(entry)
    db.flush()
    return len(entries)
