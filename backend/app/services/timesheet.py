from __future__ import annotations

import calendar as _cal
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.audit import log_action
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.services.employment_period import (
    check_employment_period,
    employment_bounds,
    is_within_employment,
)
from app.services.org_access import accessible_department_ids, is_department_scoped
from app.services.position_terms import month_segments
from app.services.positions import (
    NO_DEPARTMENT,
    DepartmentFilter,
    in_department,
    in_departments,
    visible_positions,
)


def _position_label(employee: Employee, position) -> str:
    """ФИО для отчёта автозаполнения; у совместителя — с должностью, иначе
    в списке пропущенных две одинаковые строки без объяснения."""
    if position is None or position.is_primary and len(employee.active_positions) <= 1:
        return employee.full_name
    return f"{employee.full_name} — {position.display_title}"


def visible_employees_for_actor(
    db: Session,
    actor: Employee,
    department_id: DepartmentFilter = None,
    year: int | None = None,
    month: int | None = None,
) -> list[Employee]:
    """Сотрудники выборки. `department_id`: id отдела, `NO_DEPARTMENT` — группа
    «Без отдела», `None` — фильтр не задан (все доступные actor-у)."""
    q = db.query(Employee).filter(Employee.is_system_admin == False)  # noqa: E712

    if year is not None and month is not None:
        period_start = date(year, month, 1)
        q = q.filter(
            or_(
                Employee.is_active == True,  # noqa: E712
                Employee.dismissal_date >= period_start,
            )
        )
    else:
        q = q.filter(Employee.is_active == True)  # noqa: E712

    if actor.role == "employee":
        return q.filter(Employee.id == actor.id).all()

    if is_department_scoped(actor):
        # Отделы, которыми менеджер руководит (или которые ведёт табельщик,
        # task_timekeeper_role) — их может быть несколько, и это НЕ его
        # собственный department_id.
        # Группа «Без отдела» отделов не имеет, а доступ у этих ролей — по
        # отделам: показывать нечего (роутер отвечает на неё 403 ещё раньше).
        if department_id is NO_DEPARTMENT:
            return []
        dept_ids = accessible_department_ids(actor, department_id)
        if not dept_ids:
            return []
        return q.filter(in_departments(dept_ids)).all()

    # admin / accountant
    if department_id is NO_DEPARTMENT:
        q = q.filter(in_department(None))
    elif department_id is not None:
        q = q.filter(in_department(department_id))
    return q.all()


def get_month_entries(
    db: Session,
    employees: list[Employee],
    year: int,
    month: int,
) -> list[TimesheetEntry]:
    if not employees:
        return []
    days_in_month = _cal.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_in_month)
    emp_ids = [e.id for e in employees]
    return (
        db.query(TimesheetEntry)
        .filter(
            TimesheetEntry.employee_id.in_(emp_ids),
            TimesheetEntry.work_date >= start,
            TimesheetEntry.work_date <= end,
        )
        .all()
    )


def compute_extra_companies_by_employee(
    employees: list[Employee],
    entries: list[TimesheetEntry],
    company_order: dict[int, int] | None = None,
) -> dict[int, list[int]]:
    """Юрлица сотрудника помимо основного — по одному проходу по ячейкам.

    Раньше на каждого сотрудника сканировался ВЕСЬ список ячеек месяца, то есть
    O(сотрудники × ячейки): на 200 сотрудниках это 1,2 млн итераций и ~10 с из
    13 с всего запроса табеля. Результат тот же: ключ есть у каждого сотрудника
    (в том числе без часов), основная компания из списка исключена. Порядок —
    настроенный в справочнике (`company_order`: id → место, из
    services/company_order); без него, как и раньше, по возрастанию id.
    """
    companies_by_emp: dict[int, set[int]] = {emp.id: set() for emp in employees}
    for entry in entries:
        bucket = companies_by_emp.get(entry.employee_id)
        # Ячейка сотрудника, которого нет в выборке (другой отдел), нас не касается
        if bucket is not None:
            bucket.add(entry.company_id)

    result: dict[int, list[int]] = {}
    for emp in employees:
        emp_company_ids = companies_by_emp[emp.id]
        if emp.default_company_id is not None:
            emp_company_ids.discard(emp.default_company_id)
        result[emp.id] = sorted(
            emp_company_ids,
            key=lambda cid: (
                (company_order.get(cid, len(company_order)), cid)
                if company_order is not None else (0, cid)
            ),
        )
    return result


