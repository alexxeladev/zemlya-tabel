from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.employees import Employee
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import MAX_RANGE_MONTHS, build_dashboard, months_in_range

router = APIRouter()


def _valid(year: int, month: int) -> bool:
    return 1 <= month <= 12 and 2000 <= year <= 2100


@router.get("/{year}/{month}", response_model=DashboardResponse)
def get_dashboard(
    year: int,
    month: int,
    to_year: int | None = None,
    to_month: int | None = None,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Сводный дашборд за месяц или за ДИАПАЗОН месяцев.

    (year, month) — начало периода; `?to_year=&to_month=` — конец включительно
    (не заданы — один месяц, как раньше). Видимость по ролям шьётся в сервисе:
    admin/accountant — вся компания, manager — свой отдел,
    employee — только свои часы (без ФОТ и периодов).
    """
    if not _valid(year, month):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    if (to_year is None) != (to_month is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="to_year and to_month must be given together",
        )
    if to_year is not None and to_month is not None:
        if not _valid(to_year, to_month):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid to_year/to_month",
            )
        if (to_year, to_month) < (year, month):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Period end is earlier than its start",
            )
        # Каждый месяц диапазона — полный расчёт ЗП; без потолка запрос «с 2000
        # года» повесил бы сервер.
        if len(months_in_range(year, month, to_year, to_month)) > MAX_RANGE_MONTHS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Period is longer than {MAX_RANGE_MONTHS} months",
            )
    return build_dashboard(db, actor, year, month, to_year, to_month)
