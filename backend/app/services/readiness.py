"""«Жив» и «готов принимать запросы» — разные вопросы (этап 4 п.4.2).

Единственное место, где считается готовность. Два ответа, и путать их нельзя:

- **`/health` — ЖИВ**: процесс отвечает на сокет. В базу НЕ ходит, всегда 200.
  По нему перезапускают зависший контейнер; будь он завязан на базу, падение
  базы уводило бы в вечный рестарт живой процесс, который через минуту
  заработал бы сам.
- **`/ready` — ГОТОВ**: база отвечает И схема на последней миграции. По нему
  пускают трафик. Раньше `/health` возвращал константу, то есть «ок» и при
  лежащей базе, и при коде, выкаченном без миграции (аудит: «/health сейчас
  возвращает только константу»).

Почему версия миграций входит в готовность: схема и код едут отдельно. Контейнер
с новым кодом на старой схеме поднимется и будет отвечать 500 на первой же
таблице — по нему надо не пускать трафик, а не выяснять это по жалобам.

Текст ошибки базы наружу НЕ отдаётся: в нём бывает строка подключения с паролем.
Наружу — род поломки, подробности — в лог сервера.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# app/services/readiness.py → app/services → app → корень бэкенда (там alembic.ini)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    """Последняя ревизия в файлах миграций. None — каталог миграций недоступен
    (например, образ собран без него): тогда сверять не с чем."""
    ini = _BACKEND_ROOT / "alembic.ini"
    if not ini.exists():
        return None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(ini))
        # script_location в ini относительный — доводим до абсолютного, иначе
        # он считается от текущего каталога процесса, а не от файла настроек.
        config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception as exc:  # noqa: BLE001 — готовность не должна падать сама
        logger.warning("Не удалось прочитать версию миграций: %s", exc)
        return None


def _applied_revision(db: Session) -> str | None:
    """Ревизия, применённая к базе. None — таблицы alembic_version нет
    (миграции не применялись вовсе)."""
    try:
        return db.execute(text("select version_num from alembic_version")).scalar()
    except SQLAlchemyError:
        # Нет таблицы — это не сбой базы, а «миграции не применены». Транзакцию
        # после неудачного запроса надо откатить, иначе следующий получит
        # «current transaction is aborted».
        db.rollback()
        return None


def readiness_report(db: Session) -> dict:
    """Готов ли экземпляр принимать запросы. Ответ — словарь для `/ready`.

    `ready=False` в четырёх случаях: база не отвечает, миграции не применялись,
    применённая ревизия не совпадает с последней в коде, и версию не с чем
    сверить (рядом нет каталога миграций).
    """
    report: dict = {
        "status": "not_ready",
        "database": "unavailable",
        "migrations": {"state": "unknown", "applied": None, "head": migration_head()},
        "reason": None,
    }

    try:
        db.execute(text("select 1")).scalar()
    except SQLAlchemyError as exc:
        logger.error("Проверка готовности: база недоступна: %s", exc)
        report["reason"] = "База данных недоступна"
        report["database_error"] = type(exc).__name__
        return report

    report["database"] = "ok"

    head = report["migrations"]["head"]
    applied = _applied_revision(db)
    report["migrations"]["applied"] = applied

    if applied is None:
        report["migrations"]["state"] = "not_applied"
        report["reason"] = "Миграции не применены к базе (нет таблицы alembic_version)"
        return report
    if head is None:
        # Сверять не с чем: каталога миграций рядом нет. Отвечаем ОТКАЗОМ, а не
        # «готов с оговоркой»: молчаливо выключившаяся проверка версии — это
        # зелёный healthcheck над схемой, которую никто не сверял. В штатном
        # образе каталог на месте (Dockerfile копирует alembic), так что сюда
        # попадает только неправильно собранный образ. Нашло ревью.
        report["migrations"]["state"] = "head_unknown"
        report["reason"] = (
            "Версию схемы не с чем сверить: рядом нет каталога миграций — "
            "образ собран неправильно"
        )
        return report
    if applied != head:
        report["migrations"]["state"] = "mismatch"
        report["reason"] = (
            f"Схема базы не на последней миграции: применена {applied}, "
            f"в коде {head}. Выполните alembic upgrade head"
        )
        return report

    report["migrations"]["state"] = "ok"
    report["status"] = "ready"
    return report


def is_ready(report: dict) -> bool:
    return report.get("status") == "ready"
