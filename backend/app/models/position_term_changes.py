"""
Изменения условий труда рабочего места — строка на ПОЛЕ (ADR-001).

Условия (тип оплаты и его база, график, коэффициенты, официальное
трудоустройство) меняются каждое со своей даты, поэтому и хранятся по одному:
строка = «поле · с какой даты · значение». Условия, действующие в день D, —
по каждому полю последнее изменение с `effective_from <= D`
(`services/position_terms.terms_on`).

**Почему не снимок всех полей в строке** (так было до ADR-001, таблица
`position_terms`): снимок заставлял бэкенд угадывать, какое поле имел в виду
человек, сравнивая присланную форму с каким-то снимком, а правило «поле
действует с D и дальше» приходилось изображать мутацией соседних снимков. На
препроде это дало боевой дефект: правку задним числом нельзя было внести, если
значение совпадало с тем, что показывает карточка, и 64 отработанных выходных
часа ушли в расчёт по коэффициенту ×0. Здесь угадывать нечего: пришёл список
изменений — записали ровно его; «то же значение, но с сентября» — обычная
строка, потому что на сентябрь у поля стоит другое значение.

Значение лежит в JSON и декодируется ПО ИМЕНИ ПОЛЯ (`TERM_FIELD_TYPES`): одна
форма строки на все поля, без колонки под каждый тип. Деньги и коэффициенты
хранятся строкой и с тем же масштабом, что у колонок-зеркал (два знака после
запятой), иначе `Decimal` ездил бы туда-обратно через float.

Поля с теми же именами на `EmployeePosition` — ЗЕРКАЛО ПОСЛЕДНЕГО изменения
каждого поля: их читают формы, маскирование, импорт и вахта. Расчёт зеркало не
читает.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.position_terms import TERM_FIELDS, TERMS_BEGINNING


class _JSONB(TypeDecorator):
    """JSONB on PostgreSQL, JSON elsewhere (SQLite for tests)."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

#: Тип значения каждого условия — по нему значение кодируется в JSON и читается
#: обратно. Список полей — тот же `TERM_FIELDS`, что и у зеркала на позиции.
TERM_FIELD_TYPES: dict[str, type] = {
    "pay_type": str,
    "rate": Decimal,
    "shift_rate": Decimal,
    "hour_rate": Decimal,
    "schedule_id": int,
    "weekend_pay_type": str,
    "weekend_coefficient": Decimal,
    "weekend_fixed_rate": Decimal,
    "holiday_pay_type": str,
    "holiday_coefficient": Decimal,
    "holiday_fixed_rate": Decimal,
    "overtime_coefficient": Decimal,
    "is_official": bool,
    "official_salary": Decimal,
}

assert tuple(TERM_FIELD_TYPES) == TERM_FIELDS, "типы условий разошлись с TERM_FIELDS"

#: Масштаб денег и коэффициентов — как у колонок-зеркал (Numeric(*, 2)).
_MONEY_SCALE = Decimal("0.01")


def encode_term_value(field: str, value: Any) -> Any:
    """Значение условия → JSON. Деньги — строкой с двумя знаками: иначе
    `Decimal` прошёл бы через float и «60000.00» стало бы 60000.0."""
    if value is None:
        return None
    kind = TERM_FIELD_TYPES[field]
    if kind is Decimal:
        return str(Decimal(value).quantize(_MONEY_SCALE))
    if kind is bool:
        return bool(value)
    if kind is int:
        return int(value)
    return str(value)


def decode_term_value(field: str, raw: Any) -> Any:
    """JSON → значение условия того же типа, что колонка-зеркало."""
    if raw is None:
        return None
    kind = TERM_FIELD_TYPES[field]
    if kind is Decimal:
        return Decimal(str(raw)).quantize(_MONEY_SCALE)
    if kind is bool:
        return bool(raw)
    if kind is int:
        return int(raw)
    return str(raw)


class PositionTermChange(Base):
    """Одно изменение одного условия рабочего места, действующее с даты."""

    __tablename__ = "position_term_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("employee_positions.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    #: Имя поля из `TERM_FIELDS`. Строкой, а не перечислением: список полей
    #: живёт в одном месте (`position_terms.TERM_FIELDS`), и миграция под новое
    #: условие не нужна.
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Действует с этого дня ВКЛЮЧИТЕЛЬНО и до следующего изменения ЭТОГО поля.
    effective_from: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    value: Mapped[Any | None] = mapped_column(_JSONB, nullable=True)

    #: Кто и когда внёс — для истории условий в карточке. Без внешнего ключа на
    #: сотрудника: запись остаётся понятной и после его удаления.
    created_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "position_id", "field", "effective_from", name="uq_term_change_field_date",
        ),
    )

    @property
    def decoded(self) -> Any:
        """Значение условия того же типа, что колонка-зеркало позиции."""
        return decode_term_value(self.field, self.value)

    @decoded.setter
    def decoded(self, value: Any) -> None:
        self.value = encode_term_value(self.field, value)

    @property
    def is_base(self) -> bool:
        """Базовое значение «с начала» — не изменение, а стартовая точка."""
        return self.effective_from <= TERMS_BEGINNING

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<PositionTermChange pos={self.position_id} {self.field}"
            f" from={self.effective_from} value={self.value!r}>"
        )
