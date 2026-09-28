"""Условия рабочего места: изменения по ПОЛЮ вместо снимков всех полей

ADR-001 (task_terms_per_field). Версия условий была снимком всех 14 полей, из-за
чего бэкенд угадывал, какое поле человек имел в виду, а правило «поле действует
с даты и дальше» изображалось мутацией соседних снимков. Теперь строка = поле ·
дата · значение.

Разворот истории: первая версия позиции даёт БАЗОВЫЕ строки по всем полям (её
дата, обычно «с начала времён»), каждая следующая — по строке на поле, которое
реально отличается от предыдущей версии. Автор и время переносятся. Условия на
любой день после миграции собираются из этих строк и обязаны совпасть с тем, что
давал снимок, — сверка ведомости всех позиций за все месяцы до и после.

`position_terms` НЕ удаляется: пока переход не закончен, снимки остаются на
случай отката (`downgrade` собирает их заново из изменений, поэтому правки,
сделанные уже в новой модели, при откате не теряются).

Список полей и кодирование значений здесь ЗАФИКСИРОВАНЫ копией: миграция —
слепок на момент выката, и изменившийся позже `TERM_FIELDS` не должен менять
результат этого разворота.

Revision ID: a9b8c7d6e5f4
Revises: f72c8c48f4fe
"""
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "a9b8c7d6e5f4"
down_revision = "f72c8c48f4fe"
branch_labels = None
depends_on = None

#: поле → тип значения (копия `TERM_FIELD_TYPES` на момент миграции)
_FIELDS: tuple[tuple[str, type], ...] = (
    ("pay_type", str),
    ("rate", Decimal),
    ("shift_rate", Decimal),
    ("hour_rate", Decimal),
    ("schedule_id", int),
    ("weekend_pay_type", str),
    ("weekend_coefficient", Decimal),
    ("weekend_fixed_rate", Decimal),
    ("holiday_pay_type", str),
    ("holiday_coefficient", Decimal),
    ("holiday_fixed_rate", Decimal),
    ("overtime_coefficient", Decimal),
    ("is_official", bool),
    ("official_salary", Decimal),
)
_TYPES = dict(_FIELDS)
_NAMES = tuple(name for name, _ in _FIELDS)
_SCALE = Decimal("0.01")


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return JSONB()
    return sa.JSON()


def _encode(field: str, value):
    if value is None:
        return None
    kind = _TYPES[field]
    if kind is Decimal:
        return str(Decimal(value).quantize(_SCALE))
    if kind is bool:
        return bool(value)
    if kind is int:
        return int(value)
    return str(value)


def _decode(field: str, raw):
    if raw is None:
        return None
    kind = _TYPES[field]
    if kind is Decimal:
        return Decimal(str(raw)).quantize(_SCALE)
    if kind is bool:
        return bool(raw)
    if kind is int:
        return int(raw)
    return str(raw)


def _changes_table(json_type):
    return sa.table(
        "position_term_changes",
        sa.column("position_id", sa.Integer),
        sa.column("field", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("value", json_type),
        sa.column("created_by_id", sa.Integer),
        sa.column("created_by_name", sa.String),
        sa.column("created_at", sa.DateTime),
    )


def _terms_table():
    columns = [
        sa.column("position_id", sa.Integer),
        sa.column("effective_from", sa.Date),
        sa.column("created_by_id", sa.Integer),
        sa.column("created_by_name", sa.String),
        sa.column("created_at", sa.DateTime),
    ]
    types = {
        str: sa.String, Decimal: sa.Numeric, int: sa.Integer, bool: sa.Boolean,
    }
    columns += [sa.column(name, types[kind]) for name, kind in _FIELDS]
    return sa.table("position_terms", *columns)


def upgrade() -> None:
    json_type = _json_type()
    op.create_table(
        "position_term_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id", sa.Integer(),
            sa.ForeignKey("employee_positions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("value", json_type, nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "position_id", "field", "effective_from", name="uq_term_change_field_date",
        ),
    )
    op.create_index(
        "ix_position_term_changes_position_id", "position_term_changes", ["position_id"]
    )

    bind = op.get_bind()
    versions = bind.execute(
        sa.text(
            "SELECT position_id, effective_from, created_by_id, created_by_name, "
            f"created_at, {', '.join(_NAMES)} "
            "FROM position_terms ORDER BY position_id, effective_from, id"
        )
    ).mappings().all()

    rows: list[dict] = []
    previous: dict[str, object] = {}
    current_position = None
    for v in versions:
        if v["position_id"] != current_position:
            current_position, previous = v["position_id"], {}
        for name in _NAMES:
            value = v[name]
            first = name not in previous
            if first or previous[name] != value:
                rows.append({
                    "position_id": v["position_id"],
                    "field": name,
                    "effective_from": v["effective_from"],
                    "value": _encode(name, value),
                    "created_by_id": v["created_by_id"],
                    "created_by_name": v["created_by_name"],
                    "created_at": v["created_at"],
                })
            previous[name] = value
    if rows:
        op.bulk_insert(_changes_table(json_type), rows)


def downgrade() -> None:
    # Снимки собираются заново из изменений: правки, сделанные уже в новой
    # модели, иначе потерялись бы (в `position_terms` их нет — она заморожена).
    bind = op.get_bind()
    changes = bind.execute(
        sa.text(
            "SELECT position_id, field, effective_from, value, created_by_id, "
            "created_by_name, created_at FROM position_term_changes "
            "ORDER BY position_id, effective_from, id"
        )
    ).mappings().all()

    op.execute("DELETE FROM position_terms")
    rows: list[dict] = []
    current_position = None
    values: dict[str, object] = {}
    pending: dict | None = None

    def flush():
        if pending is not None:
            rows.append({**pending, **values})

    for c in changes:
        if c["position_id"] != current_position:
            flush()
            current_position, values, pending = c["position_id"], {}, None
        if pending is not None and pending["effective_from"] != c["effective_from"]:
            flush()
            pending = None
        if pending is None:
            pending = {
                "position_id": c["position_id"],
                "effective_from": c["effective_from"],
                "created_by_id": c["created_by_id"],
                "created_by_name": c["created_by_name"],
                "created_at": c["created_at"],
            }
        values[c["field"]] = _decode(c["field"], c["value"])
    flush()

    if rows:
        # Снимок несёт ВСЕ поля: у полей, не менявшихся к этой дате, значение
        # предыдущее — оно уже накоплено в `values`.
        for row in rows:
            for name in _NAMES:
                row.setdefault(name, None)
        op.bulk_insert(_terms_table(), rows)

    op.drop_index("ix_position_term_changes_position_id", table_name="position_term_changes")
    op.drop_table("position_term_changes")
