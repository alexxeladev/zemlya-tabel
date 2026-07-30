"""Доступ менеджера к отделам (task_org_structure ч.2).

Единственный источник правды «какими отделами руководит actor». До этой задачи
менеджер был привязан к одному `Employee.department_id`, и проверка
`actor.department_id == department_id` была размазана по роутерам и сервисам.
Теперь связь many-to-many (`department_managers`), а все проверки идут сюда.

Не путать два поля:
  * `Employee.department_id`      — где сотрудник ЧИСЛИТСЯ (работает);
  * `Employee.managed_departments` — чем менеджер РУКОВОДИТ.

Функции чистые (без БД и HTTP) — роутеры сами переводят отказ в 403.
"""
from __future__ import annotations

from app.models.employees import Employee


def managed_department_ids(actor: Employee) -> list[int]:
    """Отделы, которыми руководит actor. Для admin/accountant пусто —
    их доступ шире и по отделам не ограничивается."""
    return sorted(d.id for d in actor.managed_departments)


def can_access_department(actor: Employee, department_id: int | None) -> bool:
    """Виден ли actor-у отдел `department_id` (None — группа «Без отдела»)."""
    if actor.role in ("admin", "accountant"):
        return True
    if actor.role != "manager":
        return False
    # Группой «Без отдела» никто не руководит — она только для admin/accountant.
    if department_id is None:
        return False
    return department_id in set(managed_department_ids(actor))


def accessible_department_ids(actor: Employee, department_id: int | None = None) -> list[int]:
    """Отделы, по которым менеджеру можно отдавать данные, с учётом фильтра
    `department_id` из запроса. Пустой список = показывать нечего."""
    managed = managed_department_ids(actor)
    if department_id is None:
        return managed
    return [department_id] if department_id in managed else []
