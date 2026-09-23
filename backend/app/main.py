import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import MIN_SECRET_KEY_LENGTH, secret_key_problem, settings
from app.database import SessionLocal, get_db
from app.models.production_calendars import ProductionCalendar
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.calendar import router as calendar_router
from app.routers.companies import router as companies_router
from app.routers.dashboard import router as dashboard_router
from app.routers.departments import router as departments_router
from app.routers.employees import router as employees_router
from app.routers.guard import router as guard_router
from app.routers.org import router as org_router
from app.routers.schedules import router as schedules_router
from app.routers.timesheet import router as timesheet_router

# `reference_audit` и `dashboard_cache` импортируются РАДИ ПОБОЧНОГО ЭФФЕКТА: они регистрируют
# слушатели сессии, которые ведут журнал изменений справочников. Без этого
# импорта журнал молча пуст.
# Этап 3 (историчность): слушатели, ведущие версии условий позиций и
# отклоняющие запись денежных данных в закрытый месяц.
from app.services import (  # noqa: F401
    closed_periods,
    position_terms,
    reference_audit,  # noqa: F401
)
from app.services.calendar import CalendarFetchError, ensure_calendar
from app.services.dashboard_cache import drop_cache
from app.services.position_terms import ClosedPeriodError
from app.services.readiness import is_ready, readiness_report

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Отказ запуска с предсказуемой подписью токенов (task_stage2_access п.2.6).
    # Проверка здесь, а не в Settings: alembic и CLI подпись не используют и
    # должны работать и без секрета.
    problem = secret_key_problem(settings.SECRET_KEY)
    if problem:
        raise RuntimeError(
            f"Запуск отклонён: {problem}. Задайте случайный SECRET_KEY не короче "
            f"{MIN_SECRET_KEY_LENGTH} символов в .env (dev) или .env.preprod — "
            "например: python3 -c 'import secrets; print(secrets.token_hex(32))'"
        )
    current_year = datetime.now().year
    for year in [current_year, current_year + 1]:
        try:
            with SessionLocal() as db:
                exists = db.query(ProductionCalendar).filter_by(year=year).first()
                if not exists:
                    await ensure_calendar(db, year)
                    logger.info("Auto-loaded calendar %d", year)
        except CalendarFetchError as exc:
            logger.warning("Could not preload calendar for %d: %s", year, exc)
        except Exception as exc:
            logger.warning("Skipping calendar preload for %d: %s", year, exc)
    # Кэш дашборда (services/dashboard_cache): версии поднимаются ПОСЛЕ коммита
    # данных; процесс, убитый ровно между ними (рестарт на деплое), оставил бы
    # кэш месяца устаревшим навсегда. Сброс на старте закрывает этот случай —
    # ценой одного пересчёта при первом открытии после рестарта.
    try:
        with SessionLocal() as db:
            drop_cache(db)
            db.commit()
    except Exception as exc:
        logger.warning("Could not drop dashboard cache on startup: %s", exc)
    yield


app = FastAPI(title="Zemlya Tabel API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.exception_handler(StaleDataError)
async def stale_data_conflict(request: Request, exc: StaleDataError) -> JSONResponse:
    """Строку успел изменить другой запрос → 409, а не 500 (task_stage1 п.1.5).

    У ячейки табеля есть версия (`TimesheetEntry.version`, `version_id_col`), и
    она стоит в WHERE любого ORM-UPDATE/DELETE. Правка часов переводит опоздание
    в 409 сама (`services/timesheet._cell_write`); сюда попадают ОСТАЛЬНЫЕ
    писатели ячеек — код отсутствия (он удаляет часы дня), очистка часов при
    смене дат, перенос отдела. Транзакция запроса откатывается при закрытии
    сессии в `get_db`, в базе ничего не меняется.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": (
            "Данные табеля только что изменил другой пользователь — "
            "обновите страницу и повторите действие"
        )},
    )


@app.exception_handler(ClosedPeriodError)
async def closed_period_conflict(request: Request, exc: ClosedPeriodError) -> JSONResponse:
    """Правка задевает закрытый месяц или месяц на проверке → 409
    (task_stage3_historicity). Бросает её слушатель сессии
    (`services/closed_periods`, `services/position_terms`), поэтому ни один
    эндпойнт, пишущий те же данные, мимо запрета не пройдёт. Транзакция
    откатывается при закрытии сессии в `get_db`."""
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(departments_router, prefix="/api/departments", tags=["departments"])
app.include_router(companies_router, prefix="/api/companies", tags=["companies"])
app.include_router(schedules_router, prefix="/api/schedules", tags=["schedules"])
# Модуль «Вахта» (task_vahta): табель охраны на постах со своим справочником
# экипажей и постов. Расчёт свой (без округления, проценты от поста), но
# результат вливается в общую ведомость.
app.include_router(guard_router, prefix="/api/vahta", tags=["vahta"])
app.include_router(employees_router, prefix="/api/employees", tags=["employees"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])
app.include_router(timesheet_router, prefix="/api/timesheet", tags=["timesheet"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(org_router, prefix="/api/org", tags=["org"])
app.include_router(audit_router, prefix="/api/audit", tags=["audit"])


@app.get("/health")
def health_check():
    """ЖИВ: процесс отвечает. В базу НЕ ходит — намеренно (этап 4 п.4.2).

    По этой проверке перезапускают зависший контейнер. Завяжи её на базу — и
    падение базы уводило бы в бесконечный рестарт живой процесс. «Готов ли
    принимать запросы» — отдельный вопрос, `/ready`.
    """
    return {"status": "ok"}


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    """ГОТОВ принимать запросы: база отвечает и схема на последней миграции.

    Отказ — 503 (а не 200 с пометкой): по коду ответа решают балансировщик и
    docker-healthcheck, тело они не читают. Правило готовности живёт в одном
    месте — `services/readiness.py`.
    """
    report = readiness_report(db)
    if not is_ready(report):
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=report)
    return report
