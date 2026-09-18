from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Причины неудачного входа — столбец `reason`. Подписи для людей не заводим:
# журнал читает админ запросом, экрана у него пока нет.
REASON_UNKNOWN_EMAIL = "unknown_email"
REASON_WRONG_PASSWORD = "wrong_password"
REASON_NO_ACCESS = "no_access"
REASON_INACTIVE = "inactive"
# Попытка во время блокировки: пишется в журнал, но в порог НЕ считается —
# иначе блокировка продлевалась бы, пока кто-то стучится.
REASON_LOCKED = "locked"


class LoginFailure(Base):
    """Журнал НЕУДАЧНЫХ входов (task_stage2_access п.2.6) и источник счётчика
    блокировки: отдельного «счётчика попыток» в БД нет — он считается по этой
    таблице за окно, как лимит больничного по отметкам.

    Успешные входы сюда не пишутся: точку отсчёта после успеха даёт
    `Employee.last_login_at`, после снятия блокировки админом —
    `Employee.login_unlocked_at`.

    Внешнего ключа на сотрудника нет намеренно (как у `reference_changes`):
    email хранится текстом — попытки на несуществующий адрес тоже журнал.
    """

    __tablename__ = "login_failures"
    __table_args__ = (Index("ix_login_failures_email_created", "email", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Как ввели, в нижнем регистре: счётчик не должен обходиться «Admin@».
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    employee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Naive UTC, проставляется из Python (`services/login_guard._utcnow`), а не
    # server_default: сравнивается с Python-временем, часовой пояс сервера БД
    # в счёт вмешиваться не должен.
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
