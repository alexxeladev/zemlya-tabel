"""
Позиции сотрудников: фильтры видимости и подбор позиций для расчёта
(task_positions ч.A, совместительство).

Отдел, график и компания живут на ПОЗИЦИИ, а не на человеке, поэтому «сотрудник
такого-то отдела» стал условием по его позициям. Все SQL-фильтры по отделу
собираются здесь, чтобы форма условия была одна на весь проект — раньше это был
прямой `Employee.department_id`, и его легко было забыть где-нибудь обновить.

Семантика при ОДНОЙ позиции полностью совпадает со старой: сотрудник относится
к отделу своей единственной позиции.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement
from sqlalchemy.orm import Session

from app.models.employees import Employee
from app.models.positions import PAY_TYPE_BASE_FIELD, EmployeePosition
from app.services.org_access import accessible_department_ids, is_department_scoped


def in_departments(dept_ids: list[int]) -> ColumnElement[bool]:
    """Сотрудник относится к одному из отделов — есть позиция в этом отделе."""
    return Employee.positions.any(EmployeePosition.department_id.in_(dept_ids))


def in_department(department_id: int | None) -> ColumnElement[bool]:
    """Сотрудник относится к отделу; `None` — группа «Без отдела»
    (есть позиция без отдела)."""
    if department_id is None:
        return Employee.positions.any(EmployeePosition.department_id.is_(None))
    return Employee.positions.any(EmployeePosition.department_id == department_id)


def department_employment_rows(db: Session) -> list[tuple[int | None, bool, date | None]]:
    """Снимок занятости отделов: (отдел, активен ли сотрудник, дата увольнения).

    По одной строке на АКТИВНОЕ рабочее место несистемного сотрудника, без
    дублей. Из него для любого месяца получается набор занятых отделов
    (`departments_with_employees`) — одним запросом на весь ответ, а не по
    запросу на месяц: месяцев в дашборде столько, сколько строк в диапазоне и в
    просрочке, а от месяца тут зависит только сравнение с датой увольнения.
    """
    rows = (
        db.query(
            EmployeePosition.department_id,
            Employee.is_active,
            Employee.dismissal_date,
        )
        .join(Employee, Employee.id == EmployeePosition.employee_id)
        .filter(
            EmployeePosition.is_active == True,  # noqa: E712
            Employee.is_system_admin == False,  # noqa: E712
        )
        .distinct()
        .all()
    )
    return [(dept_id, bool(active), dismissal) for dept_id, active, dismissal in rows]


def departments_with_employees(
    rows: list[tuple[int | None, bool, date | None]], year: int, month: int
) -> set[int | None]:
    """Отделы, в которых в ЭТОМ месяце есть сотрудники; `None` — «Без отдела».

    Правило то же, что у видимости табеля (`visible_employees_for_actor`):
    сотрудник активен либо уволен не раньше начала месяца. Отличие одно —
    рабочее место должно быть активным: строку в табеле даёт `visible_positions`,
    то есть отдел, где все позиции деактивированы, показывать нечем.

    Отдела здесь нет — значит, закрывать в нём за этот месяц нечего, и в
    workflow периодов он не участвует вовсе.
    """
    start = date(year, month, 1)
    return {
        dept_id
        for dept_id, is_active, dismissal in rows
        if is_active or (dismissal is not None and dismissal >= start)
    }


def department_ids_of(employee: Employee) -> list[int | None]:
    """Отделы, к которым сотрудник относится (по одному на позицию, без дублей).

    Порядок стабильный: отдел основной позиции первым — от него зависит, в какой
    период (department, year, month) попадёт сотрудник по умолчанию.
    """
    seen: list[int | None] = []
    for pos in employee.active_positions or employee.positions:
        if pos.department_id not in seen:
            seen.append(pos.department_id)
    if not seen:
        seen.append(None)
    return seen


def visible_positions(
    employee: Employee, actor: Employee, department_id: int | None = None
) -> list[EmployeePosition]:
    """Позиции сотрудника, которые вправе видеть actor.

    Менеджеру (и табельщику) видны только рабочие места в его отделах: числиться
    у него в отделе основной позицией и подрабатывать в чужом отделе — разные
    вещи, и чужую подработку он видеть не должен. Admin/accountant видят все.
    """
    positions = employee.active_positions
    if is_department_scoped(actor):
        allowed = set(accessible_department_ids(actor, department_id))
        positions = [p for p in positions if p.department_id in allowed]
    elif department_id is not None:
        positions = [p for p in positions if p.department_id == department_id]
    return positions


def positions_for_payroll(
    employees: list[Employee], actor: Employee, department_id: int | None = None
) -> list[tuple[Employee, EmployeePosition]]:
    """Пары (сотрудник, позиция) для расчёта ЗП — по одной строке на рабочее место.

    Сотрудник без единой видимой позиции пропускается: показывать пустую строку
    без оклада и графика бессмысленно, а «нет доступа» уже отработал фильтр выше.
    """
    result: list[tuple[Employee, EmployeePosition]] = []
    for emp in employees:
        for pos in visible_positions(emp, actor, department_id):
            result.append((emp, pos))
    return result


# ── CRUD позиций из карточки сотрудника (task_positions ч.B) ──────────────────


class PositionError(ValueError):
    """Позицию нельзя изменить/удалить — текст показывается пользователю."""


def _normalize_pay_base(position: EmployeePosition) -> None:
    """Погасить базы чужих типов оплаты: у окладника не должно остаться ставки за
    смену или за час, у посменного — оклада."""
    for pay_type, base_field in PAY_TYPE_BASE_FIELD.items():
        if position.pay_type != pay_type:
            setattr(position, base_field, None)


def _apply_coefficient_defaults(position: EmployeePosition) -> None:
    """Дефолт 1.5 у коэффициентов — как в `build_employee`, чтобы карточка и
    импорт заводили позицию одинаково (NULL читается расчётом как 1.5, но хранить
    его — значит показывать в UI пустое поле вместо реальной ставки)."""
    if position.weekend_pay_type == "coefficient" and position.weekend_coefficient is None:
        position.weekend_coefficient = Decimal("1.5")
    if position.holiday_pay_type == "coefficient" and position.holiday_coefficient is None:
        position.holiday_coefficient = Decimal("1.5")
    if position.overtime_coefficient is None:
        position.overtime_coefficient = Decimal("1.5")


def apply_position_fields(position: EmployeePosition, data: dict) -> None:
    """Записать поля из payload-а и привести карточку рабочего места в порядок."""
    for field, value in data.items():
        setattr(position, field, value)
    _normalize_pay_base(position)
    _apply_coefficient_defaults(position)


def create_position(employee: Employee, data: dict) -> EmployeePosition:
    """Добавить рабочее место. `is_primary=True` переносит признак основной."""
    make_it_primary = bool(data.pop("is_primary", False))
    position = EmployeePosition(employee_id=employee.id)
    apply_position_fields(position, data)
    employee.positions.append(position)
    if make_it_primary or employee.primary_position is None:
        set_primary(employee, position)
    else:
        position.is_primary = False
    return position


def set_primary(employee: Employee, position: EmployeePosition) -> None:
    """Основная позиция ровно одна: назначая новую, снимаем признак с остальных."""
    if not position.is_active:
        raise PositionError("Основной можно сделать только активную позицию")
    for p in employee.positions:
        p.is_primary = p is position


def position_usage(db: Session, position: EmployeePosition) -> dict[str, int]:
    """Сколько данных завязано на позицию — от этого зависит, можно ли её удалить
    физически или остаётся только деактивировать (часы и начисления терять нельзя).
    """
    from app.models.company_shares import CompanyShareOverride, EmployeeCompanyShare
    from app.models.employee_adjustments import EmployeeAdjustment
    from app.models.timesheet_entries import TimesheetEntry

    return {
        "entries": db.query(TimesheetEntry)
        .filter(TimesheetEntry.position_id == position.id).count(),
        "adjustments": db.query(EmployeeAdjustment)
        .filter(EmployeeAdjustment.position_id == position.id).count(),
        "shares": db.query(EmployeeCompanyShare)
        .filter(EmployeeCompanyShare.position_id == position.id).count()
        + db.query(CompanyShareOverride)
        .filter(CompanyShareOverride.position_id == position.id).count(),
        "loan": 1 if position.employee.loan_position_id == position.id else 0,
    }


def delete_position(db: Session, employee: Employee, position: EmployeePosition) -> str:
    """Убрать рабочее место. Возвращает «deleted» или «deactivated».

    Основную удалить нельзя — у сотрудника всегда есть хотя бы одно рабочее место
    (сначала назначьте основной другую). Позиция, на которой уже есть часы или
    начисления, не удаляется физически, а деактивируется: иначе история табеля и
    расчётов прошлых месяцев осталась бы без рабочего места.
    """
    if position.is_primary:
        raise PositionError(
            "Нельзя удалить основную позицию — сначала назначьте основной другую"
        )
    if len([p for p in employee.positions if p.is_active]) <= 1:
        raise PositionError("У сотрудника должна остаться хотя бы одна позиция")

    if any(position_usage(db, position).values()):
        position.is_active = False
        return "deactivated"

    employee.positions.remove(position)
    db.delete(position)
    return "deleted"


def entries_by_position(
    employee: Employee, entries: list
) -> dict[int, list]:
    """Часы сотрудника, разложенные по позициям.

    Строки с `position_id IS NULL` (заведены до появления позиций) относятся к
    основной позиции — иначе миграция потеряла бы часы.
    """
    primary = employee.primary_position
    primary_id = primary.id if primary is not None else None
    by_position: dict[int, list] = {}
    for entry in entries:
        pid = entry.position_id if entry.position_id is not None else primary_id
        if pid is None:
            continue
        by_position.setdefault(pid, []).append(entry)
    return by_position
