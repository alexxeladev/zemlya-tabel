from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database import get_db
from app.models.employees import Employee
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import build_dashboard

router = APIRouter()


@router.get("/{year}/{month}", response_model=DashboardResponse)
def get_dashboard(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(get_current_user),
):
    """Сводный дашборд за месяц. Видимость по ролям шьётся в сервисе:
    admin/accountant — вся компания, manager — свой отдел,
    employee — только свои часы (без ФОТ и периодов)."""
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid year/month"
        )
    return build_dashboard(db, actor, year, month)
