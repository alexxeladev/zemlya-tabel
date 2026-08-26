from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.departments import Department


class DepartmentApplication(Base):
    """
    Число заявок на подбор, отработанных отделом для юрлица за месяц
    (task_hr_applications).

    Отдел с флагом `Department.uses_applications_distribution` (HR) распределяет
    зарплату СВОИХ сотрудников по этим заявкам: процент компании = её заявки /
    сумма заявок месяца. Заявки заводятся заново каждый месяц, поэтому ключ —
    (отдел, компания, год, месяц), а не «настройка отдела».

    Хранятся ДВЕ части — «в работе» и «закрытые», как в исходном файле HR;
    общее число заявок (`count`) — их сумма и считается, а не хранится: два
    источника одного числа рано или поздно разойдутся. Распределение считается
    от ОБЩЕГО числа, обе части в нём равноправны.

    Строки существуют ТОЛЬКО там, где заявки введены: пустой набор за месяц
    означает «заявок нет» → отдел уходит на обычный каскад распределения.
    """

    __tablename__ = "department_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    # Заявки в работе и закрытые за месяц — целые неотрицательные. Строка, где
    # обе части нулевые, не пишется (иначе «нет заявок» и «0 заявок» стали бы
    # разными состояниями).
    in_progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    closed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    @property
    def count(self) -> int:
        """Всего заявок = в работе + закрытые. База распределения."""
        return (self.in_progress or 0) + (self.closed or 0)

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
            name="uq_department_application_period",
        ),
    )
