# Backend образ для preprod/prod: FastAPI под uvicorn (без --reload).
# Миграции и создание админа запускаются отдельными шагами из install.sh/deploy.sh
# (docker compose run --rm backend alembic upgrade head), поэтому CMD — только сервер.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости (psycopg[binary] и cryptography/bcrypt ставятся из wheel — build-tools не нужны).
# app копируем до install, т.к. пакет ставится из исходников (setuptools find "app*").
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# Alembic — отдельным слоем: правки миграций не триггерят переустановку зависимостей.
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
