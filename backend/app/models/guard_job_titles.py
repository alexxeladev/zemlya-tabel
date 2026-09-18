"""Справочник должностей охраны (модуль «Вахта»).

Раньше четыре должности — Охранник / ГБР / Диспетчер / Начальник охраны — были
зашиты кортежем в коде, и завести «Старшего смены» или «Оператора
видеонаблюдения» было нельзя вовсе. Теперь должность — строка справочника,
который ведёт вахта в своих настройках.

От должности в расчёте зависит РОВНО ОДНО — способ оплаты:

* `per_shift` — ставка строки это цена одной смены (охранник, ГБР, диспетчер);
* `salary`    — ставка строки это оклад за месяц, половина за полмесяца,
  пропорционально отмеченным дням (начальник охраны).

Третьего способа в расчёте вахты нет. Понадобится — это новая ветка
`guard_payroll._half_salary`, а не новая строка справочника.

Две должности помечены «по умолчанию»: одна для поста объекта, одна для экипажа
ГБР — с ними встаёт на место человек, которому должность не выбрали явно.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

GUARD_PAY_PER_SHIFT = "per_shift"
GUARD_PAY_SALARY = "salary"
GUARD_PAY_TYPES: tuple[str, ...] = (GUARD_PAY_PER_SHIFT, GUARD_PAY_SALARY)

GUARD_PAY_TYPE_LABELS: dict[str, str] = {
    GUARD_PAY_PER_SHIFT: "Ставка за смену",
    GUARD_PAY_SALARY: "Оклад за месяц",
}

#: Должности «из коробки»: (название, способ оплаты, по умолчанию для поста, для
#: экипажа). Ими заполняет таблицу миграция и тестовая фикстура; в рабочей базе
#: список дальше ведут пользователи.
DEFAULT_GUARD_JOB_TITLES: tuple[tuple[str, str, bool, bool], ...] = (
    ("Охранник", GUARD_PAY_PER_SHIFT, True, False),
    ("ГБР", GUARD_PAY_PER_SHIFT, False, True),
    ("Диспетчер", GUARD_PAY_PER_SHIFT, False, False),
    ("Начальник охраны", GUARD_PAY_SALARY, False, False),
)


class GuardJobTitle(Base):
    __tablename__ = "guard_job_titles"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Название — как его видят в табеле, ведомости и Excel. Уникально без учёта
    #: регистра (держит сервис): по названию должность рабочего места узнаётся
    #: из `EmployeePosition.title`.
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    pay_type: Mapped[str] = mapped_column(
        String(20), default=GUARD_PAY_PER_SHIFT, server_default=GUARD_PAY_PER_SHIFT,
        nullable=False,
    )
    #: С этой должностью человек встаёт на ПОСТ ОБЪЕКТА / в ЭКИПАЖ ГБР, если её
    #: не выбрали явно. Ровно одна на каждый вид места (держит сервис).
    default_for_post: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    default_for_crew: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    #: Снятая должность из выбора пропадает, но остаётся в истории: строки
    #: прошлых месяцев на неё ссылаются.
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_at: Mapped[str] = mapped_column(server_default=func.now())

    @property
    def is_per_shift(self) -> bool:
        return self.pay_type == GUARD_PAY_PER_SHIFT

    @property
    def pay_type_label(self) -> str:
        return GUARD_PAY_TYPE_LABELS.get(self.pay_type, self.pay_type)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<GuardJobTitle {self.id} {self.name!r} {self.pay_type}>"