def _resolve_cell_target(
    db: Session, employee_id: int, position_id: int | None
) -> tuple[Employee | None, "EmployeePosition | None"]:
    """(сотрудник, его рабочее место ячейки) — ОДИН раз на операцию.

    Все проверки ячейки (период, период работы, охранная позиция) и сама запись
    получают эти объекты параметрами, а не грузят сотрудника заново по id:
    после `populate_existing` в `lock_period` и каждого flush объект в сессии
    истекает, и любой повторный `db.get` + `position_by_id` стоил три SELECT
    (сотрудник, позиции, отдел). На одной ячейке это давало +5 запросов и
    +80–140 мс (диагностика после этапа 1).
    """
    emp = db.get(Employee, employee_id)
    if emp is None:
        return None, None
    return emp, emp.position_by_id(position_id)


def _resolve_position_id(
    db: Session, employee_id: int, position_id: int | None
) -> int | None:
    """Позиция ячейки: явно переданная либо основная (task_positions ч.A).

    Ввод часов по конкретному рабочему месту появится в части B; пока каждая
    ячейка попадает на основную позицию — ровно то, что было до позиций.
    """
    emp = db.get(Employee, employee_id)
    if emp is None:
        return position_id
    position = emp.position_by_id(position_id)
    return position.id if position is not None else None


class CellConflict(Exception):
    """Ячейку успел изменить другой редактор (task_stage1 п.1.5).

    `current_hours` — что лежит в базе сейчас (None — ячейки нет): отказ обязан
    назвать текущее значение, иначе пользователь не поймёт, что именно не так.
    """

    def __init__(self, current_hours: int | None) -> None:
        self.current_hours = current_hours
        super().__init__(
            "ячейка удалена" if current_hours is None else f"сейчас в ней {current_hours} ч"
        )


def _check_expected_version(
    existing: TimesheetEntry | None, expected_version: int | None
) -> None:
    """Версия, которую видел клиент, против базы. 0 — «ячейки нет»; None —
    клиент версию не прислал, проверки нет (батч, автозаполнение)."""
    if expected_version is None:
        return
    current = existing.version if existing is not None else 0
    if current != expected_version:
        raise CellConflict(int(existing.hours) if existing is not None else None)


_CELL_UNIQUE = "uq_timesheet_employee_date_company"


@contextmanager
def _cell_write(db: Session, lookup):
    """Запись ячейки (flush + commit) — гонку редакторов превращает в `CellConflict`.

    Проверка `expected_version` ловит устаревший экран. Двоих, прошедших её
    ОДНОВРЕМЕННО, ловит сама база: `version_id_col` ставит версию в WHERE
    UPDATE/DELETE (опоздавший получает `StaleDataError`), а двойную вставку —
    unique-ключ ячейки (`IntegrityError`). Обе ошибки всплывают уже на FLUSH
    внутри `_upsert_cell_no_commit`, а не на коммите, — поэтому менеджер обязан
    охватывать ВЕСЬ блок записи, иначе опоздавший получал 500 вместо 409 (так
    было в первой версии — нашло ревью, держит PG-тест).

    `lookup` перечитывает ячейку уже после отката — чтобы назвать в отказе
    актуальные часы. Любая другая ошибка откатывает транзакцию и летит дальше.
    """
    try:
        yield
    except CellConflict:
        db.rollback()
        raise
    except StaleDataError:
        db.rollback()
        raise _conflict_from(lookup())
    except IntegrityError as exc:
        db.rollback()
        cause = str(exc.orig)
        if _CELL_UNIQUE not in cause and "UNIQUE constraint failed: timesheet_entries" not in cause:
            raise
        raise _conflict_from(lookup())
    except Exception:
        db.rollback()
        raise


def _conflict_from(current: TimesheetEntry | None) -> CellConflict:
    return CellConflict(int(current.hours) if current is not None else None)


def _find_cell(
    db: Session, employee_id: int, work_date: date, company_id: int,
    position_id: int | None,
) -> TimesheetEntry | None:
    """Ячейка (рабочее место, день, юрлицо). `position_id` — уже разрешённый."""
    return (
        db.query(TimesheetEntry)
        .filter(
            and_(
                TimesheetEntry.employee_id == employee_id,
                TimesheetEntry.work_date == work_date,
                TimesheetEntry.company_id == company_id,
                # Строки без позиции — доположенческие, они принадлежат основной:
                # без этого правка старой ячейки создала бы вторую рядом.
                or_(
                    TimesheetEntry.position_id == position_id,
                    TimesheetEntry.position_id.is_(None),
                ),
            )
        )
        .first()
    )


