from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.auth import router as auth_router
from app.routers.companies import router as companies_router
from app.routers.departments import router as departments_router
from app.routers.employees import router as employees_router
from app.routers.schedules import router as schedules_router

app = FastAPI(title="Zemlya Tabel API", version="0.1.0")

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


@app.get("/health")
def health_check():
    return {"status": "ok"}
