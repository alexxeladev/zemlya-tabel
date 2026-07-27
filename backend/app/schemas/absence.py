from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

AbsenceKindLiteral = Literal["vacation", "unpaid", "sick", "absent"]


class AbsenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: int
    work_date: date
    kind: AbsenceKindLiteral
    code: str  # ОТ / ДО / Б / Н — для отображения в табеле и Excel
    # Больничный сверх годового лимита — отмечен, но не оплачивается (ч.2)
    over_limit: bool = False


class AbsenceInput(BaseModel):
    """Поставить код отсутствия на день; kind=None — снять отметку."""

    employee_id: int
    work_date: date
    kind: Optional[AbsenceKindLiteral] = None