def _upsert_cell_no_commit(
    db: Session,
    actor: Employee,
    employee_id: int,
    work_date: date,
    company_id: int,
    hours: Decimal,
    position_id: int | None = None,
    expected_version: int | None = None,
    resolved: bool = False,
) -> TimesheetEntry | None:
    """Core upsert logic — flush only, no commit. Caller owns the transaction.

    `resolved=True` — `position_id` уже разрешён вызывающим (это id рабочего
    места, а не «то, что прислал клиент»), сотрудника заново не грузим.
    """
    if not resolved:
        position_id = _resolve_position_id(db, employee_id, position_id)
    existing = _find_cell(db, employee_id, work_date, company_id, position_id)
    # До любых записей: конфликт не должен ни снять код отсутствия, ни оставить
    # след в audit log.
    _check_expected_version(existing, expected_version)

    # Взаимоисключение часы/код отсутствия: часы в дне снимают код (день стал
    # рабочим). Обратное направление — в services.absences.set_absence.
    if hours != Decimal("0"):
        from app.services.absences import delete_absence_for_day

        delete_absence_for_day(db, actor, employee_id, work_date)

    if hours == Decimal("0"):
        if existing:
            log_action(
                db, actor, "timesheet_entry", existing.id, "delete",
                before={"hours": str(existing.hours)},
            )
            db.delete(existing)
            db.flush()
        return None

    if existing:
        before_hours = str(existing.hours)
        existing.hours = hours
        existing.position_id = position_id
        db.flush()
        log_action(
            db, actor, "timesheet_entry", existing.id, "update",
            before={"hours": before_hours},
            after={"hours": str(hours)},
        )
        db.flush()
        return existing
    else:
        entry = TimesheetEntry(
            employee_id=employee_id,
            position_id=position_id,
            work_date=work_date,
            company_id=company_id,
            hours=hours,
        )
        db.add(entry)
        db.flush()
        log_action(
            db, actor, "timesheet_entry", entry.id, "create",
            after={"hours": str(hours)},
        )
        db.flush()
        return entry


def _check_period_lock(
    db: Session, employee_id: int, work_date: date, position_id: int | None = None,
    already_locked: set[tuple[int | None, int, int]] | None = None,
    *, position: "EmployeePosition | None" = None, position_known: bool = False,
) -> None:
    """Raises PeriodLockedException if the period for this position+date is not draft.

    Период привязан к отделу, а отдел — к ПОЗИЦИИ (task_positions ч.A): часы
    подработки в другом отделе закрывает период того отдела, а не основного.
    """
    from app.services.timesheet_periods import (
        PeriodLockedException,
        can_edit_cells,
        get_or_create_period,
        lock_period,
    )

    if not position_known:
        # Вызывающие ячейки передают позицию готовой (`position_known`);
        # отсутствия и ночные по-прежнему разрешают её здесь.
        emp = db.get(Employee, employee_id)
        if emp is None:
            return  # employee not found — let the FK check handle it
        position = emp.position_by_id(position_id)
    department_id = position.department_id if position is not None else None
    # Батч: период одного (отдел, месяц) блокируется и проверяется ОДИН раз, а не
    # на каждую ячейку — автозаполнение отдела это ~1500 ячеек на 1–2 периода.
    # Блокировка держится до конца транзакции, повторять её незачем.
    key = (department_id, work_date.year, work_date.month)
    if already_locked is not None:
        if key in already_locked:
            return
        already_locked.add(key)
    period = get_or_create_period(db, department_id, work_date.year, work_date.month)
    # Строка периода — под разделяемой блокировкой ДО проверки статуса и до конца
    # транзакции вызывающего: переход периода (submit/close) дождётся этой записи,
    # а запись, пришедшая во время перехода, дождётся его и получит отказ
    # (task_stage1 п.1.5). Вызывающий обязан писать в ТОЙ ЖЕ транзакции.
    period = lock_period(db, period, exclusive=False)
    if not can_edit_cells(period):
        raise PeriodLockedException(period.status)


def _ensure_hours_allowed(db: Session, position: "EmployeePosition | None") -> None:
    """Часы на охранную позицию не вводятся — её смены ведёт вахта (аудит 2-Г).
    Зовётся только для НЕнулевых часов: удаление ячейки разрешено всегда."""
    from app.services.guard_staff import ensure_no_guard_accrual

    ensure_no_guard_accrual(db, position, "Часы")


