"""
Справочник модуля «Вахта»: объекты, посты и экипажи ГБР (task_vahta).

Структура повторяет то, как думает руководитель охраны, и разметку колонки A в
табеле заказчика (там шесть зон):

    ЗОНА ОБСЛУЖИВАНИЯ — географически связанная группа объектов
      ├─ ЭКИПАЖ ГБР зоны (может не быть)
      │     ├─ своя ставка и своё распределение (в образце 35/60/5)
      │     └─ свой состав: люди стоят в экипаже, а не на посту
      └─ ОБЪЕКТЫ (посёлок, площадка) — то, что охраняем
            ├─ ставка за смену по умолчанию
            ├─ РАСПРЕДЕЛЕНИЕ ПО ЮРЛИЦАМ — источник % для его постов
            └─ ПОСТЫ — точки внутри объекта (GW 1, GW 2, КПП, обход)
                  └─ на посту стоят люди

**Экипаж принадлежит ОДНОЙ зоне и за её пределы не выезжает** — зоны разнесены
географически; в зоне при этом экипажей может быть сколько угодно. Отсюда
`GuardCrew.zone_id` обычной ссылкой, а не связь «многие ко многим»: последняя
позволила бы одному экипажу обслуживать две зоны, чего не бывает.

**Отдел охраны задаётся у ЗОНЫ**, а объект и экипаж берут его оттуда. Пока
отдел был на каждом из них, приходилось сверять, что объект и его экипаж из
одного подразделения; теперь такого расхождения просто не бывает.

**Почему у объекта и экипажа проценты РАЗНЫЕ, а у постов своих нет.** В табеле
заказчика внутри каждого объекта расклад по юрлицам одинаковый у всех людей
(Green Wood — Эксплуатация 100 %, РЖД Архив — Секьюрити 100 %), а у выездных
экипажей свой мультикомпанийный (35/60/5). Значит % — свойство объекта и
экипажа, но не поста: на посту меняются только должность и ставка.

**Должность живёт на ПОСТУ.** «КП Олимп» из образца — это один объект с двумя
постами: «ГБР» (двое по 4 500) и «Охрана» (трое по 3 000). Разные ставки внутри
объекта решаются переопределением ставки на посту, расклад у них общий — от
объекта.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.companies import Company
    from app.models.departments import Department


# ── Должность ─────────────────────────────────────────────────────────────────
# Должность принадлежит СТРОКЕ ТАБЕЛЯ (человеку на месте), а не посту: «GW 1» —
# это физическая точка, она не «Охранник». В табеле заказчика «Должность» и
# стоит колонкой рядом с ФИО.
#
# Пока она висела на посту, модель заставляла ВЫДУМЫВАТЬ данные: на «КП Олимп»,
# где стоят двое ГБР и трое охранников, приходилось заводить два несуществующих
# поста «КП Олимп · ГБР» и «КП Олимп · Охранник». В жизни это один объект.
#
# Охранник, ГБР и диспетчер считаются ОДИНАКОВО (смены × ставка) и различаются
# только подписью и величиной ставки. Настоящих способов расчёта два: посменно
# и фикс-оклад начальника — он и выводится из должности.
GUARD_KIND_GUARD = "guard"            # Охранник
GUARD_KIND_GBR = "gbr"                # ГБР
GUARD_KIND_DISPATCHER = "dispatcher"  # Диспетчер
GUARD_KIND_CHIEF = "chief"            # Начальник охраны: ставка — ОКЛАД ЗА МЕСЯЦ

GUARD_KINDS: tuple[str, ...] = (
    GUARD_KIND_GUARD, GUARD_KIND_GBR, GUARD_KIND_DISPATCHER, GUARD_KIND_CHIEF,
)

GUARD_KIND_LABELS: dict[str, str] = {
    GUARD_KIND_GUARD: "Охранник",
    GUARD_KIND_GBR: "ГБР",
    GUARD_KIND_DISPATCHER: "Диспетчер",
    GUARD_KIND_CHIEF: "Начальник охраны",
}

#: Должности, у которых ставка — цена ОДНОЙ СМЕНЫ (а не месячный оклад).
PER_SHIFT_GUARD_KINDS: tuple[str, ...] = (
    GUARD_KIND_GUARD, GUARD_KIND_GBR, GUARD_KIND_DISPATCHER,
)

_ZERO = Decimal("0")


class GuardZone(Base):
    """Зона обслуживания: географически связанная группа объектов.

    Верхний уровень группировки табеля. У зоны свои экипажи ГБР (их может не
    быть вовсе — в образце зоны 4–6 без экипажа) и свои объекты.
    """

    __tablename__ = "guard_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Отдел охраны. Через него работают ПРАВА: менеджер ведёт раздел, если отдел
    # есть в его `managed_departments`. Единственное место, где отдел задаётся.
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), index=True, nullable=False
    )

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    department: Mapped[Department] = relationship("Department")
    sites: Mapped[list[GuardSite]] = relationship(
        "GuardSite", back_populates="zone",
        order_by="GuardSite.sort_order, GuardSite.id",
    )
    crews: Mapped[list[GuardCrew]] = relationship(
        "GuardCrew", back_populates="zone",
        order_by="GuardCrew.sort_order, GuardCrew.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<GuardZone {self.id} {self.name!r}>"


class GuardCrew(Base):
    """Выездной экипаж ГБР зоны обслуживания.

    Люди стоят В ЭКИПАЖЕ, а не на посту: экипаж на объекте не находится, он на
    него выезжает. Поэтому у экипажа свои ставка и распределение, а не объекта.
    """

    __tablename__ = "guard_crews"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Зона экипажа. За её пределы он не выезжает (зоны разнесены
    #: географически), но в самой зоне экипажей может быть несколько.
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("guard_zones.id"), index=True, nullable=False
    )

    #: Ставка за смену экипажа по умолчанию; в строке переопределяется (в
    #: образце у двух ГБР одного экипажа 5 000 и 4 750).
    shift_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=_ZERO, server_default="0", nullable=False
    )

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    zone: Mapped[GuardZone] = relationship("GuardZone", back_populates="crews")
    shares: Mapped[list[GuardCrewShare]] = relationship(
        "GuardCrewShare", back_populates="crew", cascade="all, delete-orphan"
    )

    @property
    def department_id(self) -> int | None:
        """Отдел экипажа — отдел его зоны (права проверяются по нему)."""
        return self.zone.department_id if self.zone else None

    #: Экипаж ГБР и есть ГБР — это должность его людей по умолчанию, когда их
    #: ставят в экипаж. Саму должность хранит строка табеля.
    default_kind = GUARD_KIND_GBR

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<GuardCrew {self.id} {self.name!r}>"


class GuardCrewShare(Base):
    """Распределение затрат ЭКИПАЖА по юрлицам (сумма ≈100 %)."""

    __tablename__ = "guard_crew_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    crew_id: Mapped[int] = mapped_column(
        ForeignKey("guard_crews.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    crew: Mapped[GuardCrew] = relationship("GuardCrew", back_populates="shares")
    company: Mapped[Company] = relationship("Company")

    __table_args__ = (
        UniqueConstraint("crew_id", "company_id", name="uq_guard_crew_share"),
    )


class GuardSite(Base):
    """Охраняемый объект: посёлок, площадка, здание.

    Несёт ставку по умолчанию и РАСПРЕДЕЛЕНИЕ ПО ЮРЛИЦАМ для всех своих постов.
    Живёт внутри зоны обслуживания; ГБР у объекта не свой, а зонный.
    """

    __tablename__ = "guard_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("guard_zones.id"), index=True, nullable=False
    )

    #: Ставка за смену по умолчанию для постов объекта. Пост может переопределить
    #: (на «КП Олимп» ГБР стоит 4 500 при 3 000 у охраны), строка — тоже.
    shift_rate: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=_ZERO, server_default="0", nullable=False
    )

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    zone: Mapped[GuardZone] = relationship("GuardZone", back_populates="sites")
    posts: Mapped[list[GuardPost]] = relationship(
        "GuardPost",
        back_populates="site",
        order_by="GuardPost.sort_order, GuardPost.id",
    )
    shares: Mapped[list[GuardSiteShare]] = relationship(
        "GuardSiteShare", back_populates="site", cascade="all, delete-orphan"
    )

    @property
    def department_id(self) -> int | None:
        """Отдел объекта — отдел его зоны."""
        return self.zone.department_id if self.zone else None

    @property
    def crews(self) -> list[GuardCrew]:
        """ГБР объекта — экипажи его ЗОНЫ, своих у объекта нет."""
        return list(self.zone.crews) if self.zone else []

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<GuardSite {self.id} {self.name!r} zone={self.zone_id}>"


class GuardSiteShare(Base):
    """Распределение затрат ОБЪЕКТА по юрлицам (сумма ≈100 %).

    Своих процентов у поста нет намеренно: в табеле заказчика внутри объекта
    расклад одинаковый у всех, а два разных расклада на одном месте означали бы
    два разных объекта (так в образце и сделано со «Стройгородком»: он заведён
    дважды — на Эксплуатацию и на Стройдеп).
    """

    __tablename__ = "guard_site_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("guard_sites.id"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    site: Mapped[GuardSite] = relationship("GuardSite", back_populates="shares")
    company: Mapped[Company] = relationship("Company")

    __table_args__ = (
        UniqueConstraint("site_id", "company_id", name="uq_guard_site_share"),
    )


class GuardPost(Base):
    """Пост — точка внутри объекта, которую закрывают сменами.

    Несёт только НАЗВАНИЕ точки («GW 1», «КПП») и, если она дороже остальных,
    свою ставку. Должности у поста НЕТ: её носит человек, а не точка.
    """

    __tablename__ = "guard_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("guard_sites.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Ставка поста. NULL — берётся ставка объекта; заполнено — переопределяет её.
    shift_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    created_at: Mapped[str] = mapped_column(server_default=func.now())
    updated_at: Mapped[str] = mapped_column(server_default=func.now(), onupdate=func.now())

    site: Mapped[GuardSite] = relationship("GuardSite", back_populates="posts")

    #: Должность людей поста по умолчанию. Саму должность хранит строка табеля.
    default_kind = GUARD_KIND_GUARD

    @property
    def effective_rate(self) -> Decimal:
        """Ставка поста: своя, иначе от объекта."""
        if self.shift_rate is not None:
            return Decimal(str(self.shift_rate))
        return Decimal(str(self.site.shift_rate)) if self.site else _ZERO

    @property
    def department_id(self) -> int | None:
        """Отдел поста — отдел зоны его объекта (права проверяются по нему)."""
        return self.site.department_id if self.site else None

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<GuardPost {self.id} {self.name!r} site={self.site_id}>"
