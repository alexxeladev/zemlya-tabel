from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.departments import Department


class DepartmentQuantity(Base):
    """
    Значение КОЛИЧЕСТВЕННОГО ПОКАЗАТЕЛЯ отдела по юрлицу за месяц
    (task_hr_applications → обобщено в task_it_arm_distribution).

    Отдел с флагом `Department.uses_quantity_distribution` распределяет зарплату
    СВОИХ сотрудников не обычным каскадом, а по этому показателю:

        процент компании = её количество / сумма количеств месяца

    Что именно считается, задаётся на отделе (`quantity_metric_name`): у HR это
    заявки на подбор, у ИТ — число АРМ (рабочих мест). Логика расчёта одна на
    всех, отличается только подпись. Значения заводятся заново каждый месяц,
    поэтому ключ — (отдел, компания, год, месяц), а не «настройка отдела».

    Показатель может состоять из ДВУХ ЧАСТЕЙ (`part1`/`part2`) с подписями из
    карточки отдела: у HR это «в работе» и «закрытые», как в исходном файле.
    Показатель без разбивки (АРМ) заполняет только `part1`, `part2` остаётся 0.
    Общее количество (`count`) — их сумма и СЧИТАЕТСЯ, а не хранится: два
    источника одного числа рано или поздно разойдутся. Распределение считается
    от общего количества, обе части в нём равноправны.

    Строки существуют ТОЛЬКО там, где количество введено: пустой набор за месяц
    означает «показатель не задан» → отдел уходит на обычный каскад.
    """

    __tablename__ = "department_quantities"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    # Две части показателя — целые неотрицательные. Строка, где обе части
    # нулевые, не пишется (иначе «не задано» и «0» стали бы разными
    # состояниями). У показателя без разбивки заполнена только первая.
    part1: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    part2: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    @property
    def count(self) -> int:
        """Всего = часть 1 + часть 2. База распределения."""
        return (self.part1 or 0) + (self.part2 or 0)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    department: Mapped[Department] = relationship("Department", foreign_keys=[department_id])
    company: Mapped[Company] = relationship("Company", foreign_keys=[company_id])

    __table_args__ = (
        UniqueConstraint(
            "department_id", "company_id", "year", "month",
            name="uq_department_quantity_period",
        ),
    )