def upsert_cell(
    db: Session,
    actor: Employee,
    employee_id: int,
    work_date: date,
    company_id: int,
    hours: Decimal,
    position_id: int | None = None,
    expected_version: int | None = None,
) -> TimesheetEntry | None:
    emp, position = _resolve_cell_target(db, employee_id, position_id)
    resolved_id = position.id if position is not None else None
    _check_period_lock(
        db, employee_id, work_date, resolved_id, position=position, position_known=True,
    )
    # Вне периода работы позиции день заполнять нельзя (task_employment_period).
    # hours=0 — это УДАЛЕНИЕ ячейки, его не блокируем никогда: иначе часы,
    # оставшиеся за новой границей, было бы нечем убрать.
    if hours != Decimal("0"):
        check_employment_period(db, employee_id, work_date, position_id, employee=emp)
        _ensure_hours_allowed(db, position)

    def current_cell():
        return _find_cell(db, employee_id, work_date, company_id, resolved_id)

    with _cell_write(db, current_cell):
        result = _upsert_cell_no_commit(
            db, actor, employee_id, work_date, company_id, hours, resolved_id,
            expected_version, resolved=True,
        )
        db.commit()
    if result is not None:
        db.refresh(result)
    return result


class CellNotFound(Exception):
    """У переносимой ячейки нет часов — переносить нечего."""


def move_cell_company(
    db: Session,
    actor: Employee,
    employee_id: int,
    work_date: date,
    old_company_id: int,
    new_company_id: int,
    position_id: int | None = None,
    expected_version: int | None = None,
) -> TimesheetEntry:
    """Смена юрлица ячейки ОДНОЙ транзакцией (task_stage1 п.1.1).

    Раньше фронт слал два сохранения — «обнулить старую» и «записать новую», —
    и каждое коммитилось само: сбой второго оставлял день без часов. Здесь все
    проверки идут ДО первой записи, обе записи — одним коммитом, а любой сбой
    откатывает транзакцию целиком.

    Часы берутся из САМОЙ ячейки, а не из запроса: переносится то, что лежит в
    базе, и устаревший экран их не подменит. Если у новой компании в этот день
    уже есть часы, они ЗАМЕНЯЮТСЯ переносимыми (решение заказчика — не
    складывать и не отказывать).
    """
    emp, position = _resolve_cell_target(db, employee_id, position_id)
    resolved_position_id = position.id if position is not None else None
    _check_period_lock(
        db, employee_id, work_date, resolved_position_id, position=position, position_known=True,
    )
    _ensure_hours_allowed(db, position)
    check_employment_period(db, employee_id, work_date, position_id, employee=emp)
    source = _find_cell(db, employee_id, work_date, old_company_id, resolved_position_id)
    if source is None:
        # Клиент видел ячейку, а её уже нет — это конфликт редакторов, а не
        # «переносить нечего».
        if expected_version:
            raise CellConflict(None)
        raise CellNotFound()
    _check_expected_version(source, expected_version)
    hours = source.hours
    source_version = source.version

    def conflicting_cell():
        # Опоздать можно на любой из двух ячеек. Исходная не тронута (версия та
        # же) — значит, конфликт на ЦЕЛЕВОЙ: её и называем в отказе.
        origin = _find_cell(db, employee_id, work_date, old_company_id, resolved_position_id)
        if origin is None or origin.version != source_version:
            return origin
        return _find_cell(db, employee_id, work_date, new_company_id, resolved_position_id)

    with _cell_write(db, conflicting_cell):
        _upsert_cell_no_commit(
            db, actor, employee_id, work_date, old_company_id, Decimal("0"),
            resolved_position_id, resolved=True,
        )
        moved = _upsert_cell_no_commit(
            db, actor, employee_id, work_date, new_company_id, hours,
            resolved_position_id, resolved=True,
        )
        db.commit()
    db.refresh(moved)
    return moved


