import calendar as _cal
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.employees import Employee
from app.schemas.schedule import (
    SchedulePreview,
    SchedulePreviewDay,
    SchedulePreviewRequest,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    validate_cycle_fields,
)
from app.services.calendar import is_holiday, is_short_day
from app.services.work_schedule import (
    is_planned_work_day,
    schedule_issue,
    shift_hours_for_date,
)

router = APIRouter()

_admin_only = require_role("admin")


def _to_dict(obj: Schedule) -> dict:
    return {
        "id": obj.id,
        "name": obj.name,
        "hours_per_shift": obj.hours_per_shift,
        "schedule_type": obj.schedule_type,
        "work_weekdays": obj.work_weekdays,
        "cycle_start_date": obj.cycle_start_date.isoformat() if obj.cycle_start_date else None,
        "cycle_work_days": obj.cycle_work_days,
        "cycle_off_days": obj.cycle_off_days,
        "description": obj.description,
        "is_active": obj.is_active,
    }


@router.get("", response_model=list[ScheduleRead])
def list_schedules(
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return db.query(Schedule).all()


@router.post("", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    if db.query(Schedule).filter(Schedule.name == payload.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Name already exists")
    schedule = Schedule(
        name=payload.name,
        hours_per_shift=payload.hours_per_shift,
        schedule_type=payload.schedule_type,
        work_weekdays=payload.work_weekdays,
        cycle_start_date=payload.cycle_start_date,
        cycle_work_days=payload.cycle_work_days,
        cycle_off_days=payload.cycle_off_days,
        description=payload.description,
        is_active=True,
    )
    db.add(schedule)
    db.flush()
    log_action(db, actor, "schedule", schedule.id, "create", after=_to_dict(schedule))
    db.commit()
    db.refresh(schedule)
    return schedule


@router.post("/preview", response_model=SchedulePreview)
def preview_schedule(
    payload: SchedulePreviewRequest,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Какие дни месяца рабочие по графику — чтобы админ проверил фазу цикла
    ещё до сохранения. График передаётся полями формы, а не id.
    """
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if not 1 <= payload.month <= 12:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неверный месяц")

    cal = db.query(ProductionCalendar).filter_by(year=payload.year).first()
    calendar_data = cal.data if cal else None

    # Черновой график: превью работает и для несохранённой формы.
    draft = Schedule(
        name=payload.name or "",
        hours_per_shift=payload.hours_per_shift,
        schedule_type=payload.schedule_type,
        work_weekdays=payload.work_weekdays,
        cycle_start_date=payload.cycle_start_date,
        cycle_work_days=payload.cycle_work_days,
        cycle_off_days=payload.cycle_off_days,
    )
    issue = schedule_issue(draft)

    days: list[SchedulePreviewDay] = []
    work_days = 0
    norm_hours = 0
    total_days = _cal.monthrange(payload.year, payload.month)[1]
    for day in range(1, total_days + 1):
        work_date = date(payload.year, payload.month, day)
        is_work = issue is None and is_planned_work_day(draft, work_date, calendar_data)
        hours = int(shift_hours_for_date(draft, work_date, calendar_data)) if is_work else 0
        if is_work:
            work_days += 1
            norm_hours += hours
        days.append(SchedulePreviewDay(
            day=day,
            work_date=work_date,
            weekday=work_date.weekday(),
            is_work_day=is_work,
            hours=hours,
            is_holiday=calendar_data is not None and is_holiday(calendar_data, payload.month, day),
            is_short_day=calendar_data is not None and is_short_day(calendar_data, payload.month, day),
        ))

    return SchedulePreview(
        year=payload.year,
        month=payload.month,
        days=days,
        work_days=work_days,
        norm_hours=norm_hours,
        has_calendar=calendar_data is not None,
        issue=issue,
    )


@router.get("/{schedule_id}", response_model=ScheduleRead)
def get_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    if current_user.role == "employee":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleRead)
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    before = _to_dict(schedule)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)
    # Проверяем поля цикла уже после слияния: PATCH может менять только тип
    # или только дату старта, целостность важна у итогового графика.
    try:
        validate_cycle_fields(
            schedule.schedule_type,
            schedule.cycle_start_date,
            schedule.cycle_work_days,
            schedule.cycle_off_days,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.flush()
    log_action(db, actor, "schedule", schedule.id, "update", before=before, after=_to_dict(schedule))
    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    active_employees = [e for e in schedule.employees if e.is_active]
    if active_employees:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Нельзя удалить: на этом графике {len(active_employees)} сотрудников",
        )
    before = _to_dict(schedule)
    schedule.is_active = False
    db.flush()
    log_action(db, actor, "schedule", schedule.id, "delete", before=before)
    db.commit()
