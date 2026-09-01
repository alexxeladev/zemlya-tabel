"""
Количественный показатель отдела → проценты распределения по юрлицам
(task_hr_applications, обобщено в task_it_arm_distribution).

Отдел с флагом `Department.uses_quantity_distribution` распределяет зарплату
своих сотрудников не по обычному каскаду, а по количественному показателю,
набранному за месяц по каждому юрлицу:

    процент компании = количество компании / сумма количеств месяца

Что именно считается — настройка отдела (`quantity_metric_name`), а не имя
отдела и не ветка в коде: у HR это заявки на подбор, у ИТ — число АРМ (рабочих
мест). Механизм ОДИН, отличается только подпись; добавить третий показатель
можно, не трогая расчёт.

Проценты ЕДИНЫЕ для всего отдела: и у рекрутёра, и у его руководителя зарплата
делится одинаково — показатель набран отделом, а не человеком.

Здесь только количества и проценты из них. САМО распределение суммы (округление
до тысяч методом наибольшего остатка) остаётся в `app.services.distribution` —
второй реализации округления в проекте нет.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.department_quantities import DepartmentQuantity
from app.models.departments import Department
from app.schemas.quantity import DepartmentQuantitiesRead, QuantityShareRead
from app.services.company_order import company_order_by, order_index
from app.services.distribution import PERCENT_STEP, distribute

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def quantity_department_ids(
    db: Session, dept_ids: list[int] | set[int] | None
) -> set[int]:
    """Из переданных отделов — те, что распределяются по количественному показателю.

    `dept_ids=None` — «все отделы с флагом» (так их видит admin/accountant, у
    которого выборка не сужена отделом); пустой список — ни одного.
    """
    query = db.query(Department.id).filter(
        Department.uses_quantity_distribution == True,  # noqa: E712
    )
    if dept_ids is not None:
        ids = {d for d in dept_ids if d is not None}
        if not ids:
            return set()
        query = query.filter(Department.id.in_(ids))
    return {r[0] for r in query.all()}


def load_quantity_parts(
    db: Session, dept_ids: list[int] | set[int], year: int, month: int
) -> dict[int, dict[int, tuple[int, int]]]:
    """{department_id: {company_id: (часть 1, часть 2)}} за КОНКРЕТНЫЙ месяц.

    Показатель помесячный: в наборе только строки этого года и месяца, прошлый
    месяц на распределение не влияет.
    """
    result: dict[int, dict[int, tuple[int, int]]] = {}
    ids = {d for d in dept_ids if d is not None}
    if not ids:
        return result
    rows = (
        db.query(DepartmentQuantity)
        .filter(
            DepartmentQuantity.department_id.in_(ids),
            DepartmentQuantity.year == year,
            DepartmentQuantity.month == month,
        )
        .all()
    )
    for r in rows:
        if r.count > 0:
            result.setdefault(r.department_id, {})[r.company_id] = (
                r.part1 or 0, r.part2 or 0,
            )
    return result


def load_quantity_counts(
    db: Session, dept_ids: list[int] | set[int], year: int, month: int
) -> dict[int, dict[int, int]]:
    """{department_id: {company_id: ВСЕГО}} — то, чем считается распределение.

    Всего = часть 1 + часть 2: обе части равноправны (у HR платят за все
    отработанные заявки, а не только за закрытые). У показателя без разбивки
    вторая часть всегда 0.
    """
    return {
        dept_id: {cid: a + b for cid, (a, b) in by_company.items()}
        for dept_id, by_company in load_quantity_parts(db, dept_ids, year, month).items()
    }


def quantity_weights(counts: dict[int, int]) -> dict[int, Decimal]:
    """Количества как ВЕСА распределения — сами числа, а не округлённые проценты.

    Суммы считаются от весов напрямую (`distribute*` сами нормализуют), поэтому
    доля компании = база × количество / Σколичеств ТОЧНО, без потери на
    промежуточном округлении процента: 57000 × 45/104, а не 57000 × 43.27%.
    """
    return {cid: Decimal(n) for cid, n in counts.items() if n > 0}


def quantity_percents(counts: dict[int, int]) -> dict[int, Decimal]:
    """{company_id: процент} — количество компании / сумма количеств, до сотых.

    Проценты ПОКАЗЫВАЮТСЯ (в табеле, ведомости, Excel) и потому обязаны давать
    ровно 100.00: считаются тем же методом наибольшего остатка, что и деньги.
    Остаток отдаётся компании с наибольшей долей, а НЕ основной компании
    сотрудника — набор один на весь отдел, и у разных людей он обязан совпадать.
    """
    weights = quantity_weights(counts)
    if not weights:
        return {}
    return distribute(_HUNDRED, weights, main_key=None, step=PERCENT_STEP)


def quantity_total(counts: dict[int, int]) -> int:
    return sum(n for n in counts.values() if n > 0)


# ── Состояние для API (табель отдела и ведомость) ─────────────────────────────

def department_quantities_state(
    db: Session, dept_ids: list[int] | set[int] | None, year: int, month: int
) -> list[DepartmentQuantitiesRead]:
    """Количества и проценты по отделам с флагом — то, что показывает табель.

    В выдачу попадают ВСЕ отделы с флагом, даже те, где показатель за месяц ещё
    не введён (`is_empty=True`): именно в них его и надо завести, а невидимый
    блок ввода не завести нельзя.
    """
    flagged = quantity_department_ids(db, dept_ids)
    if not flagged:
        return []
    parts_by_dept = load_quantity_parts(db, flagged, year, month)
    departments = {
        d.id: d for d in db.query(Department).filter(Department.id.in_(flagged)).all()
    }
    # Колонки юрлиц — в настроенном порядке справочника (task_vedomost_format ч.1).
    company_order = order_index(
        cid for (cid,) in db.query(Company.id).order_by(*company_order_by()).all()
    )
    state: list[DepartmentQuantitiesRead] = []
    for dept_id in sorted(flagged):
        dept = departments.get(dept_id)
        parts = parts_by_dept.get(dept_id, {})
        counts = {cid: a + b for cid, (a, b) in parts.items()}
        percents = quantity_percents(counts)
        state.append(DepartmentQuantitiesRead(
            department_id=dept_id,
            department_name=dept.name if dept else None,
            metric_name=dept.quantity_metric_label if dept else None,
            part1_name=(dept.quantity_part1_name if dept else None) or None,
            part2_name=(dept.quantity_part2_name if dept else None) or None,
            has_parts=bool(dept.quantity_has_parts) if dept else False,
            year=year,
            month=month,
            items=[
                QuantityShareRead(
                    company_id=cid,
                    part1=parts[cid][0],
                    part2=parts[cid][1],
                    count=counts[cid],
                    percent=percents.get(cid, _ZERO),
                )
                for cid in sorted(
                    parts, key=lambda c: (company_order.get(c, len(company_order)), c)
                )
            ],
            total_part1=sum(a for a, _ in parts.values()),
            total_part2=sum(b for _, b in parts.values()),
            total_count=quantity_total(counts),
            is_empty=not counts,
        ))
    return state


def set_department_quantities(
    db: Session, department_id: int, year: int, month: int, items,
    actor_id: int | None = None,
) -> None:
    """Заменить набор количеств отдела за месяц целиком.

    Нули не хранятся: «компания без показателя» и «строка с 0» — одно и то же
    состояние, и хранить его двумя способами значит рано или поздно разойтись.
    Коммит — на вызывающем (вместе с audit log, как везде в проекте).
    """
    db.query(DepartmentQuantity).filter(
        DepartmentQuantity.department_id == department_id,
        DepartmentQuantity.year == year,
        DepartmentQuantity.month == month,
    ).delete(synchronize_session=False)
    for item in items:
        if (item.part1 or 0) + (item.part2 or 0) <= 0:
            continue
        db.add(DepartmentQuantity(
            department_id=department_id,
            company_id=item.company_id,
            year=year,
            month=month,
            part1=item.part1 or 0,
            part2=item.part2 or 0,
            created_by_id=actor_id,
        ))
