from __future__ import annotations

import calendar as _cal
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.schemas.calendar import (
    CalendarImportRequest,
    CalendarRead,
    DayInfo,
    MonthData,
    MonthSummary,
)
from app.services.calendar import (
    CalendarFetchError,
    ensure_calendar,
    norm_hours_for_period,
    reload_calendar,
    save_calendar_from_dict,
    short_days_in_month,
    workdays_in_month,
    is_holiday,
    is_short_day,
)

router = APIRouter()

_admin_only = require_role("admin")


def _build_calendar_read(cal: ProductionCalendar) -> CalendarRead:
    data = cal.data
    months = [MonthData(**m) for m in data.get("months", [])]
    workdays_total = sum(workdays_in_month(data, cal.year, m.month) for m in months)
    short_days_total = sum(short_days_in_month(data, m.month) for m in months)
    return CalendarRead(
        id=cal.id,
        year=cal.year,
        months=months,
        source=cal.source,
        loaded_at=cal.loaded_at,
        workdays_total=workdays_total,
        short_days_total=short_days_total,
    )


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_calendar(
    body: CalendarImportRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(_admin_only),
) -> CalendarRead:
    existing = db.query(ProductionCalendar).filter_by(year=body.year).first()
    data = {"year": body.year, "months": [m.model_dump() for m in body.months]}
    cal = save_calendar_from_dict(db, body.year, data, source="manual")
    log_action(db, current_user, "production_calendar", cal.id, "calendar_imported")
    db.commit()
    if existing:
        response.status_code = status.HTTP_200_OK
    return _build_calendar_read(cal)


@router.get("/{year}", response_model=CalendarRead)
async def get_calendar(
    year: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> CalendarRead:
    try:
        cal = await ensure_calendar(db, year)
    except CalendarFetchError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Календарь {year} не найден. "
                "Загрузите вручную через POST /api/calendar/import "
                "или повторите попытку позже."
            ),
        )
    return _build_calendar_read(cal)


@router.post("/{year}/load", response_model=CalendarRead)
async def load_calendar(
    year: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(_admin_only),
) -> CalendarRead:
    try:
        cal = await reload_calendar(db, year)
    except CalendarFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    log_action(db, current_user, "production_calendar", cal.id, "calendar_loaded")
    db.commit()
    return _build_calendar_read(cal)


@router.get("/{year}/{month}/summary", response_model=MonthSummary)
async def get_month_summary(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> MonthSummary:
    try:
        cal = await ensure_calendar(db, year)
    except CalendarFetchError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Данные календаря за {year} год недоступны.",
        )

    data = cal.data
    days_in_month = _cal.monthrange(year, month)[1]
    days: list[DayInfo] = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if is_holiday(data, month, day):
            dtype: str = "holiday"
        elif is_short_day(data, month, day):
            dtype = "short"
        else:
            dtype = "work"
        days.append(DayInfo(day=day, type=dtype, weekday=d.weekday()))  # type: ignore[arg-type]

    return MonthSummary(
        year=year,
        month=month,
        workdays=workdays_in_month(data, year, month),
        short_days=short_days_in_month(data, month),
        norm_hours_8h=norm_hours_for_period(data, year, month, 8),
        days=days,
    )