def upsert_cells_batch(
    db: Session,
    actor: Employee,
    cells: list[tuple[int, date, int, Decimal] | tuple[int, date, int, Decimal, int | None]],
) -> list[TimesheetEntry | None]:
    """Transactional batch upsert — single commit for all cells.

    Ячейка — (employee_id, work_date, company_id, hours[, position_id[,
    expected_version]]); без позиции она уходит на основную, без версии проверки
    редакторов нет (автозаполнение).
    """
    # Все проверки — ДО первой записи. Период блокируется один раз на (отдел,
    # месяц), охранная позиция проверяется один раз на позицию: ни одного запроса
    # на ячейку (см. «Производительность» в CLAUDE.md).
    locked_periods: set[tuple[int | None, int, int]] = set()
    hours_allowed: set[tuple[int, int | None]] = set()
    # (сотрудник, присланный position_id) → (объект сотрудника, объект позиции):
    # разрешается один раз на рабочее место, а не на ячейку.
    targets: dict[tuple[int, int | None], tuple] = {}

    def target(employee_id: int, position_id: int | None):
        key = (employee_id, position_id)
        if key not in targets:
            targets[key] = _resolve_cell_target(db, employee_id, position_id)
        return targets[key]

    for cell in cells:
        position_id = cell[4] if len(cell) > 4 else None
        emp, position = target(cell[0], position_id)
        _check_period_lock(
            db, cell[0], cell[1], position.id if position else None, locked_periods,
            position=position, position_known=True,
        )
        if cell[3] != Decimal("0"):
            check_employment_period(db, cell[0], cell[1], position_id, employee=emp)
            if (cell[0], position_id) not in hours_allowed:
                _ensure_hours_allowed(db, position)
                hours_allowed.add((cell[0], position_id))

    results = []
    last: list = []

    def conflicting_cell():
        employee_id, work_date, company_id, resolved_id = last
        return _find_cell(db, employee_id, work_date, company_id, resolved_id)

    with _cell_write(db, conflicting_cell):
        for cell in cells:
            employee_id, work_date, company_id, hours = cell[:4]
            position_id = cell[4] if len(cell) > 4 else None
            expected_version = cell[5] if len(cell) > 5 else None
            _, position = target(employee_id, position_id)
            resolved_id = position.id if position is not None else None
            last[:] = [employee_id, work_date, company_id, resolved_id]
            result = _upsert_cell_no_commit(
                db, actor, employee_id, work_date, company_id, hours, resolved_id,
                expected_version, resolved=True,
            )
            results.append(result)
        db.commit()
    for r in results:
        if r is not None:
            db.refresh(r)
    return results


