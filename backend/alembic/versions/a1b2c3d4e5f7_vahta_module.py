"""Модуль «Вахта»: зоны, объекты, посты, экипажи ГБР, табель (task_vahta)

Структура справочника повторяет разметку табеля заказчика (колонка A — зоны):

    ЗОНА ОБСЛУЖИВАНИЯ (отдел охраны задаётся здесь)
      ├─ ЭКИПАЖ ГБР (своя ставка и своё распределение) → строки табеля напрямую
      └─ ОБЪЕКТ (ставка по умолчанию + распределение по юрлицам)
            └─ ПОСТ (название точки и, если надо, своя ставка) → строки табеля

Должность живёт на СТРОКЕ табеля, а не на посту: точка не бывает «охранником».

Строка табеля привязана ЛИБО к посту, ЛИБО к экипажу — это держит
CheckConstraint: от места работы зависит, откуда берутся проценты.

Автогенерация тянет посторонний «дрейф» (TIMESTAMP → String у колонок с
TypeDecorator, partial-индексы периодов) — здесь оставлено ТОЛЬКО новое.

Флаг отдела по умолчанию ВЫКЛЮЧЕН: после деплоя ни один существующий отдел в
модуль не попадает, пока галочку не поставят руками.

Revision ID: a1b2c3d4e5f7
Revises: e5f6a7b8c9d0
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column(
            "is_guard_department", sa.Boolean(),
            server_default="false", nullable=False,
        ),
    )

    # ── Зоны обслуживания ────────────────────────────────────────────────────
    op.create_table(
        "guard_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guard_zones_department_id", "guard_zones", ["department_id"])

    # ── Выездные экипажи ГБР: экипаж принадлежит ОДНОЙ зоне ──────────────────
    op.create_table(
        "guard_crews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column(
            "shift_rate", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["zone_id"], ["guard_zones.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guard_crews_zone_id", "guard_crews", ["zone_id"])

    op.create_table(
        "guard_crew_shares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("crew_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("percent", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["crew_id"], ["guard_crews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crew_id", "company_id", name="uq_guard_crew_share"),
    )
    op.create_index("ix_guard_crew_shares_crew_id", "guard_crew_shares", ["crew_id"])

    # ── Охраняемые объекты ───────────────────────────────────────────────────
    op.create_table(
        "guard_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column(
            "shift_rate", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["zone_id"], ["guard_zones.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guard_sites_zone_id", "guard_sites", ["zone_id"])

    op.create_table(
        "guard_site_shares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("percent", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["site_id"], ["guard_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "company_id", name="uq_guard_site_share"),
    )
    op.create_index("ix_guard_site_shares_site_id", "guard_site_shares", ["site_id"])

    # ── Посты внутри объектов ────────────────────────────────────────────────
    op.create_table(
        "guard_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        # NULL — ставка берётся у объекта; заполнено — переопределяет её.
        # Должности у поста НЕТ: её носит человек, а не точка (см. модель).
        sa.Column("shift_rate", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["site_id"], ["guard_sites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guard_posts_site_id", "guard_posts", ["site_id"])

    # ── Табель ───────────────────────────────────────────────────────────────
    op.create_table(
        "guard_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("crew_id", sa.Integer(), nullable=True),
        sa.Column("position_id", sa.Integer(), nullable=True),
        # Должность принадлежит СТРОКЕ: на одном посту стоят люди разных
        # должностей с разными ставками (в образце — «КП Олимп»).
        sa.Column("kind", sa.String(length=20), server_default="guard", nullable=False),
        sa.Column(
            "rate", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column("is_official", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "premium_h1", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column(
            "premium_h2", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column(
            "penalty_h1", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column(
            "penalty_h2", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column(
            "official_payout_h1", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column(
            "official_payout_h2", sa.Numeric(precision=12, scale=2),
            server_default="0", nullable=False,
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["crew_id"], ["guard_crews.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["employee_positions.id"]),
        sa.ForeignKeyConstraint(["post_id"], ["guard_posts.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Место работы ровно одно: пост объекта ЛИБО выездной экипаж.
        sa.CheckConstraint(
            "(post_id IS NULL) <> (crew_id IS NULL)",
            name="ck_guard_assignment_place",
        ),
    )
    op.create_index("ix_guard_assignments_post_id", "guard_assignments", ["post_id"])
    op.create_index("ix_guard_assignments_crew_id", "guard_assignments", ["crew_id"])
    op.create_index(
        "ix_guard_assignments_position_id", "guard_assignments", ["position_id"]
    )
    # Табель месяца и врезка в ведомость грузятся «за этот месяц».
    op.create_index(
        "ix_guard_assignment_period", "guard_assignments", ["year", "month"]
    )

    op.create_table(
        "guard_shifts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["guard_assignments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assignment_id", "work_date", name="uq_guard_shift_day"),
    )
    op.create_index("ix_guard_shifts_assignment_id", "guard_shifts", ["assignment_id"])


def downgrade() -> None:
    op.drop_index("ix_guard_shifts_assignment_id", table_name="guard_shifts")
    op.drop_table("guard_shifts")
    op.drop_index("ix_guard_assignment_period", table_name="guard_assignments")
    op.drop_index("ix_guard_assignments_position_id", table_name="guard_assignments")
    op.drop_index("ix_guard_assignments_crew_id", table_name="guard_assignments")
    op.drop_index("ix_guard_assignments_post_id", table_name="guard_assignments")
    op.drop_table("guard_assignments")
    op.drop_index("ix_guard_posts_site_id", table_name="guard_posts")
    op.drop_table("guard_posts")
    op.drop_index("ix_guard_site_shares_site_id", table_name="guard_site_shares")
    op.drop_table("guard_site_shares")
    op.drop_index("ix_guard_sites_zone_id", table_name="guard_sites")
    op.drop_table("guard_sites")
    op.drop_index("ix_guard_crew_shares_crew_id", table_name="guard_crew_shares")
    op.drop_table("guard_crew_shares")
    op.drop_index("ix_guard_crews_zone_id", table_name="guard_crews")
    op.drop_table("guard_crews")
    op.drop_index("ix_guard_zones_department_id", table_name="guard_zones")
    op.drop_table("guard_zones")
    op.drop_column("departments", "is_guard_department")
