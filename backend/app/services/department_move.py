"""Перенос отдела в другую компанию (task_move_department).

Подразделение может отделиться в самостоятельное юрлицо. Инструмент меняет
головную компанию отдела и компанию его рабочих мест на целевую — массово, но
только для позиций ЭТОГО отдела: подработка сотрудника в другом отделе остаётся
на своей компании.

── Почему перед переносом замораживаются закрытые месяцы ─────────────────────

Снапшота расчёта в системе нет: ведомость за любой месяц, включая `closed`,
пересчитывается из ТЕКУЩИХ справочных данных. Компания у отработанного часа
лежит в самой ячейке (`timesheet_entries.company_id`), поэтому основную часть
истории смена компании позиции не задевает — проверено замером. Но
`position.company` входит в расчёт ещё двумя путями:

  1. «Нет часов вообще → вся сумма на основную компанию» — сотрудник, у которого
     в закрытом месяце была только премия (или отпускные), после переноса увёл бы
     её на новое юрлицо целиком;
  2. остаток округления в `distribute` достаётся основной компании, а если её нет
     в наборе — компании с наибольшей долей: рубль переезжает между юрлицами.

Поэтому перенос СНАЧАЛА фиксирует фактический расклад закрытых месяцев месячным
override-ом (`CompanyShareOverride` — он стоит на вершине каскада), и только
потом меняет компанию. Прошлое после этого прибито к тому, что уже видела
бухгалтерия, а не «почти совпадает».

Проценты фиксации вычисляются ИЗ УЖЕ ПОСЧИТАННЫХ СУММ и с шагом 1e-6 — при
трёх знаках обратный пересчёт расходился с исходными суммами на единицы рублей,
то есть заморозка сама двигала бы историю (см. миграцию `e1f2a3b4c5d6`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.companies import Company
from app.models.company_shares import (
    CompanyShareOverride,
    DepartmentCompanyShare,
    EmployeeCompanyShare,
)
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.reference_changes import SOURCE_BULK
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.distribution import distribute
from app.services.payroll_statement import build_payroll_statement
from app.services.reference_audit import audit_operation
from app.services.timesheet import get_month_entries

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

#: Шаг округления замороженных процентов. Не 0.01 и не 0.001: только на шести
#: знаках обратный пересчёт сумм совпадает с исходным бит в бит.
FREEZE_PERCENT_STEP = Decimal("0.000001")


class MoveError(ValueError):
    """Перенос невозможен — текст показывается пользователю."""


@dataclass
class MovePreview:
    """Что произойдёт при переносе — для диалога подтверждения."""

    department_id: int
    department_name: str
    source_company_id: int | None
    source_company_name: str | None
    target_company_id: int
    target_company_name: str
    employee_count: int
    position_count: int
    #: Рабочие места сотрудников, которые НЕ переносятся (другие отделы).
    untouched_position_count: int
    closed_months: list[tuple[int, int]] = field(default_factory=list)
    #: Позиции с явным %, не включающим целевую компанию — перенос их не изменит.
    stale_share_position_count: int = 0
    #: Дефолт распределения отдела не включает целевую компанию.
    department_shares_stale: bool = False
    #: Ячеек часов в НЕзакрытых месяцах, которые сменят юрлицо со старого на целевое.
    entries_to_reattribute: int = 0


@dataclass
class MoveResult:
    positions_moved: int
    employees_affected: int
    #: Сколько закрытых месяцев обработано. Строк процентов при этом может быть
    #: и ноль — если расклад там уже задан руками бухгалтером.
    closed_months_frozen: int
    override_rows_written: int
    #: Ячеек часов, перепривязанных со старого юрлица на целевое.
    entries_reattributed: int = 0


def _target_positions(db: Session, department_id: int) -> list[EmployeePosition]:
    """Рабочие места, которые переезжают: активные позиции ЭТОГО отдела.

    Неактивные не берутся намеренно — они не дают строк расчёта
    (`visible_positions` смотрит только на `active_positions`), поэтому смена их
    компании ни на что не влияет, а заморозку раздувала бы впустую.
    """
    return (
        db.query(EmployeePosition)
        .filter(
            EmployeePosition.department_id == department_id,
            EmployeePosition.is_active == True,  # noqa: E712
        )
        .order_by(EmployeePosition.id)
        .all()
    )


def closed_months(db: Session, department_id: int) -> list[tuple[int, int]]:
    """Закрытые месяцы отдела — по его же периодам.

    «Закрытый месяц» для отдела и есть период `(department_id, year, month)` в
    статусе `closed`; месяц, который никто не открывал, периода не имеет и
    замораживать в нём нечего.
    """
    rows = (
        db.query(TimesheetPeriod.year, TimesheetPeriod.month)
        .filter(
            TimesheetPeriod.department_id == department_id,
            TimesheetPeriod.status == "closed",
        )
        .order_by(TimesheetPeriod.year, TimesheetPeriod.month)
        .all()
    )
    return [(r.year, r.month) for r in rows]


def _employees_of(db: Session, positions: list[EmployeePosition]) -> list[Employee]:
    emp_ids = {p.employee_id for p in positions}
    if not emp_ids:
        return []
    return db.query(Employee).filter(Employee.id.in_(emp_ids)).all()


def _stale_share_positions(
    db: Session, positions: list[EmployeePosition], target_company_id: int
) -> int:
    """Позиции, у которых в карточке задан явный %, НЕ включающий целевую компанию.

    Наборы процентов ссылаются на `company_id` напрямую, поэтому перенос их не
    трогает — и стоят они в каскаде выше авто-распределения по часам. То есть у
    таких рабочих мест зарплата и после переезда продолжит уходить на прежние
    юрлица, а нового среди них нет. Молча стирать чужую настройку нельзя,
    поэтому предупреждаем в диалоге.

    Набор, где целевая компания уже есть, к перенастройке не обязывает — про
    него не сообщаем.
    """
    if not positions:
        return 0
    pos_ids = [p.id for p in positions]
    rows = (
        db.query(EmployeeCompanyShare.position_id, EmployeeCompanyShare.company_id)
        .filter(
            EmployeeCompanyShare.position_id.in_(pos_ids),
            EmployeeCompanyShare.percent > 0,
        )
        .all()
    )
    by_position: dict[int, set[int]] = {}
    for position_id, company_id in rows:
        by_position.setdefault(position_id, set()).add(company_id)
    return sum(
        1 for companies in by_position.values() if target_company_id not in companies
    )


def build_preview(
    db: Session, dept: Department, target_company: Company
) -> MovePreview:
    positions = _target_positions(db, dept.id)
    employees = _employees_of(db, positions)
    moved_ids = {p.id for p in positions}
    untouched = sum(
        1
        for emp in employees
        for p in emp.active_positions
        if p.id not in moved_ids
    )
    # Дефолт распределения самого отдела — та же история, что и с карточками:
    # он переживает перенос и стоит выше авто по часам.
    dept_share_companies = {
        cid
        for (cid,) in db.query(DepartmentCompanyShare.company_id)
        .filter(
            DepartmentCompanyShare.department_id == dept.id,
            DepartmentCompanyShare.percent > 0,
        )
        .all()
    }
    dept_shares_stale = bool(
        dept_share_companies and target_company.id not in dept_share_companies
    )
    source = db.get(Company, dept.head_company_id) if dept.head_company_id else None
    months = closed_months(db, dept.id)
    return MovePreview(
        department_id=dept.id,
        department_name=dept.name,
        source_company_id=source.id if source else None,
        source_company_name=source.name if source else None,
        target_company_id=target_company.id,
        target_company_name=target_company.name,
        employee_count=len(employees),
        position_count=len(positions),
        untouched_position_count=untouched,
        closed_months=months,
        stale_share_position_count=_stale_share_positions(
            db, positions, target_company.id
        ),
        department_shares_stale=dept_shares_stale,
        entries_to_reattribute=(
            _reattribute_entries(
                db, employees, {p.id for p in positions}, dept.head_company_id,
                target_company.id, set(months), apply=False,
            )
            if dept.head_company_id is not None
            else 0
        ),
    )


def _freeze_month(
    db: Session,
    employees: list[Employee],
    moved_position_ids: set[int],
    year: int,
    month: int,
    actor: Employee,
) -> int:
    """Записать фактический расклад закрытого месяца месячным override-ом.

    Возвращает число созданных строк процентов. Позиции, у которых override за
    этот месяц уже есть, пропускаются: бухгалтер задал расклад руками, он и так
    на вершине каскада, переписывать его нечем и незачем.
    """
    entries = get_month_entries(db, employees, year, month)
    # actor не передаём: замораживаем ВСЕ активные позиции набора, а не то, что
    # видно конкретному пользователю, — иначе часть отдела осталась бы незамороженной.
    statement = build_payroll_statement(db, employees, entries, year, month)

    # Уже заданные наборы за этот месяц. `position_id IS NULL` — набор, заведённый
    # до появления позиций: каскад читает его как ОСНОВНУЮ позицию
    # (`load_month_overrides`), и здесь его надо разрешить так же. Иначе мы
    # записали бы второй набор на ту же позицию, и в каскаде они СЛИЛИСЬ бы в
    # один — с процентами из обоих.
    primary_by_employee = {
        e.id: (e.primary_position.id if e.primary_position else None) for e in employees
    }
    already: set[tuple[int, int | None]] = set()
    for employee_id, position_id in (
        db.query(CompanyShareOverride.employee_id, CompanyShareOverride.position_id)
        .filter(
            CompanyShareOverride.year == year,
            CompanyShareOverride.month == month,
            CompanyShareOverride.employee_id.in_([e.id for e in employees] or [0]),
        )
        .distinct()
        .all()
    ):
        if position_id is None:
            position_id = primary_by_employee.get(employee_id)
        already.add((employee_id, position_id))

    written = 0
    for row in statement.rows:
        if row.position_id not in moved_position_ids:
            continue
        if (row.employee_id, row.position_id) in already:
            continue
        # Проценты — из СУММ, а не из исходных весов: так обратный пересчёт
        # (`distribute` от процентов) возвращает ровно те же рубли.
        # Целевые премии/KPI (task_funding_source) из сумм ВЫЧИТАЮТСЯ: замороженный
        # процент — это процент КАСКАДА, а каскад делит базу распределения уже без
        # них. Заморозив фактическую долю (40/60 вместо 50/50), мы бы прибавили
        # целевую сумму второй раз и сдвинули закрытый месяц — ровно то, от чего
        # заморозка и защищает.
        #
        # Округление долей до тысячи (task_it_arm_distribution ч.3) заморозке не
        # мешает: проценты считаются из УЖЕ округлённых сумм, а обратный пересчёт
        # (floor + раздача недостающих тысяч по наибольшим хвостам) сам себя
        # исправляет — доля, floor-нувшаяся на тысячу вниз из-за погрешности
        # процента, получает эту тысячу назад как наибольший хвост. Держит
        # test_move_department.
        targeted = row.targeted_amounts or {}
        weights = {
            d.company_id: d.amount - targeted.get(d.company_id, _ZERO)
            for d in row.distribution
            if d.amount - targeted.get(d.company_id, _ZERO) > _ZERO
        }
        if not weights:
            # Начислений нет (0 ₽) — сумму фиксировать нечем, но ПРИВЯЗКА строки
            # к юрлицу всё равно поехала бы: у строки без часов расклад берётся
            # от основной компании, а она меняется. Фиксируем по процентам.
            weights = {
                d.company_id: d.percent for d in row.distribution if d.percent > _ZERO
            }
        if not weights:
            continue
        percents = distribute(_HUNDRED, weights, None, FREEZE_PERCENT_STEP)
        for company_id, percent in percents.items():
            db.add(
                CompanyShareOverride(
                    employee_id=row.employee_id,
                    position_id=row.position_id,
                    company_id=company_id,
                    year=year,
                    month=month,
                    percent=percent,
                    created_by_id=actor.id,
                )
            )
            written += 1
    return written


def _entry_position_ids(
    employees: list[Employee], moved_position_ids: set[int]
) -> dict[int, set[int | None]]:
    """{employee_id: {position_id, ...}} — какие position_id ячеек принадлежат
    переносимым рабочим местам.

    `position_id IS NULL` — ячейка, заведённая до появления позиций: она читается
    как ОСНОВНАЯ позиция (`Employee.position_by_id`). Если основная позиция
    сотрудника переезжает, такие ячейки обязаны переехать вместе с ней, иначе
    часть часов молча останется на старом юрлице.
    """
    result: dict[int, set[int | None]] = {}
    for emp in employees:
        ids: set[int | None] = {
            p.id for p in emp.positions if p.id in moved_position_ids
        }
        if not ids:
            continue
        primary = emp.primary_position
        if primary is not None and primary.id in moved_position_ids:
            ids.add(None)
        result[emp.id] = ids
    return result


def _reattribute_entries(
    db: Session,
    employees: list[Employee],
    moved_position_ids: set[int],
    source_company_id: int,
    target_company_id: int,
    closed: set[tuple[int, int]],
    apply: bool,
) -> int:
    """Перевести уже введённые часы со СТАРОГО юрлица отдела на целевое —
    в месяцах, которые НЕ закрыты. Возвращает число затронутых ячеек.

    Зачем вообще: компания лежит в самой ячейке, и без этого весь заполненный
    текущий месяц остался бы на прежнем юрлице — то есть «с текущего месяца
    вперёд» не выполнялось бы, деньги за август ушли бы старой компании.

    Что НЕ трогаем:
      * закрытые месяцы — там расклад заморожен (и по нему уже отчитались);
      * ячейки на ДРУГИХ юрлицах — это осознанная работа сотрудника на сторону,
        а не «часы отдела»; переносим только то, что было на старой головной.

    Столкновение: если в тот же день на то же рабочее место уже есть ячейка
    целевого юрлица, часы СКЛАДЫВАЮТСЯ в неё, а исходная удаляется — иначе
    нарушится unique (employee, position, date, company). Человек в этот день
    отработал и то, и другое, и после переезда всё это — часы целевой компании.
    """
    by_employee = _entry_position_ids(employees, moved_position_ids)
    if not by_employee:
        return 0

    rows = (
        db.query(TimesheetEntry)
        .filter(
            TimesheetEntry.employee_id.in_(by_employee.keys()),
            TimesheetEntry.company_id == source_company_id,
        )
        .all()
    )

    # Ячейки целевого юрлица тех же ключей — чтобы поймать столкновение.
    existing_target: dict[tuple[int, int | None, object], TimesheetEntry] = {}
    if apply:
        for entry in (
            db.query(TimesheetEntry)
            .filter(
                TimesheetEntry.employee_id.in_(by_employee.keys()),
                TimesheetEntry.company_id == target_company_id,
            )
            .all()
        ):
            existing_target[(entry.employee_id, entry.position_id, entry.work_date)] = entry

    touched = 0
    for entry in rows:
        if entry.position_id not in by_employee[entry.employee_id]:
            continue
        if (entry.work_date.year, entry.work_date.month) in closed:
            continue
        touched += 1
        if not apply:
            continue
        key = (entry.employee_id, entry.position_id, entry.work_date)
        collision = existing_target.get(key)
        if collision is not None:
            collision.hours = min(24, collision.hours + entry.hours)
            db.delete(entry)
        else:
            entry.company_id = target_company_id
            existing_target[key] = entry
    return touched


def move_department(
    db: Session,
    dept: Department,
    target_company: Company,
    actor: Employee,
) -> MoveResult:
    """Перенести отдел в другую компанию. Вызывающий коммитит транзакцию.

    Порядок важен: сначала заморозка закрытых месяцев (она считает ведомость по
    ЕЩЁ СТАРОЙ компании), потом смена компании. Всё в одной транзакции — при
    сбое не остаётся ни половины перенесённых позиций, ни половины заморозки.
    """
    if not target_company.is_active:
        raise MoveError("Целевая компания неактивна")
    if dept.head_company_id == target_company.id:
        raise MoveError("Отдел уже числится в этой компании")

    # Журнал изменений: всё, что тронет перенос — головная компания отдела и
    # юрлицо каждого рабочего места, — уходит в журнал ОДНОЙ операцией с общим
    # id, иначе сотня одинаковых строк выглядит как сотня независимых правок и
    # разобрать «что сделал этот перенос» невозможно.
    with audit_operation(db, SOURCE_BULK):
        return _move_department(db, dept, target_company, actor)


def _move_department(
    db: Session,
    dept: Department,
    target_company: Company,
    actor: Employee,
) -> MoveResult:
    positions = _target_positions(db, dept.id)
    employees = _employees_of(db, positions)
    moved_ids = {p.id for p in positions}

    months = closed_months(db, dept.id)
    written = 0
    if employees and moved_ids:
        for year, month in months:
            written += _freeze_month(db, employees, moved_ids, year, month, actor)

    # Часы НЕзакрытых месяцев переезжают вместе с отделом: компания лежит в самой
    # ячейке, и без этого заполненный текущий месяц остался бы на прежнем юрлице.
    reattributed = 0
    if employees and moved_ids and dept.head_company_id is not None:
        reattributed = _reattribute_entries(
            db, employees, moved_ids, dept.head_company_id, target_company.id,
            set(months), apply=True,
        )

    before = {
        "head_company_id": dept.head_company_id,
        "position_ids": sorted(moved_ids),
    }
    dept.head_company_id = target_company.id
    for position in positions:
        position.company_id = target_company.id

    db.flush()
    log_action(
        db,
        actor,
        "department",
        dept.id,
        "moved_to_company",
        before=before,
        after={
            "head_company_id": target_company.id,
            "positions_moved": len(positions),
            "employees_affected": len(employees),
            "closed_months_frozen": len(months),
            "override_rows_written": written,
            "entries_reattributed": reattributed,
        },
        reason=f"Перенос отдела «{dept.name}» в компанию «{target_company.name}»",
    )
    return MoveResult(
        positions_moved=len(positions),
        employees_affected=len(employees),
        closed_months_frozen=len(months),
        override_rows_written=written,
        entries_reattributed=reattributed,
    )
