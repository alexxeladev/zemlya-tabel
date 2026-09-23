"""
Версии условий труда рабочего места (task_stage3_historicity, часть 1).

Условия, влияющие на расчёт, — тип оплаты и его база, график, коэффициенты,
официальная зарплата охранника — хранятся ВЕРСИЯМИ с датой начала действия.
Расчёт любого дня берёт версию, действовавшую в этот день
(`services/position_terms.terms_on`), поэтому повышение оклада посреди месяца
применяется с даты повышения, а не ко всему месяцу.

Версия действует с `effective_from` ВКЛЮЧИТЕЛЬНО и до дня, предшествующего
следующей версии. Первая версия каждой позиции начинается с `TERMS_BEGINNING`
(«с начала времён»): миграция перенесла в неё значения, действовавшие до этапа 3,
и расчёт любого прошлого месяца после неё не сдвинулся ни на копейку.

Поля с теми же именами на `EmployeePosition` — ЗЕРКАЛО ПОСЛЕДНЕЙ версии: их
читают формы, маскирование, импорт и вахта. Расчёт зеркало не читает.
Пишутся версии только слушателем сессии (`services/position_terms`) — из
изменений полей позиции; руками строки этой таблицы не создают.

Не версионируются (решение заказчика): ФИО, табельный номер, доступ, отдел и
компания позиции. Распределение по юрлицам версионируется отдельно — прямо в
`employee_company_shares.effective_from` (действует с 1-го числа месяца).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.schedules import Schedule

#: Начало действия первой версии — «с начала времён». Дата, а не NULL: версия
#: ищется сравнением `effective_from <= день`, и NULL пришлось бы обходить
#: отдельной веткой в каждом запросе. В интерфейсе показывается словами.
TERMS_BEGINNING = datetime.date(1900, 1, 1)

#: Версионируемые поля. Порядок — как в карточке позиции. Имена совпадают с
#: полями `EmployeePosition`: версия и зеркало читаются одним и тем же кодом.
TERM_FIELDS: tuple[str, ...] = (
    "pay_type",
    "rate",
    "shift_rate",
    "hour_rate",
    "schedule_id",
    "weekend_pay_type",
    "weekend_coefficient",
    "weekend_fixed_rate",
    "holiday_pay_type",
    "holiday_coefficient",
    "holiday_fixed_rate",
    "overtime_coefficient",
    "is_official",
    "official_salary",
)


class PositionTerms(Base):
    """Одна версия условий рабочего места, действующая с `effective_from`."""

    __tablename__ = "position_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("employee_positions.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    effective_from: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    pay_type: Mapped[str] = mapped_column(String(20), nullable=False)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    shift_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    hour_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id"), nullable=True
    )
    weekend_pay_type: Mapped[str] = mapped_column(String(20), nullable=False)
    weekend_coefficient: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    weekend_fixed_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    holiday_pay_type: Mapped[str] = mapped_column(String(20), nullable=False)
    holiday_coefficient: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    holiday_fixed_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    overtime_coefficient: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    is_official: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    official_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    #: Кто и когда завёл версию — для истории в карточке. Без внешнего ключа на
    #: сотрудника: запись остаётся понятной и после его удаления.
    created_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    schedule: Mapped[Optional[Schedule]] = relationship("Schedule", lazy="joined")

    __table_args__ = (
        UniqueConstraint("position_id", "effective_from", name="uq_position_terms_date"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<PositionTerms pos={self.position_id} from={self.effective_from}>"
