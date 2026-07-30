from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Table, UniqueConstraint

from app.database import Base

# Менеджер ↔ отделы, которыми он руководит (task_org_structure ч.2).
#
# Не путать с `Employee.department_id`: это ГДЕ сотрудник работает, а здесь —
# ЧЕМ менеджер руководит. Менеджер может числиться в отделе А, а руководить
# отделами А, Б, В; у отдела может быть несколько менеджеров.
# Связь управляется со стороны отдела (см. PUT /api/departments/{id}/managers).
department_managers = Table(
    "department_managers",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "department_id",
        Integer,
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "employee_id",
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    UniqueConstraint("department_id", "employee_id", name="uq_department_manager"),
)
