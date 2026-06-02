"""merge users into employees

Revision ID: a1b2c3d4e5f6
Revises: 59a2b3cf4826
Create Date: 2026-06-02 16:10:00.000000

BACKUP BEFORE RUNNING:
  docker exec -t $(docker ps -q -f ancestor=postgres:16) \\
    pg_dump -U tabel tabel > /tmp/backup_$(date +%s).sql
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "59a2b3cf4826"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Step 1: Add auth fields to employees ──────────────────────────────
    op.add_column("employees", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("employees", sa.Column("hashed_password", sa.String(255), nullable=True))
    op.add_column("employees", sa.Column("role", sa.String(50), nullable=True))
    op.add_column("employees", sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("employees", sa.Column("last_login_at", sa.DateTime(), nullable=True))
    op.add_column("employees", sa.Column("is_system_admin", sa.Boolean(), server_default="false", nullable=False))

    op.create_index("ix_employees_email", "employees", ["email"], unique=True)

    # ── Step 2: Make structure/finance fields nullable ────────────────────
    op.alter_column("employees", "department_id", nullable=True)
    op.alter_column("employees", "schedule_id", nullable=True)
    op.alter_column("employees", "default_company_id", nullable=True)
    op.alter_column("employees", "rate", nullable=True)

    # ── Step 3: Copy auth fields from users → employees (linked via employee_id) ──
    op.execute("""
        UPDATE employees e
        SET
            email             = u.email,
            hashed_password   = u.hashed_password,
            role              = u.role::text,
            must_change_password = u.must_change_password,
            last_login_at     = u.last_login_at,
            is_system_admin   = false
        FROM users u
        WHERE u.employee_id = e.id
    """)

    # ── Step 4: Create new employee records for orphan users (no employee_id) ──
    # Use a CTE with RETURNING to capture new IDs for the mapping
    op.execute("""
        CREATE TEMP TABLE _user_emp_map (user_id INTEGER, employee_id INTEGER)
    """)

    # Store mapping for users that already had employee_id
    op.execute("""
        INSERT INTO _user_emp_map (user_id, employee_id)
        SELECT id, employee_id FROM users WHERE employee_id IS NOT NULL
    """)

    # Insert orphan users as new employees and store mapping
    op.execute("""
        WITH ins AS (
            INSERT INTO employees (full_name, email, hashed_password, role,
                                   must_change_password, last_login_at,
                                   is_system_admin, is_active)
            SELECT
                full_name,
                email,
                hashed_password,
                role::text,
                must_change_password,
                last_login_at,
                (role::text = 'admin'),
                is_active
            FROM users
            WHERE employee_id IS NULL
            RETURNING id, email
        )
        INSERT INTO _user_emp_map (user_id, employee_id)
        SELECT u.id, ins.id
        FROM users u
        JOIN ins ON ins.email = u.email
        WHERE u.employee_id IS NULL
    """)

    # ── Step 5: Reroute audit_log.actor_id from user IDs → employee IDs ──
    op.execute("""
        UPDATE audit_log al
        SET actor_id = m.employee_id
        FROM _user_emp_map m
        WHERE al.actor_id = m.user_id
    """)

    # ── Step 6: Swap FK on audit_log ──────────────────────────────────────
    op.drop_constraint("audit_log_actor_id_fkey", "audit_log", type_="foreignkey")
    op.create_foreign_key(
        "audit_log_actor_id_fkey", "audit_log", "employees", ["actor_id"], ["id"]
    )

    # ── Step 7: Drop users table & orphaned enum type ─────────────────────
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS userrole")


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade not supported: users table has been merged into employees. "
        "Restore from backup if needed."
    )
