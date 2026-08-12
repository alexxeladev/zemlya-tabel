"""Доступ к отделам и к финансам по роли (task_org_structure ч.2,
task_timekeeper_role).

Единственный источник правды «какими отделами руководит actor». До этой задачи
менеджер был привязан к одному `Employee.department_id`, и проверка
`actor.department_id == department_id` была размазана по роутерам и сервисам.
Теперь связь many-to-many (`department_managers`), а все проверки идут сюда.

Не путать два поля:
  * `Employee.department_id`      — где сотрудник ЧИСЛИТСЯ (работает);
  * `Employee.managed_departments` — чем менеджер РУКОВОДИТ (а табельщик ВЕДЁТ).

Здесь же живёт вторая половина ролевой модели — видимость ФИНАНСОВ
(`can_see_finances`): табельщик работает в тех же отделах, что менеджер, но
оклады, премии, удержания и «к выплате» ему не отдаются. Обе проверки в одном
месте, чтобы «кто что видит» не приходилось собирать по роутерам.

Функции чистые (без БД и HTTP) — роутеры сами переводят отказ в 403.
"""
from __future__ import annotations

from app.models.employees import Employee

# Роли, чей доступ ограничен списком `managed_departments`. Менеджер руководит
# отделом, табельщик ведёт его табель — по отделам они устроены одинаково,
# различие только в финансах (см. FINANCE_ROLES).
DEPARTMENT_SCOPED_ROLES = ("manager", "timekeeper")

# Роли, которым отдаются деньги (оклады, премии, удержания, «к выплате»,
# распределение по юрлицам). Табельщика здесь нет намеренно.
FINANCE_ROLES = ("admin", "accountant", "manager")


def is_department_scoped(actor: Employee) -> bool:
    """Ограничен ли actor своими отделами (manager / timekeeper)."""
    return actor.role in DEPARTMENT_SCOPED_ROLES


def can_see_finances(actor: Employee) -> bool:
    """Вправе ли actor видеть денежные данные ЧУЖИХ сотрудников.

    employee-у отдаётся его собственная карточка (там свой оклад) — это отдельное
    правило конкретных роутеров, здесь речь только про доступ к финансам отдела.
    """
    return actor.role in FINANCE_ROLES


def hides_finances(actor: Employee) -> bool:
    """Нужно ли вычищать денежные поля из ответа для этой роли.

    Табельщик получает те же данные табеля, что менеджер, поэтому мало вернуть
    403 на расчётных эндпойнтах: оклад и ставки лежат в карточке сотрудника и
    его позициях, которые приходят вместе с табелем (см. finance_masking).
    """
    return actor.role == "timekeeper"


def managed_department_ids(actor: Employee) -> list[int]:
    """Отделы, которыми руководит (или которые ведёт) actor. Для admin/accountant
    пусто — их доступ шире и по отделам не ограничивается."""
    return sorted(d.id for d in actor.managed_departments)


def can_access_department(actor: Employee, department_id: int | None) -> bool:
    """Виден ли actor-у отдел `department_id` (None — группа «Без отдела»)."""
    if actor.role in ("admin", "accountant"):
        return True
    if not is_department_scoped(actor):
        return False
    # Группой «Без отдела» никто не руководит — она только для admin/accountant.
    if department_id is None:
        return False
    return department_id in set(managed_department_ids(actor))


def accessible_department_ids(actor: Employee, department_id: int | None = None) -> list[int]:
    """Отделы, по которым manager/timekeeper можно отдавать данные, с учётом фильтра
    `department_id` из запроса. Пустой список = показывать нечего."""
    managed = managed_department_ids(actor)
    if department_id is None:
        return managed
    return [department_id] if department_id in managed else []
