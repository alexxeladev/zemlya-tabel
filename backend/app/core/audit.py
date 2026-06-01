from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.users import User


def log_action(
    db: Session,
    actor: User,
    entity_type: str,
    entity_id: int | None,
    action: str,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
) -> None:
    entry = AuditLog(
        actor_id=actor.id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before=before,
        after=after,
        reason=reason,
    )
    db.add(entry)
