from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class QuantityCountInput(BaseModel):
    """Количественный показатель для юрлица за месяц: две части.

    Общее число не передаётся — оно всегда сумма этих двух (см. модель).
    У показателя без разбивки (АРМ) заполняется только `part1`.
    """

    company_id: int
    part1: int = Field(default=0, ge=0)
    part2: int = Field(default=0, ge=0)


class QuantityShareRead(QuantityCountInput):
    """Количество компании + вычисленные общее число и процент распределения."""

    # Всего = часть 1 + часть 2; это и есть база распределения.
    count: int
    percent: Decimal


class DepartmentQuantitiesRead(BaseModel):
    """Количественный показатель отдела за месяц + проценты, по которым делится
    зарплата отдела.

    Отдаётся только для отделов с флагом «распределение по количественному
    показателю»: у остальных показателя нет и вводить его негде.
    """

    department_id: int
    department_name: str | None = None
    # Подпись показателя из карточки отдела: «Заявки» у HR, «АРМ» у ИТ.
    metric_name: str | None = None
    # Подписи частей; обе пусты (`has_parts=False`) → показатель вводится одним
    # числом, и строк «в работе»/«закрытые» в UI нет.
    part1_name: str | None = None
    part2_name: str | None = None
    has_parts: bool = False
    year: int
    month: int
    items: list[QuantityShareRead]
    total_part1: int
    total_part2: int
    total_count: int
    # Показатель за месяц не заведён → отдел временно распределяется по обычному
    # каскаду, и это надо показать: молча посчитать «как у всех» нельзя.
    is_empty: bool


class DepartmentQuantitiesUpdate(BaseModel):
    """Полный набор количеств отдела за месяц: что прислали, то и будет
    (пустой список или все нули — показатель снят)."""

    department_id: int
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    items: list[QuantityCountInput]


class QuantityDistributionRow(BaseModel):
    """Распределение рабочего места по юрлицам — для показа В ТАБЕЛЕ отдела,
    делящегося по количественному показателю.

    Считается на бэке теми же числами, что ведомость: фронт не пересобирает базу
    из кусков расчёта и не может разойтись с /statement.
    """

    employee_id: int
    position_id: int | None = None
    department_id: int | None = None
    # База распределения — «К выплате» строки (округлённая до тысячи,
    # task_it_arm_distribution ч.2), а не «Итого начислено».
    base_amount: Decimal
    # company_id → сумма; сумма значений ровно равна base_amount
    amounts: dict[int, Decimal]
