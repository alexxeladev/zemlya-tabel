"""Кэш помесячных итогов дашборда (task_perf, п.6.2 плана).

Дашборд считал зарплату ВСЕЙ компании заново при каждом открытии — на 300
сотрудниках это ~2600 вызовов `calculate_position_payroll` (133 тыс.
`is_planned_work_day`) на месяц, 6 месяцев динамики, итого 4–5 с чистого
Python. Запросами это не ускорить: расчёт, а не база.

Две таблицы:

* `dashboard_month_cache` — по одной строке на (год, месяц): готовые итоги
  по КАЖДОМУ рабочему месту (JSON), из которых дашборд складывает часы, ФОТ,
  разрез по отделам и юрлицам. Видимость актора (менеджер видит свои отделы)
  применяется ПОВЕРХ кэша при чтении: строка хранит все места, фильтр дешёв,
  расчёт — нет.
* `data_versions` — счётчики изменений. Ключ `month:YYYY-MM` растёт при любой
  записи, меняющей цифры этого месяца (часы, отсутствия, ночные, премии, заём,
  доли, вахта, показатели, периоды); ключ `reference` — при правке справочных
  данных, влияющих на все месяцы (ставки и графики позиций, календарь,
  отделы, юрлица, вахта). Кэш месяца действителен, пока совпадают ОБА.

Счётчики двигает слушатель `after_flush` (`services/dashboard_cache.py`), а не
вызовы из обработчиков — иначе первый же новый эндпойнт забыл бы, и руководство
увидело бы вчерашние цифры. Тот же принцип, что у журнала справочников.
"""
from __future__ import annotations

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.schedules import _JSONB


class DataVersion(Base):
    __tablename__ = "data_versions"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class DashboardMonthCache(Base):
    __tablename__ = "dashboard_month_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Версии данных, при которых посчитано: месяца и справочника.
    month_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Список итогов по рабочим местам — см. `dashboard_cache.serialize_results`.
    rows: Mapped[list] = mapped_column(_JSONB, nullable=False)
    computed_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("year", "month", name="uq_dashboard_month_cache"),)
