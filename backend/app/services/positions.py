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

from sqlalchemy import ColumnElement

from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.services.org_access import accessible_department_ids


def in_departments(dept_ids: list[int]) -> ColumnElement[bool]:
    """Сотрудник относится к одному из отделов — есть позиция в этом отделе."""
    return Employee.positions.any(EmployeePosition.department_id.in_(dept_ids))


def in_department(department_id: int | None) -> ColumnElement[bool]:
    """Сотрудник относится к отделу; `None` — группа «Без отдела»
    (есть позиция без отдела)."""
    if department_id is None:
        return Employee.positions.any(EmployeePosition.department_id.is_(None))
    return Employee.positions.any(EmployeePosition.department_id == department_id)


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

    Менеджеру видны только рабочие места в его отделах: числиться у него в отделе
    основной позицией и подрабатывать в чужом отделе — разные вещи, и чужую
    подработку он видеть не должен. Admin/accountant видят все.
    """
    positions = employee.active_positions
    if actor.role == "manager":
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
