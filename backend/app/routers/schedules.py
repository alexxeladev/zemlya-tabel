from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import get_current_user, require_role
from app.database import get_db
from app.models.schedules import Schedule
from app.models.employees import Employee
from app.schemas.schedule import ScheduleCreate, ScheduleRead, ScheduleUpdate

router = APIRouter()

_admin_only = require_role("admin")


def _to_dict(obj: Schedule) -> dict:
    return {
        "id": obj.id,
        "name": obj.name,
        "hours_per_shift": obj.hours_per_shift,
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
        description=payload.description,
        is_active=True,
    )
    db.add(schedule)
    db.flush()
    log_action(db, actor, "schedule", schedule.id, "create", after=_to_dict(schedule))
    db.commit()
    db.refresh(schedule)
    return schedule


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
