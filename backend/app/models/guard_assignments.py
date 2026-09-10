"""
Табель вахты: кто на каком посту стоял и в какие дни (task_vahta).

Строка табеля = НАЗНАЧЕНИЕ (`GuardAssignment`): месяц + МЕСТО РАБОТЫ + рабочее
место человека. Человека может не быть вовсе — это «пустой слот», незанятая
позиция со ставкой и нулём смен (в образце заказчика такие строки есть, падать
на них нельзя).

**Мест работы два вида, и ровно одно из них задано у строки:**

* `post_id` — стационарный ПОСТ объекта (охранник на КПП). Проценты
  распределения берутся от ОБЪЕКТА этого поста;
* `crew_id` — выездной ЭКИПАЖ ГБР. Проценты берутся от САМОГО ЭКИПАЖА, и они
  другие: у объектов в образце 100 % на одно юрлицо, у экипажей 35/60/5.

Их не свести в одно поле: экипаж на объекте не стоит, он на него выезжает.
Придумывать экипажу фиктивный «пост» ради единообразия — ровно та ошибка, из-за
которой модуль и переделан.

Замена на посту не правит строку, а создаёт ВТОРУЮ на тот же пост: дни с
выбранного числа уходят сменщику, прежний остаётся со своими. Поэтому
уникальности по (месяц, пост) здесь нет и быть не может.

Месяц ведётся целиком, но внутри у него ДВЕ РАСЧЁТНЫЕ ПОЛОВИНЫ (1–15 и
16–конец): выплата дважды, поэтому премия, штраф и официальная выплата свои у
каждой половины. Отдельной таблицы под них нет — половин ровно две и больше не
станет, а join ради шести чисел ничего не улучшает.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.guard_posts import (
    GUARD_KIND_GUARD,
    GUARD_KIND_LABELS,
    PER_SHIFT_GUARD_KINDS,
)

if TYPE_CHECKING:
    from app.models.employees import Employee
    from app.models.guard_posts import GuardCrew, GuardPost
    from app.models.positions import EmployeePosition


#: Номера расчётных половин месяца. Первая — дни 1–15, вторая — 16 и до конца
#: месяца (то есть вторая длиннее на 13–16 дней; это не ошибка, так в образце).
HALF_FIRST = 1
HALF_SECOND = 2
GUARD_HALVES: tuple[int, ...] = (HALF_FIRST, HALF_SECOND)

#: Последний день первой половины. Граница фиксированная, из образца заказчика.
FIRST_HALF_LAST_DAY = 15


def half_of_day(day: int) -> int:
    """Номер расчётной половины, в которую попадает число месяца."""
    return HALF_FIRST if day <= FIRST_HALF_LAST_DAY else HALF_SECOND


class GuardAssignment(Base):
    """Строка табеля вахты: человек (или пустой слот) на посту в месяце."""

    __tablename__ = "guard_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # Место работы: ЛИБО пост объекта, ЛИБО выездной экипаж ГБР — строго одно
    # из двух (см. CheckConstraint ниже и шапку модуля).
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("guard_posts.id"), index=True, nullable=True
    )
    crew_id: Mapped[int | None] = mapped_column(
        ForeignKey("guard_crews.id"), index=True, nullable=True
    )
    # Рабочее место человека. NULL — ПУСТОЙ СЛОТ: пост есть, ставка есть, а
    # человека на нём нет. Такая строка считается по нулям и в ведомость не идёт.
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("employee_positions.id"), index=True, nullable=True
    )

    #: ДОЛЖНОСТЬ этой строки: Охранник / ГБР / Диспетчер / Начальник охраны.
    #: Принадлежит человеку, а не месту — в табеле заказчика это колонка рядом с
    #: ФИО. На «КП Олимп» на одном посту стоят двое ГБР и трое охранников.
    #: Из должности же следует способ оплаты: начальник охраны получает
    #: фикс-оклад, остальные — посменно.
    kind: Mapped[str] = mapped_column(
        String(20), default=GUARD_KIND_GUARD, server_default=GUARD_KIND_GUARD,
        nullable=False,
    )

    #: Ставка ЭТОЙ строки. По умолчанию берётся от поста, но правится: в образце
    #: у двух ГБР одного экипажа ставки 5000 и 4500, а Караулов стоит на двух
    #: постах со ставками 5000 и 3500.
    rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )

    #: «Трудоустройство» в образце — отметка «Официальный».
    is_official: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # ── Деньги по расчётным половинам ─────────────────────────────────────────
    # Премия — ручная, произвольная; ею же добивают сумму до круглой (поэтому в
    # вахте ничего не округляется). Штраф — простое удержание без обоснования,
    # он УМЕНЬШАЕТ «итого начислено». Официальная выплата — часть, выданная
    # через банк; остаток идёт из кассы и показывается как «к выплате».
    premium_h1: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    premium_h2: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    penalty_h1: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    penalty_h2: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    official_payout_h1: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    official_payout_h2: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )

    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    post: Mapped[Optional[GuardPost]] = relationship("GuardPost")
    crew: Mapped[Optional[GuardCrew]] = relationship("GuardCrew")
    position: Mapped[Optional[EmployeePosition]] = relationship("EmployeePosition")
    shifts: Mapped[list[GuardShift]] = relationship(
        "GuardShift",
        back_populates="assignment",
        cascade="all, delete-orphan",
        order_by="GuardShift.work_date",
    )

    __table_args__ = (
        # Ровно одно место работы. Без этого правила в базе завелись бы строки
        # «нигде» и строки «и там, и там», а от места работы зависит, откуда
        # брать проценты распределения.
        CheckConstraint(
            "(post_id IS NULL) <> (crew_id IS NULL)",
            name="ck_guard_assignment_place",
        ),
    )

    @property
    def employee(self) -> Employee | None:
        """Человек строки; у пустого слота — None."""
        return self.position.employee if self.position else None

    @property
    def place(self):
        """Место работы строки: пост объекта либо экипаж ГБР."""
        return self.post if self.post_id is not None else self.crew

    @property
    def kind_label(self) -> str:
        """Подпись должности для UI и выгрузок."""
        return GUARD_KIND_LABELS.get(self.kind, GUARD_KIND_LABELS[GUARD_KIND_GUARD])

    @property
    def is_per_shift(self) -> bool:
        """Ставка строки — цена смены, а не месячный оклад начальника."""
        return self.kind in PER_SHIFT_GUARD_KINDS

    @property
    def place_name(self) -> str:
        """Как называется место работы — колонка «КП» в выгрузке."""
        place = self.place
        return place.name if place is not None else ""

    @property
    def department_id(self) -> int | None:
        """Отдел строки: у поста — отдел его объекта, у экипажа — свой."""
        if self.post is not None:
            return self.post.department_id
        return self.crew.department_id if self.crew is not None else None

    def premium(self, half: int) -> Decimal:
        return self.premium_h1 if half == HALF_FIRST else self.premium_h2

    def penalty(self, half: int) -> Decimal:
        return self.penalty_h1 if half == HALF_FIRST else self.penalty_h2

    def official_payout(self, half: int) -> Decimal:
        return (
            self.official_payout_h1 if half == HALF_FIRST else self.official_payout_h2
        )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<GuardAssignment {self.id} {self.year}-{self.month:02d} "
            f"post={self.post_id} pos={self.position_id}>"
        )


class GuardShift(Base):
    """Отметка выхода: наличие строки = в этот день человек стоял на посту.

    Часов у смены нет — смена равна суткам (отсюда 24 часа факта на смену в
    ведомости). Как и в основном табеле, «ноль» не хранится: сняли день —
    строка удаляется.
    """

    __tablename__ = "guard_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("guard_assignments.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    work_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    created_at: Mapped[str] = mapped_column(server_default=func.now())

    assignment: Mapped[GuardAssignment] = relationship(
        "GuardAssignment", back_populates="shifts"
    )

    __table_args__ = (
        UniqueConstraint("assignment_id", "work_date", name="uq_guard_shift_day"),
    )
