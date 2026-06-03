from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.employees import Employee
from app.models.timesheet_periods import TimesheetPeriod
from app.schemas.timesheet_period import TimesheetPeriodRead


class PeriodLockedException(Exception):
    """Raised when a cell edit is attempted on a non-draft period."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Период закрыт для редактирования, статус: {status}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _can_edit(period: TimesheetPeriod) -> bool:
    return period.status == "draft"


def _can_submit(period: TimesheetPeriod, actor: Employee) -> bool:
    if period.status != "draft":
        return False
    if period.department_id is None:
        return False
    if actor.role == "admin":
        return True
    return actor.role == "manager" and actor.department_id == period.department_id


def _can_close(period: TimesheetPeriod, actor: Employee) -> bool:
    if actor.role not in ("accountant", "admin"):
        return False
    if period.status == "pending_review":
        return True
    # NULL-department can skip directly from draft to closed
    if period.status == "draft" and period.department_id is None:
        return True
    return False


def _can_return(period: TimesheetPeriod, actor: Employee) -> bool:
    return period.status == "pending_review" and actor.role in ("accountant", "admin")


def _can_reopen(period: TimesheetPeriod, actor: Employee) -> bool:
    return period.status == "closed" and actor.role == "admin"


def make_period_read(period: TimesheetPeriod, actor: Employee) -> TimesheetPeriodRead:
    return TimesheetPeriodRead(
        id=period.id,
        department_id=period.department_id,
        department_name=(
            period.department.name if period.department else "Без отдела"
        ),
        year=period.year,
        month=period.month,
        status=period.status,
        submitted_at=period.submitted_at,
        submitted_by_name=(
            period.submitted_by.full_name if period.submitted_by else None
        ),
        reviewed_at=period.reviewed_at,
        reviewed_by_name=(
            period.reviewed_by.full_name if period.reviewed_by else None
        ),
        closed_at=period.closed_at,
        closed_by_name=(
            period.closed_by.full_name if period.closed_by else None
        ),
        can_edit=_can_edit(period),
        can_submit=_can_submit(period, actor),
        can_close=_can_close(period, actor),
        can_return=_can_return(period, actor),
        can_reopen=_can_reopen(period, actor),
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_or_create_period(
    db: Session,
    department_id: int | None,
    year: int,
    month: int,
) -> TimesheetPeriod:
    """Returns existing period or creates a new draft. Flushes but does not commit."""
    q = db.query(TimesheetPeriod).filter(
        TimesheetPeriod.year == year,
        TimesheetPeriod.month == month,
    )
    if department_id is None:
        q = q.filter(TimesheetPeriod.department_id.is_(None))
    else:
        q = q.filter(TimesheetPeriod.department_id == department_id)

    period = q.first()
    if period is None:
        period = TimesheetPeriod(
            department_id=department_id,
            year=year,
            month=month,
            status="draft",
        )
        db.add(period)
        db.flush()
    return period


def can_edit_cells(period: TimesheetPeriod) -> bool:
    return period.status == "draft"


# ── Workflow transitions ──────────────────────────────────────────────────────

def submit_for_review(
    db: Session,
    period: TimesheetPeriod,
    actor: Employee,
) -> TimesheetPeriod:
    if period.department_id is None:
        raise ValueError("Период без отдела нельзя отправить на проверку")
    if period.status != "draft":
        raise ValueError(f"Ожидается статус draft, текущий: {period.status}")
    if actor.role not in ("admin", "manager"):
        raise PermissionError("Только manager или admin может отправить на проверку")
    if actor.role == "manager" and actor.department_id != period.department_id:
        raise PermissionError("Manager может отправить только свой отдел")

    before_status = period.status
    period.status = "pending_review"
    period.submitted_at = datetime.utcnow()
    period.submitted_by_id = actor.id
    db.flush()
    log_action(
        db, actor, "timesheet_period", period.id, "period_submitted",
        before={"status": before_status},
        after={"status": period.status},
    )
    db.commit()
    db.refresh(period)
    return period


def return_to_draft(
    db: Session,
    period: TimesheetPeriod,
    actor: Employee,
    reason: str,
) -> TimesheetPeriod:
    if not reason or len(reason.strip()) < 3:
        raise ValueError("Причина возврата обязательна (минимум 3 символа)")
    if period.status != "pending_review":
        raise ValueError(f"Ожидается статус pending_review, текущий: {period.status}")
    if actor.role not in ("accountant", "admin"):
        raise PermissionError("Только accountant или admin может вернуть на доработку")

    before_status = period.status
    period.status = "draft"
    period.reviewed_at = datetime.utcnow()
    period.reviewed_by_id = actor.id
    db.flush()
    log_action(
        db, actor, "timesheet_period", period.id, "period_returned",
        before={"status": before_status},
        after={"status": period.status},
        reason=reason,
    )
    db.commit()
    db.refresh(period)
    return period


def close_period(
    db: Session,
    period: TimesheetPeriod,
    actor: Employee,
) -> TimesheetPeriod:
    if actor.role not in ("accountant", "admin"):
        raise PermissionError("Только accountant или admin может закрыть период")
    # NULL-department can close from draft directly
    if period.department_id is None:
        if period.status not in ("draft", "pending_review"):
            raise ValueError(f"Нельзя закрыть период со статусом: {period.status}")
    else:
        if period.status != "pending_review":
            raise ValueError(f"Ожидается статус pending_review, текущий: {period.status}")

    before_status = period.status
    period.status = "closed"
    period.closed_at = datetime.utcnow()
    period.closed_by_id = actor.id
    db.flush()
    log_action(
        db, actor, "timesheet_period", period.id, "period_closed",
        before={"status": before_status},
        after={"status": period.status},
    )
    db.commit()
    db.refresh(period)
    return period


def reopen_period(
    db: Session,
    period: TimesheetPeriod,
    actor: Employee,
    reason: str,
) -> TimesheetPeriod:
    if not reason or len(reason.strip()) < 3:
        raise ValueError("Причина переоткрытия обязательна (минимум 3 символа)")
    if period.status != "closed":
        raise ValueError(f"Ожидается статус closed, текущий: {period.status}")
    if actor.role != "admin":
        raise PermissionError("Только admin может переоткрыть закрытый период")

    period.status = "draft"
    db.flush()
    log_action(
        db, actor, "timesheet_period", period.id, "period_reopened",
        before={"status": "closed"},
        after={"status": "draft"},
        reason=reason,
    )
    db.commit()
    db.refresh(period)
    return period
