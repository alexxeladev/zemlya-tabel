"""
Заявки на подбор → проценты распределения по юрлицам (task_hr_applications).

Отдел с флагом `Department.uses_applications_distribution` (у нас это HR)
распределяет зарплату своих сотрудников не по обычному каскаду, а по числу
отработанных за месяц заявок на подбор для каждого юрлица:

    процент компании = заявки компании / сумма заявок месяца

Проценты ЕДИНЫЕ для всего отдела: и у рекрутёра, и у его руководителя зарплата
делится одинаково — заявки отработаны отделом, а не человеком.

Здесь только заявки и проценты из них. САМО распределение суммы (метод
наибольшего остатка, остаток — основной компании) остаётся в
`app.services.distribution` — второй реализации округления в проекте нет.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.department_applications import DepartmentApplication
from app.models.departments import Department
from app.schemas.application import ApplicationShareRead, DepartmentApplicationsRead
from app.services.distribution import PERCENT_STEP, distribute

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def applications_department_ids(
    db: Session, dept_ids: list[int] | set[int] | None
) -> set[int]:
    """Из переданных отделов — те, что распределяются по заявкам.

    `dept_ids=None` — «все отделы с флагом» (так их видит admin/accountant, у
    которого выборка не сужена отделом); пустой список — ни одного.
    """
    query = db.query(Department.id).filter(
        Department.uses_applications_distribution == True,  # noqa: E712
    )
    if dept_ids is not None:
        ids = {d for d in dept_ids if d is not None}
        if not ids:
            return set()
        query = query.filter(Department.id.in_(ids))
    return {r[0] for r in query.all()}


def load_applications(
    db: Session, dept_ids: list[int] | set[int], year: int, month: int
) -> dict[int, dict[int, tuple[int, int]]]:
    """{department_id: {company_id: (в работе, закрытые)}} за КОНКРЕТНЫЙ месяц.

    Заявки помесячные: в наборе только строки этого года и месяца, прошлый месяц
    на распределение не влияет.
    """
    result: dict[int, dict[int, tuple[int, int]]] = {}
    ids = {d for d in dept_ids if d is not None}
    if not ids:
        return result
    rows = (
        db.query(DepartmentApplication)
        .filter(
            DepartmentApplication.department_id.in_(ids),
            DepartmentApplication.year == year,
            DepartmentApplication.month == month,
        )
        .all()
    )
    for r in rows:
        if r.count > 0:
            result.setdefault(r.department_id, {})[r.company_id] = (
                r.in_progress or 0, r.closed or 0,
            )
    return result


def load_application_counts(
    db: Session, dept_ids: list[int] | set[int], year: int, month: int
) -> dict[int, dict[int, int]]:
    """{department_id: {company_id: ВСЕГО заявок}} — то, чем считается распределение.

    Всего = в работе + закрытые: обе части равноправны, платит отдел за все
    отработанные заявки, а не только за закрытые.
    """
    return {
        dept_id: {cid: work + closed for cid, (work, closed) in by_company.items()}
        for dept_id, by_company in load_applications(db, dept_ids, year, month).items()
    }


def application_weights(counts: dict[int, int]) -> dict[int, Decimal]:
    """Заявки как ВЕСА распределения — числа заявок, а не округлённые проценты.

    Суммы считаются от весов напрямую (`distribute` сам нормализует), поэтому
    доля компании = итог × заявки / Σзаявок ТОЧНО, без потери копеек на
    промежуточном округлении процента: 320000 × 16/43 = 119069.77 → 119070 ₽,
    а не 320000 × 37.21%.
    """
    return {cid: Decimal(n) for cid, n in counts.items() if n > 0}


def application_percents(counts: dict[int, int]) -> dict[int, Decimal]:
    """{company_id: процент} — заявки компании / сумма заявок, до сотых.

    Проценты ПОКАЗЫВАЮТСЯ (в табеле, ведомости, Excel) и потому обязаны давать
    ровно 100.00: считаются тем же методом наибольшего остатка, что и деньги.
    Остаток отдаётся компании с наибольшей долей, а НЕ основной компании
    сотрудника — набор один на весь отдел, и у разных людей он обязан совпадать.
    """
    weights = application_weights(counts)
    if not weights:
        return {}
    return distribute(_HUNDRED, weights, main_key=None, step=PERCENT_STEP)


def applications_total(counts: dict[int, int]) -> int:
    return sum(n for n in counts.values() if n > 0)


# ── Состояние для API (табель HR и ведомость) ─────────────────────────────────

def department_applications_state(
    db: Session, dept_ids: list[int] | set[int] | None, year: int, month: int
) -> list[DepartmentApplicationsRead]:
    """Заявки и проценты по отделам с флагом — то, что показывает табель.

    В выдачу попадают ВСЕ отделы с флагом, даже те, где заявки за месяц ещё не
    введены (`is_empty=True`): именно в них их и надо завести, а невидимый блок
    ввода не завести нельзя.
    """
    flagged = applications_department_ids(db, dept_ids)
    if not flagged:
        return []
    parts_by_dept = load_applications(db, flagged, year, month)
    names = dict(
        db.query(Department.id, Department.name)
        .filter(Department.id.in_(flagged))
        .all()
    )
    state: list[DepartmentApplicationsRead] = []
    for dept_id in sorted(flagged):
        parts = parts_by_dept.get(dept_id, {})
        counts = {cid: work + closed for cid, (work, closed) in parts.items()}
        percents = application_percents(counts)
        state.append(DepartmentApplicationsRead(
            department_id=dept_id,
            department_name=names.get(dept_id),
            year=year,
            month=month,
            applications=[
                ApplicationShareRead(
                    company_id=cid,
                    in_progress=parts[cid][0],
                    closed=parts[cid][1],
                    count=counts[cid],
                    percent=percents.get(cid, _ZERO),
                )
                for cid in sorted(parts)
            ],
            total_in_progress=sum(work for work, _ in parts.values()),
            total_closed=sum(closed for _, closed in parts.values()),
            total_applications=applications_total(counts),
            is_empty=not counts,
        ))
    return state


def set_department_applications(
    db: Session, department_id: int, year: int, month: int, items,
    actor_id: int | None = None,
) -> None:
    """Заменить набор заявок отдела за месяц целиком.

    Нули не хранятся: «компания без заявок» и «строка с 0» — одно и то же
    состояние, и хранить его двумя способами значит рано или поздно разойтись.
    Коммит — на вызывающем (вместе с audit log, как везде в проекте).
    """
    db.query(DepartmentApplication).filter(
        DepartmentApplication.department_id == department_id,
        DepartmentApplication.year == year,
        DepartmentApplication.month == month,
    ).delete(synchronize_session=False)
    for item in items:
        if (item.in_progress or 0) + (item.closed or 0) <= 0:
            continue
        db.add(DepartmentApplication(
            department_id=department_id,
            company_id=item.company_id,
            year=year,
            month=month,
            in_progress=item.in_progress or 0,
            closed=item.closed or 0,
            created_by_id=actor_id,
        ))
