"""Единый экран «Оргструктура» (task_org_structure ч.3).

Только чтение дерева. Создание компаний/отделов и назначение менеджеров идут
через существующие роутеры /api/companies и /api/departments — дерево не
дублирует CRUD, чтобы права и audit log остались в одном месте.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import require_role
from app.database import get_db
from app.models.employees import Employee
from app.schemas.org import OrgTreeRead
from app.services.org_structure import build_org_tree

router = APIRouter()

_admin_only = require_role("admin")


@router.get("/tree", response_model=OrgTreeRead)
def get_org_tree(
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    actor: Employee = Depends(_admin_only),
):
    """Дерево Компания → Отдел → Сотрудники. Структуру и права меняет только
    admin, поэтому и смотрит дерево только он."""
    return build_org_tree(db, include_inactive=include_inactive)
