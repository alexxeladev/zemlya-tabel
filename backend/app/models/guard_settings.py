"""
Общие настройки модуля «Вахта» (task_vahta_taxes).

Пока здесь одно число — **ставка налоговой нагрузки на официальную часть
выплаты**. У официально устроенных охранников компания несёт налоги сверх
выплаты, и это реальная затрата: она добавляется к базе разнесения по юрлицам
(`база = итого начислено + официальная выплата × ставка`).

**Ставка одна на все месяцы** — так решил заказчик. Истории нет: правка ставки
пересчитывает разнесение и прошлых месяцев (снапшотов расчёта в системе нет, а
статусов периодов у вахты нет вовсе).

Таблица из одной строки. Если строки нет (чистая база тестов, `create_all`
вместо миграции), действует `DEFAULT_EMPLOYER_TAX_PERCENT` — миграция заводит
строку с тем же значением. В расчёт ставка приходит ПАРАМЕТРОМ, числом там её
не зашивают: ставки взносов меняются.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: Ставка по умолчанию, в ПРОЦЕНТАХ. Только для отсутствующей строки настроек и
#: для миграции — расчёт берёт ставку из настроек.
DEFAULT_EMPLOYER_TAX_PERCENT = Decimal("40")


class GuardSettings(Base):
    """Настройки вахты — одна строка на всю систему."""

    __tablename__ = "guard_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: Ставка налоговой нагрузки на официальную выплату, в ПРОЦЕНТАХ (40 = 40 %).
    #: Хранится процентами, потому что процентами её и вводят: доля 0.4 в базе
    #: и 40 на экране — лишний пересчёт, в котором легко ошибиться в 100 раз.
    employer_tax_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=DEFAULT_EMPLOYER_TAX_PERCENT,
        server_default=str(DEFAULT_EMPLOYER_TAX_PERCENT),
        nullable=False,
    )

    updated_at: Mapped[str] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
