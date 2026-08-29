"""
Личные отметки «строку проверил» (task_pilot_ux ч.3).

Единственное место, где эти отметки читаются и пишутся. Главное правило:
выборка ВСЕГДА сужена по `user_id` актора — отметка личная, чужие её не
видят и снять не могут. Никакой «общей» проверки строки здесь нет и не
должно появиться: workflow периода живёт отдельно (draft → pending_review
→ closed), а это просто закладка табельщика.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.employees import Employee
from app.models.row_checks import RowCheck


def checked_position_ids(
    db: Session,
    actor: Employee,
    year: int,
    month: int,
    position_ids: list[int] | None = None,
) -> list[int]:
    """Отмеченные АКТОРОМ рабочие места за месяц — одним запросом.

    Уезжает вместе с выдачей табеля: запрос на строку сделал бы 70 запросов
    там, где хватает одного.
    """
    query = db.query(RowCheck.position_id).filter(
        RowCheck.user_id == actor.id,
        RowCheck.year == year,
        RowCheck.month == month,
    )
    if position_ids is not None:
        if not position_ids:
            return []
        query = query.filter(RowCheck.position_id.in_(position_ids))
    return sorted(row[0] for row in query.all())


def set_row_check(
    db: Session,
    actor: Employee,
    position_id: int,
    year: int,
    month: int,
    value: bool,
) -> bool:
    """Поставить/снять отметку. Идемпотентна, возвращает итоговое состояние.

    Ни расчёта, ни пересчёта: это закладка одного человека. В audit log не
    пишется намеренно — журнал про изменения ДАННЫХ табеля, а личная
    закладка данных не меняет и залила бы его шумом (70 строк × месяц ×
    каждый табельщик).
    """
    existing = (
        db.query(RowCheck)
        .filter(
            RowCheck.user_id == actor.id,
            RowCheck.position_id == position_id,
            RowCheck.year == year,
            RowCheck.month == month,
        )
        .first()
    )
    if value and existing is None:
        db.add(
            RowCheck(
                user_id=actor.id, position_id=position_id, year=year, month=month
            )
        )
    elif not value and existing is not None:
        db.delete(existing)
    db.commit()
    return value
