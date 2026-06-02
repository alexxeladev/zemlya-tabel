import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import SessionLocal
from app.models.production_calendars import ProductionCalendar
from app.routers.auth import router as auth_router
from app.routers.calendar import router as calendar_router
from app.routers.companies import router as companies_router
from app.routers.departments import router as departments_router
from app.routers.employees import router as employees_router
from app.routers.schedules import router as schedules_router
from app.services.calendar import CalendarFetchError, ensure_calendar

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    yield


app = FastAPI(title="Zemlya Tabel API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(departments_router, prefix="/api/departments", tags=["departments"])
app.include_router(companies_router, prefix="/api/companies", tags=["companies"])
app.include_router(schedules_router, prefix="/api/schedules", tags=["schedules"])
app.include_router(employees_router, prefix="/api/employees", tags=["employees"])
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