def build_autofill_preview(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    department_id: DepartmentFilter = None,
):
    """
    Compute what would be filled by schedule for visible employees.
    Returns AutofillPreview without modifying timesheet_entries.
    """
    from app.models.production_calendars import ProductionCalendar
    from app.schemas.timesheet import AutofillPreview, AutofillSkippedEmployee, TimesheetCellInput
    from app.services.guard_staff import GUARD_AUTOFILL_SKIP_REASON, is_guard_position
    from app.services.timesheet_periods import can_edit_cells, get_or_create_period
    from app.services.work_schedule import (
        planned_work_dates,
        schedule_issue,
        shift_hours_for_date,
    )

    cal = db.query(ProductionCalendar).filter_by(year=year).first()
    if cal is None:
        raise ValueError(f"Загрузите производственный календарь {year}")

    calendar_data = cal.data
    employees = visible_employees_for_actor(db, actor, department_id, year=year, month=month)

    days_in_month = _cal.monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month)

    emp_ids = [e.id for e in employees]
    existing_entries = (
        db.query(TimesheetEntry)
        .filter(
            TimesheetEntry.employee_id.in_(emp_ids) if emp_ids else False,
            TimesheetEntry.work_date >= period_start,
            TimesheetEntry.work_date <= period_end,
        )
        .all()
    ) if emp_ids else []

    existing_keys: set[tuple[int, int | None, date, int]] = set()
    for e in existing_entries:
        emp_primary = None
        e_position_id = e.position_id
        if e_position_id is None:
            emp_obj = db.get(Employee, e.employee_id)
            emp_primary = emp_obj.primary_position if emp_obj else None
            e_position_id = emp_primary.id if emp_primary else None
        existing_keys.add((e.employee_id, e_position_id, e.work_date, e.company_id))

    entries_to_create: list[TimesheetCellInput] = []
    cells_skipped = 0
    employees_processed = 0
    employees_skipped: list[AutofillSkippedEmployee] = []

    has_draft = False

    # Заполняем по КАЖДОЙ позиции: у рабочих мест разные графики и компании
    # (task_positions ч.A). У сотрудника без совместительства позиция одна —
    # поведение то же, что было.
    for emp in employees:
        for position in visible_positions(emp, actor, department_id):
            period = get_or_create_period(db, position.department_id, year, month)
            if not can_edit_cells(period):
                employees_skipped.append(AutofillSkippedEmployee(
                    employee_id=emp.id,
                    employee_name=_position_label(emp, position),
                    reason="Период не открыт для редактирования",
                ))
                continue

            has_draft = True

            # Охранное рабочее место табелем не ведётся вовсе (аудит 2-Г): ручной
            # ввод на него отклоняется, автозаполнение — пропускает с причиной.
            if is_guard_position(db, position):
                employees_skipped.append(AutofillSkippedEmployee(
                    employee_id=emp.id,
                    employee_name=_position_label(emp, position),
                    reason=GUARD_AUTOFILL_SKIP_REASON,
                ))
                continue

            # График — на КАЖДЫЙ отрезок месяца свой (task_stage3_historicity):
            # сменили график с 15-го — дни до 15-го заполняются по старому.
            usable: list[tuple[object, date, date]] = []
            skip_reason: str | None = None
            for seg_start, seg_end, terms in month_segments(position, year, month):
                seg_schedule = getattr(terms, "schedule", None)
                reason = (
                    "Не назначен график работы" if seg_schedule is None
                    else schedule_issue(seg_schedule)
                )
                if reason is not None:
                    skip_reason = skip_reason or reason
                    continue
                usable.append((seg_schedule, seg_start, seg_end))
            if not usable:
                employees_skipped.append(AutofillSkippedEmployee(
                    employee_id=emp.id,
                    employee_name=_position_label(emp, position),
                    reason=skip_reason or "Не назначен график работы",
                ))
                continue

            if position.company_id is None:
                employees_skipped.append(AutofillSkippedEmployee(
                    employee_id=emp.id,
                    employee_name=_position_label(emp, position),
                    reason="Не указана основная компания",
                ))
                continue

            # Рабочее место, закрытое на весь месяц, даёт ноль ячеек — без
            # причины это выглядит как молчаливый сбой автозаполнения.
            emp_start, emp_end = employment_bounds(emp, position)
            if (emp_end is not None and emp_end < period_start) or (
                emp_start is not None and emp_start > period_end
            ):
                employees_skipped.append(AutofillSkippedEmployee(
                    employee_id=emp.id,
                    employee_name=_position_label(emp, position),
                    reason="Месяц вне периода работы на этой должности",
                ))
                continue

            employees_processed += 1

            # Плановые дни графика (task_shift_schedules): weekday — рабочие дни
            # недели графика по производственному календарю, cyclic — смены по
            # циклу от стартовой даты (календарь на цикл не влияет).
            planned = [
                (work_date, seg_schedule)
                for seg_schedule, seg_start, seg_end in usable
                for work_date in planned_work_dates(seg_schedule, year, month, calendar_data)
                if seg_start <= work_date <= seg_end
            ]
            for work_date, schedule in planned:
                # Вне периода работы рабочего места не заполняем: уволенному
                # пятнадцатого смены до конца месяца не ставим, принятому
                # пятнадцатого — дни до выхода (task_employment_period).
                if not is_within_employment(emp, position, work_date):
                    continue
                hours = int(shift_hours_for_date(schedule, work_date, calendar_data))
                key = (emp.id, position.id, work_date, position.company_id)
                if key in existing_keys:
                    cells_skipped += 1
                    continue

                entries_to_create.append(TimesheetCellInput(
                    employee_id=emp.id,
                    position_id=position.id,
                    work_date=work_date,
                    company_id=position.company_id,
                    hours=hours,
                ))

    db.commit()  # commit any lazily-created periods

    if not has_draft and employees:
        raise ValueError("Нет периодов в статусе draft для автозаполнения")

    return AutofillPreview(
        year=year,
        month=month,
        entries_to_create=entries_to_create,
        cells_skipped=cells_skipped,
        employees_processed=employees_processed,
        employees_skipped=employees_skipped,
    )


def apply_autofill(
    db: Session,
    actor: Employee,
    preview,
) -> int:
    """Apply preview entries to DB via upsert_cells_batch. Returns count created."""
    if not preview.entries_to_create:
        return 0

    cells = [
        (e.employee_id, e.work_date, e.company_id, e.hours, e.position_id)
        for e in preview.entries_to_create
    ]
    results = upsert_cells_batch(db, actor, cells)
    return sum(1 for r in results if r is not None)
