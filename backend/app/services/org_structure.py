"""Дерево оргструктуры: Компания → Отдел → Сотрудники (task_org_structure ч.3).

Только чтение и только для навигации. Головная компания отдела группирует узлы
дерева и НИЧЕГО не решает в расчёте ЗП — там компании берутся из часов табеля и
процентов распределения.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, selectinload

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.schemas.org import (
    OrgCompanyRead,
    OrgDepartmentRead,
    OrgEmployeeRead,
    OrgTreeRead,
)


def _employee_read(emp: Employee) -> OrgEmployeeRead:
    return OrgEmployeeRead(
        id=emp.id,
        full_name=emp.full_name,
        tab_number=emp.tab_number,
        position=emp.position,
        role=emp.role,
        is_active=emp.is_active,
    )


def _by_name(items: list[Employee]) -> list[Employee]:
    return sorted(items, key=lambda e: e.full_name)


def build_org_tree(db: Session, include_inactive: bool = False) -> OrgTreeRead:
    """Собрать дерево целиком за несколько запросов (без N+1)."""
    dept_q = db.query(Department).options(selectinload(Department.managers))
    comp_q = db.query(Company)
    emp_q = db.query(Employee).filter(Employee.is_system_admin == False)  # noqa: E712
    if not include_inactive:
        dept_q = dept_q.filter(Department.is_active == True)  # noqa: E712
        comp_q = comp_q.filter(Company.is_active == True)  # noqa: E712
        emp_q = emp_q.filter(Employee.is_active == True)  # noqa: E712

    departments = dept_q.all()
    companies = comp_q.all()
    employees = emp_q.all()

    by_dept: dict[int | None, list[Employee]] = {}
    for emp in employees:
        by_dept.setdefault(emp.department_id, []).append(emp)

    def dept_read(dept: Department) -> OrgDepartmentRead:
        members = _by_name(by_dept.get(dept.id, []))
        return OrgDepartmentRead(
            id=dept.id,
            name=dept.name,
            code=dept.code,
            is_active=dept.is_active,
            head_company_id=dept.head_company_id,
            night_shift_fund=dept.night_shift_fund,
            uses_applications_distribution=dept.uses_applications_distribution,
            managers=[_employee_read(m) for m in _by_name(list(dept.managers))],
            employee_count=len(members),
            employees=[_employee_read(e) for e in members],
        )

    depts_by_company: dict[int | None, list[Department]] = {}
    for dept in departments:
        depts_by_company.setdefault(dept.head_company_id, []).append(dept)

    known_company_ids = {c.id for c in companies}

    return OrgTreeRead(
        companies=[
            OrgCompanyRead(
                id=c.id,
                code=c.code,
                name=c.name,
                inn=c.inn,
                is_active=c.is_active,
                departments=[
                    dept_read(d)
                    for d in sorted(depts_by_company.get(c.id, []), key=lambda d: d.name)
                ],
            )
            for c in sorted(companies, key=lambda c: c.name)
        ],
        departments_without_company=[
            dept_read(d)
            for d in sorted(
                # Отделы без головной компании + те, чья компания скрыта фильтром:
                # потерять узел из-за деактивированного юрлица нельзя.
                [d for d in departments if d.head_company_id not in known_company_ids],
                key=lambda d: d.name,
            )
        ],
        employees_without_department=[
            _employee_read(e) for e in _by_name(by_dept.get(None, []))
        ],
    )
