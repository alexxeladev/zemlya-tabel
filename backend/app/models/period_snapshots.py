"""
Снимок расчёта закрытого периода (task_stage3_historicity, часть 2).

При закрытии периода (отдел × месяц) результат расчёта сохраняется целиком, и
закрытая ведомость, её Excel, суммы табеля, дашборд и экран вахты читаются
отсюда, а не пересчитываются из текущих справочников. Переоткрытие снимок
удаляет, повторное закрытие создаёт новый. Истории снимков нет — хранится
снимок последнего закрытия (решение заказчика).

Всё хранится в тех представлениях, в которых это отдают экраны: строки
`/payroll` (`EmployeePayrollRead`), строки ведомости с распределением
(`StatementRow`), строки кэша дашборда, экран вахты по трём режимам. Так чтение
не зависит ни от одного справочника: изменились проценты, график, календарь,
налог — снимок этого не видит.

Единственное место записи и чтения — `services/period_snapshots.py`.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.schedules import _JSONB


class PeriodSnapshot(Base):
    __tablename__ = "period_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("timesheet_periods.id", ondelete="CASCADE"), nullable=False
    )
    #: Копия ключа периода — чтобы искать снимки месяца без join-а.
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Поиск снимков месяца — на каждом открытии ведомости и табеля.
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Позиции снимка — лёгкий список: по нему запрос по ОДНОМУ отделу узнаёт,
    #: что позиция лежит в снимке другого отдела, не загружая его строк.
    position_ids: Mapped[list] = mapped_column(_JSONB, nullable=False)
    #: `EmployeePayrollRead` по каждой позиции отдела (строки `/payroll`).
    payroll_rows: Mapped[list] = mapped_column(_JSONB, nullable=False)
    #: `StatementRow` по каждой позиции — с распределением по юрлицам.
    statement_rows: Mapped[list] = mapped_column(_JSONB, nullable=False)
    #: Строки кэша дашборда (`dashboard_cache.serialize_results`).
    dashboard_rows: Mapped[list] = mapped_column(_JSONB, nullable=False)
    #: {position_id: [версии условий, действовавшие в месяце]}.
    terms_used: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    #: {employee_id: {"position_id": …, "amount": …}} — удержание займа месяца.
    #: Факт для расчёта остатка займа в открытых месяцах (решение заказчика:
    #: удержания закрытых месяцев берутся из снимка).
    loan_facts: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    #: Экран вахты охранного отдела: {"0": месяц, "1": 1-я половина, "2": 2-я}.
    #: Пусто у обычных отделов.
    guard_views: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("period_id", name="uq_period_snapshot_period"),
    )


class GuardTaxRate(Base):
    """Версия ставки налога вахты на официальную часть выплаты.

    Действует с 1-го числа месяца `effective_from` (решение заказчика: ставка
    налога версионируется, начиная с месяца). Первая версия — с начала времён,
    её миграция взяла из `guard_settings`.
    """

    __tablename__ = "guard_tax_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    effective_from: Mapped[datetime.date] = mapped_column(Date, nullable=False, unique=True)
    employer_tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
