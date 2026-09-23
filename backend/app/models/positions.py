"""
Позиция сотрудника — «рабочее место» (task_positions ч.A, совместительство).

Сотрудник может занимать несколько должностей: основную + совместительство.
Всё, что раньше жило в карточке сотрудника и влияло на расчёт (оклад/ставка,
график, отдел, компания, коэффициенты, тип оплаты), переехало СЮДА. На человеке
остались ФИО, таб.номер, доступ и деньги «на человека» (премии/KPI/займ/аванс),
которые привязываются к конкретной позиции.

Расчёт ЗП идёт по каждой позиции отдельно (`app.services.payroll`), «к выплате»
между позициями НЕ суммируется — разные компании платят раздельно.

У сотрудника ровно одна позиция помечена `is_primary`. Совместимость со старым
API держится на compat-аксессорах `Employee.rate/schedule_id/...`, которые
читают и пишут основную позицию (см. `app.models.employees`).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.departments import Department
    from app.models.employees import Employee
    from app.models.position_terms import PositionTerms
    from app.models.schedules import Schedule


# ── Типы оплаты позиции ───────────────────────────────────────────────────────
# Тип определяет ТОЛЬКО способ расчёта базовой суммы. Переработка, отсутствия,
# премии, удержания и распределение по юрлицам работают по общим правилам.
PAY_TYPE_SALARY = "salary"        # месячный оклад `rate`, база = оклад × зачётные/норма
PAY_TYPE_PER_SHIFT = "per_shift"  # ставка за смену `shift_rate`, база = смены × ставка
PAY_TYPE_HOURLY = "hourly"        # ставка за час `hour_rate`, база = часы в пределах нормы × ставка

PAY_TYPES: tuple[str, ...] = (PAY_TYPE_SALARY, PAY_TYPE_PER_SHIFT, PAY_TYPE_HOURLY)

# Тип оплаты → поле, где лежит его база. Поля взаимоисключающие: при смене типа
# чужие гасятся, иначе в карточке остаётся мусор от прошлого типа и расчёт молча
# возьмёт не ту базу. Единый источник для роутера сотрудников и CRUD позиций.
PAY_TYPE_BASE_FIELD: dict[str, str] = {
    PAY_TYPE_SALARY: "rate",
    PAY_TYPE_PER_SHIFT: "shift_rate",
    PAY_TYPE_HOURLY: "hour_rate",
}

# Значения, которые в старой модели стояли server_default-ами на employees.
# Дублируются в __init__, иначе до flush-а поля были бы None и compat-аксессоры
# отдавали бы не то, что отдавала колонка с дефолтом.
_INIT_DEFAULTS: dict[str, object] = {
    "pay_type": PAY_TYPE_SALARY,
    "is_primary": False,
    "is_active": True,
    "sort_order": 0,
    "has_night_shifts": False,
    "weekend_pay_type": "coefficient",
    "weekend_coefficient": Decimal("1.5"),
    "holiday_pay_type": "coefficient",
    "holiday_coefficient": Decimal("1.5"),
    "overtime_coefficient": Decimal("1.5"),
}

# Поля позиции, которые видны через compat-аксессоры сотрудника.
# Ключ — имя атрибута на Employee, значение — имя поля на позиции.
EMPLOYEE_COMPAT_FIELDS: dict[str, str] = {
    "department_id": "department_id",
    "schedule_id": "schedule_id",
    "default_company_id": "company_id",
    "pay_type": "pay_type",
    "rate": "rate",
    "shift_rate": "shift_rate",
    "hour_rate": "hour_rate",
    "weekend_pay_type": "weekend_pay_type",
    "weekend_coefficient": "weekend_coefficient",
    "weekend_fixed_rate": "weekend_fixed_rate",
    "holiday_pay_type": "holiday_pay_type",
    "holiday_coefficient": "holiday_coefficient",
    "holiday_fixed_rate": "holiday_fixed_rate",
    "overtime_coefficient": "overtime_coefficient",
    "has_night_shifts": "has_night_shifts",
}

# Relationship-аксессоры: имя на Employee → имя на позиции.
EMPLOYEE_COMPAT_RELATIONS: dict[str, str] = {
    "department": "department",
    "schedule": "schedule",
    "default_company": "company",
}


class EmployeePosition(Base):
    """Одна должность сотрудника со своим окладом/графиком/отделом/компанией."""

    __tablename__ = "employee_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True, nullable=False
    )

    # Название должности. У основной позиции мигрировано из Employee.position.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Ровно одна основная позиция на сотрудника; остальные — совместительство.
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # ── Структура: у каждой позиции свои отдел / график / компания ────────────
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"), index=True, nullable=True
    )
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id"), nullable=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)

    # ── Тип оплаты и база ─────────────────────────────────────────────────────
    # Взаимоисключающие: у окладной задан `rate`, у посменной `shift_rate`,
    # у почасовой `hour_rate`. Чужое поле гасится при смене типа, иначе расчёт
    # молча возьмёт не то.
    pay_type: Mapped[str] = mapped_column(
        String(20), default=PAY_TYPE_SALARY, server_default=PAY_TYPE_SALARY, nullable=False
    )
    rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    shift_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Ставка за ЧАС почасовой позиции. Не путать с `EmployeePayroll.hourly_rate` —
    # там производная величина «оклад / норма часов».
    hour_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # ── Оплата особых категорий часов ─────────────────────────────────────────
    # Вне графика — выход в свой законный выходной по графику позиции.
    weekend_pay_type: Mapped[str] = mapped_column(
        String(20), default="coefficient", server_default="coefficient", nullable=False
    )
    weekend_coefficient: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), default=Decimal("1.5"), server_default="1.5", nullable=True
    )
    weekend_fixed_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Праздничные — работа в нерабочий праздничный день производственного календаря.
    holiday_pay_type: Mapped[str] = mapped_column(
        String(20), default="coefficient", server_default="coefficient", nullable=False
    )
    holiday_coefficient: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), default=Decimal("1.5"), server_default="1.5", nullable=True
    )
    holiday_fixed_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Коэффициент переработки: 0 / 1 / 1.5. None трактуется как 1.5.
    overtime_coefficient: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), default=Decimal("1.5"), server_default="1.5", nullable=True
    )

    # ── Период работы НА ЭТОЙ должности (task_employment_period) ──────────────
    # Границы заполнения табеля, включительно с обеих сторон. Пустая дата —
    # границы нет. Действуют В ПЕРЕСЕЧЕНИИ с датами человека
    # (`Employee.hire_date` / `.dismissal_date`): даты человека — всегда внешняя
    # граница, поэтому увольнение кадровиком закрывает все рабочие места разом и
    # разъехаться уровням нечему. Единственное место, где это считается, —
    # `app.services.employment_period`.
    #
    # Дата увольнения позиции НЕ трогает `is_active`: тот про снятие рабочего
    # места с учёта, и `visible_positions` по нему убирает строку из табеля
    # целиком — вместе с днями, которые человек до увольнения отработал.
    hire_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    dismissal_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    # ── Официальное трудоустройство (вахта, task_guard_form_rate_official) ────
    # Признак и официальная зарплата НА РУКИ (сумма на карту после НДФЛ, не
    # оклад по договору) — свойства РАБОЧЕГО МЕСТА, а не строки табеля месяца:
    # человек устроен официально на этой работе, а не «в августе».
    #
    # Из зарплаты ВЫЧИСЛЯЕТСЯ официальная выплата каждой половины месяца
    # (`guard_duty.official_month_payouts`), от неё — налог 40 % сверху и «к
    # выплате». Руками выплату не вводят: через банк всегда уходит половина
    # зарплаты в каждую половину (решение заказчика). Раньше и флаг, и суммы
    # лежали в строке табеля, были не связаны между собой, и флаг не участвовал
    # в расчёте вовсе.
    #
    # Признак выключен — зарплата не хранится (NULL): два состояния одного
    # факта неминуемо разъехались бы.
    is_official: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    official_salary: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    # ── Ночные смены ──────────────────────────────────────────────────────────
    # Только ФЛАГ: можно ли отмечать этому рабочему месту выходы в ночь.
    # Ставки здесь нет — она вычисляется из фонда ОТДЕЛА (фонд / календарные
    # дни месяца, task_night_shifts_rework), а ручное поле `night_rate` снято
    # миграцией: два источника цены смены неминуемо разошлись бы.
    has_night_shifts: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Должность ОХРАНЫ — ссылка на справочник вахты (`guard_job_titles`); у
    # обычных рабочих мест пусто. Связь по ключу, а не по названию: переименование
    # должности в справочнике штат не ломает. `title` при этом дублирует имя
    # должности — его читают табель, ведомость и Excel, и он единственная
    # «должность» у обычных позиций.
    job_title_id: Mapped[int | None] = mapped_column(
        ForeignKey("guard_job_titles.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    employee: Mapped[Employee] = relationship(
        "Employee", back_populates="positions", foreign_keys=[employee_id]
    )
    job_title = relationship("GuardJobTitle")
    department: Mapped[Optional[Department]] = relationship(
        "Department", back_populates="positions"
    )
    schedule: Mapped[Optional[Schedule]] = relationship(
        "Schedule", back_populates="positions"
    )
    company: Mapped[Optional[Company]] = relationship(
        "Company", back_populates="positions"
    )
    # История условий труда (task_stage3_historicity). Поля условий на самой
    # позиции — зеркало ПОСЛЕДНЕЙ версии; расчёт берёт версию на день
    # (`services.position_terms.terms_on`). selectin: расчёт месяца читает
    # версии каждой позиции, ленивая загрузка дала бы запрос на строку.
    terms_versions: Mapped[list["PositionTerms"]] = relationship(
        "PositionTerms",
        order_by="PositionTerms.effective_from",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_position_employee_primary", "employee_id", "is_primary"),
    )

    def __init__(self, **kwargs):
        # Python-side дефолты: server_default срабатывает только на INSERT, а
        # compat-аксессоры сотрудника читают позицию и ДО flush-а.
        for field, value in _INIT_DEFAULTS.items():
            kwargs.setdefault(field, value)
        super().__init__(**kwargs)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        kind = "основная" if self.is_primary else "совместительство"
        return f"<EmployeePosition {self.id} emp={self.employee_id} {kind} {self.title!r}>"

    @property
    def display_title(self) -> str:
        """Название должности для UI/Excel; пустое — «Основная»/«Совместительство»."""
        if self.title:
            return self.title
        return "Основная" if self.is_primary else "Совместительство"
