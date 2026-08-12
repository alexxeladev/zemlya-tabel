from __future__ import annotations

import datetime
import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.department_managers import department_managers
from app.models.positions import (
    EMPLOYEE_COMPAT_FIELDS,
    EMPLOYEE_COMPAT_RELATIONS,
    EmployeePosition,
)

if TYPE_CHECKING:
    from app.models.departments import Department
    from app.models.timesheet_entries import TimesheetEntry


class EmployeeRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    accountant = "accountant"
    # Табельщик (task_timekeeper_role): заполняет время своих отделов, финансов
    # не видит. Отделы — через ту же связь managed_departments, что у manager.
    timekeeper = "timekeeper"
    employee = "employee"


def _position_field(position_attr: str):
    """Compat-аксессор к полю ОСНОВНОЙ позиции (task_positions ч.A).

    Оклад/график/отдел/компания/коэффициенты переехали на позицию, но старый API,
    импорт, CLI и фронт продолжают писать их «в сотрудника». Такой доступ означает
    основную позицию: читаем её, а на запись — создаём, если её ещё нет.
    Источник правды один — позиция; дублирующих колонок на employees больше нет.
    """

    def getter(self) -> object | None:
        pos = self.primary_position
        return getattr(pos, position_attr) if pos is not None else None

    def setter(self, value: object) -> None:
        setattr(self.ensure_primary_position(), position_attr, value)

    return property(getter, setter)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Personal info
    tab_number: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Оклад / график / отдел / компания / коэффициенты ──────────────────────
    # Всё это переехало на ПОЗИЦИЮ (task_positions ч.A): у совместителя каждое
    # рабочее место со своими условиями. Здесь их больше нет — доступ идёт через
    # compat-аксессоры ниже, которые адресуют ОСНОВНУЮ позицию.

    # Займ (задача 3.11a): сумма, срок в месяцах, дата начала погашения.
    # Гасится равными долями (сумма/срок) автоматически; бухгалтер может
    # скорректировать удержание за конкретный месяц (LoanDeduction). Остаток
    # считается на лету = сумма − фактически удержанное (app.services.payout).
    loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loan_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loan_start_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    # К какой позиции относится удержание займа (task_positions ч.A). NULL —
    # к основной: старые займы мигрированы, а расчёт всё равно падает на неё.
    # use_alter — employees и employee_positions ссылаются друг на друга; без
    # него metadata.create_all/drop_all не может отсортировать таблицы.
    loan_position_id: Mapped[int | None] = mapped_column(
        ForeignKey("employee_positions.id", use_alter=True, name="fk_employees_loan_position"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hire_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    dismissal_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    # Auth fields (nullable — only if employee has system access)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    # Relationships
    # Позиции (рабочие места). Ровно одна помечена is_primary. lazy="selectin" —
    # позиция нужна почти на каждом обращении к сотруднику (права, расчёт,
    # compat-аксессоры), ленивая загрузка дала бы N+1.
    positions: Mapped[list[EmployeePosition]] = relationship(
        "EmployeePosition",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EmployeePosition.id",
        foreign_keys="EmployeePosition.employee_id",
    )

    # Отделы, которыми сотрудник РУКОВОДИТ или которые ВЕДЁТ как табельщик
    # (task_org_structure ч.2, task_timekeeper_role). Не путать с `department` —
    # это отдел, где он числится. Заполняется у manager и timekeeper.
    # lazy="selectin" — доступ проверяется на каждом запросе, без этого был бы
    # отдельный SELECT на каждого сотрудника в списке.
    managed_departments: Mapped[list[Department]] = relationship(
        "Department",
        secondary=department_managers,
        back_populates="managers",
        lazy="selectin",
    )

    timesheet_entries: Mapped[list[TimesheetEntry]] = relationship(
        "TimesheetEntry", back_populates="employee", cascade="all, delete-orphan"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Инвариант: у каждого сотрудника есть основная позиция. Аксессоры выше
        # создают её при первом присваивании; для сотрудника без единого
        # «позиционного» поля (например, системный админ) создаём пустую здесь.
        self.ensure_primary_position()

    @property
    def managed_department_ids(self) -> list[int]:
        """Id отделов, которыми руководит сотрудник (см. app.services.org_access)."""
        return sorted(d.id for d in self.managed_departments)

    # ── Позиции ───────────────────────────────────────────────────────────────

    @property
    def primary_position(self) -> EmployeePosition | None:
        """Основная позиция. Если признак почему-то потерян — первая по id,
        чтобы расчёт не обнулился молча."""
        first: EmployeePosition | None = None
        for pos in self.positions:
            if pos.is_primary:
                return pos
            if first is None:
                first = pos
        return first

    @property
    def active_positions(self) -> list[EmployeePosition]:
        """Позиции, по которым сейчас работают: основная всегда первой."""
        active = [p for p in self.positions if p.is_active]
        return sorted(active, key=lambda p: (not p.is_primary, p.sort_order, p.id))

    def ensure_primary_position(self) -> EmployeePosition:
        """Вернуть основную позицию, создав её при отсутствии."""
        pos = self.primary_position
        if pos is None:
            pos = EmployeePosition(is_primary=True, title=self.position)
            self.positions.append(pos)
        return pos

    def position_by_id(self, position_id: int | None) -> EmployeePosition | None:
        """Позиция сотрудника по id; None/чужой id → основная (совместимость со
        строками, заведёнными до появления позиций)."""
        if position_id is not None:
            for pos in self.positions:
                if pos.id == position_id:
                    return pos
        return self.primary_position


# ── Compat-аксессоры к основной позиции (task_positions ч.A) ──────────────────
# Навешиваются после объявления класса, чтобы маппер SQLAlchemy видел только
# настоящие колонки. Старый API/импорт/CLI/тесты продолжают работать с «плоской»
# карточкой сотрудника: emp.rate, emp.schedule_id, emp.department_id и т.д.
for _emp_attr, _pos_attr in {**EMPLOYEE_COMPAT_FIELDS, **EMPLOYEE_COMPAT_RELATIONS}.items():
    setattr(Employee, _emp_attr, _position_field(_pos_attr))
del _emp_attr, _pos_attr
