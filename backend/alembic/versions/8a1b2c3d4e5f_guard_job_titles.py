"""guard job titles: справочник должностей охраны вместо кода kind

Revision ID: 8a1b2c3d4e5f
Revises: 7703219dafc1
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8a1b2c3d4e5f"
down_revision: Union[str, None] = "7703219dafc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Четыре должности, которые раньше были зашиты кодом. Порядок и флаги «по
# умолчанию» — как были в коде: пост → охранник, экипаж ГБР → ГБР.
_TITLES = (
    ("guard", "Охранник", "per_shift", True, False, 1),
    ("gbr", "ГБР", "per_shift", False, True, 2),
    ("dispatcher", "Диспетчер", "per_shift", False, False, 3),
    ("chief", "Начальник охраны", "salary", False, False, 4),
)


def upgrade() -> None:
    op.create_table(
        "guard_job_titles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("pay_type", sa.String(20), nullable=False, server_default="per_shift"),
        sa.Column("default_for_post", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("default_for_crew", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    titles = sa.table(
        "guard_job_titles",
        sa.column("id", sa.Integer), sa.column("name", sa.String),
        sa.column("pay_type", sa.String), sa.column("default_for_post", sa.Boolean),
        sa.column("default_for_crew", sa.Boolean), sa.column("sort_order", sa.Integer),
    )
    conn = op.get_bind()
    ids: dict[str, int] = {}
    for kind, name, pay_type, for_post, for_crew, order in _TITLES:
        result = conn.execute(
            titles.insert().values(
                name=name, pay_type=pay_type, default_for_post=for_post,
                default_for_crew=for_crew, sort_order=order,
            ).returning(titles.c.id)
        )
        ids[kind] = result.scalar_one()

    # Строкам табеля — id должности по прежнему коду. Неизвестный код (руками в
    # базе) считается охранником, как считал его и код.
    op.add_column(
        "guard_assignments",
        sa.Column("job_title_id", sa.Integer(), sa.ForeignKey("guard_job_titles.id"), nullable=True),
    )
    assignments = sa.table(
        "guard_assignments", sa.column("kind", sa.String), sa.column("job_title_id", sa.Integer)
    )
    for kind, title_id in ids.items():
        conn.execute(assignments.update().where(assignments.c.kind == kind).values(job_title_id=title_id))
    conn.execute(
        assignments.update().where(assignments.c.job_title_id.is_(None)).values(job_title_id=ids["guard"])
    )
    op.alter_column("guard_assignments", "job_title_id", nullable=False)
    op.create_index("ix_guard_assignments_job_title_id", "guard_assignments", ["job_title_id"])
    op.drop_column("guard_assignments", "kind")


def downgrade() -> None:
    op.add_column(
        "guard_assignments",
        sa.Column("kind", sa.String(20), nullable=False, server_default="guard"),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text("select id, name from guard_job_titles")).all()
    by_name = {name: title_id for title_id, name in rows}
    for kind, name, *_ in _TITLES:
        title_id = by_name.get(name)
        if title_id is not None:
            conn.execute(
                sa.text("update guard_assignments set kind = :kind where job_title_id = :tid"),
                {"kind": kind, "tid": title_id},
            )
    op.drop_index("ix_guard_assignments_job_title_id", table_name="guard_assignments")
    op.drop_column("guard_assignments", "job_title_id")
    op.drop_table("guard_job_titles")
