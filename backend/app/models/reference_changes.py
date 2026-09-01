from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# ── Источники изменения ───────────────────────────────────────────────────────
# Различать надо не «кто нажал», а КАКИМ ПУТЁМ данные поменялись: правка в
# карточке и массовый перенос выглядят в данных одинаково, а разбираться в них
# приходится по-разному.
SOURCE_UI = "ui"          # обычный запрос: карточка, форма, экран
SOURCE_IMPORT = "import"  # импорт сотрудников из Excel
SOURCE_BULK = "bulk"      # массовая операция (перенос отдела в другое юрлицо)
SOURCE_CLI = "cli"        # команды app.cli и сиды
SOURCE_SYSTEM = "system"  # изменение без пользователя (фон, тесты)

SOURCES = (SOURCE_UI, SOURCE_IMPORT, SOURCE_BULK, SOURCE_CLI, SOURCE_SYSTEM)

SOURCE_LABELS = {
    SOURCE_UI: "Интерфейс",
    SOURCE_IMPORT: "Импорт из Excel",
    SOURCE_BULK: "Массовая операция",
    SOURCE_CLI: "Команда/сид",
    SOURCE_SYSTEM: "Система",
}

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"


class ReferenceChange(Base):
    """Журнал изменений СПРАВОЧНЫХ данных: кто, когда, какое поле, было → стало.

    Одна строка = ОДНО изменённое поле. «Сохранил карточку и поменял оклад и
    график» — две строки, а не одна с кашей в JSON: журнал читают глазами и
    фильтруют по полю, а разбирать снимок целиком человеку неудобно.

    Не путать с `audit_log` (`app/models/audit_log.py`): тот пишет ОПЕРАЦИИ —
    часы, отсутствия, ночные, статусы периодов, экспорты — снимками before/after
    и остаётся как был. Здесь только справочники и только реальные расхождения.

    Записи **самодостаточны**: `entity_label`, `actor_name` и человекочитаемые
    значения ссылок сохраняются текстом, поэтому после удаления сотрудника или
    отдела запись остаётся понятной. Поэтому же здесь НЕТ внешних ключей —
    физическое удаление сущности не должно ни падать, ни чистить журнал.
    """

    __tablename__ = "reference_changes"

    id: Mapped[int] = mapped_column(primary_key=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Кто. Без FK (см. докстринг) — id для фильтра, имя для чтения.
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Каким путём. Значения — SOURCE_* выше.
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=SOURCE_UI
    )

    # Общий идентификатор массовой операции: все строки одного переноса или
    # одного импорта несут его, и «что натворил этот перенос» смотрится списком.
    # У одиночной правки он тоже есть — свой на каждый запрос.
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Сотрудник, к которому относится запись: для позиции — её владелец.
    # Денормализация ради истории в карточке: иначе «покажи всё по человеку»
    # требует объединения с позициями, в том числе уже удалёнными.
    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    action: Mapped[str] = mapped_column(String(10), nullable=False)

    # Поле пусто у создания и удаления — там меняется сущность целиком.
    field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Журнал растёт быстро, и все четыре запроса экрана обязаны идти по
        # индексу: лента по дате, история сущности, история сотрудника,
        # разбор массовой операции.
        Index("ix_reference_changes_created_at", "created_at"),
        Index("ix_reference_changes_entity", "entity_type", "entity_id"),
        Index("ix_reference_changes_employee", "employee_id"),
        Index("ix_reference_changes_actor", "actor_id"),
        Index("ix_reference_changes_operation", "operation_id"),
    )
