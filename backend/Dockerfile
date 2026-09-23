# Backend образ для preprod/prod: FastAPI под uvicorn (без --reload).
# Миграции и создание админа запускаются отдельными шагами из install.sh/deploy.sh
# (docker compose run --rm backend alembic upgrade head), поэтому CMD — только сервер.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости — ТОЛЬКО из requirements.lock (этап 4 п.4.1): диапазоны в pyproject
# дали бы на каждой сборке свой состав, и образ переставал бы совпадать с тем, на
# котором прогонялись тесты. Отдельным слоем до копирования кода: правка app не
# переустанавливает зависимости.
# (psycopg[binary] и cryptography/bcrypt ставятся из wheel — build-tools не нужны.)
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Само приложение — БЕЗ зависимостей: их состав уже зафиксирован строкой выше,
# иначе pip дотянул бы по диапазонам из pyproject что-нибудь новее.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

# Alembic — отдельным слоем: правки миграций не триггерят переустановку зависимостей.
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
