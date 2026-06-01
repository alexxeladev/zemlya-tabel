# CLAUDE.md — zemlya-tabel

## Что это за проект

Система учёта рабочего времени для девелоперской группы «Земля МО».
- Несколько юрлиц; зарплата сотрудника распределяется между ними.
- Ролевая модель: Admin, Manager, Accountant, Employee.
- Workflow периода: Draft → Pending Review → Closed.
- После Closed правки только Admin с обязательным комментарием и audit log.

## Стек

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL (psycopg)
- **Схемы:** Pydantic v2 + pydantic-settings
- **Auth:** JWT в HTTP-Only куки (python-jose), хеширование passlib[bcrypt]
- **Тесты:** pytest + pytest-asyncio + httpx
- **Линтер:** ruff
- **Frontend:** React (не реализован)

## Структура

```
backend/
  app/
    main.py       — FastAPI app, /health
    config.py     — Pydantic Settings (DATABASE_URL, SECRET_KEY, ...)
    database.py   — engine, SessionLocal, Base, get_db()
    models/       — SQLAlchemy модели
    schemas/      — Pydantic схемы
    routers/      — API роутеры
    core/         — auth, security, dependencies
  alembic/        — миграции
  tests/
  pyproject.toml
  alembic.ini
  .env.example
```

## Команды

```bash
# Установка
cd backend && pip install -e ".[dev]"

# Запуск
uvicorn app.main:app --reload

# Миграции
alembic upgrade head
alembic current
alembic revision --autogenerate -m "description"

# Тесты
pytest
pytest -v --tb=short
```

## Конвенции

### Таблицы
- Имена: snake_case, **множественное число** (`users`, `departments`, `time_entries`)

### Pydantic-схемы
Три схемы на каждую сущность:
- `XxxBase` — общие поля
- `XxxCreate(XxxBase)` — поля для создания (пароль, foreign keys)
- `XxxRead(XxxBase)` — поля для чтения (id, created_at, вложенные объекты)

### Миграции
- **Только через Alembic**, никогда `Base.metadata.create_all()` в проде.
- `alembic revision --autogenerate -m "короткое описание"` — создать миграцию.
- Коммитить файл миграции вместе с изменениями модели.

### Роутеры
- Регистрировать в `app/main.py` через `app.include_router(...)`.
- Префикс `/api/v1/...`.

## Переменные окружения

| Переменная                  | Описание                        | Default    |
|-----------------------------|----------------------------------|------------|
| DATABASE_URL                | postgresql+psycopg://...         | —          |
| SECRET_KEY                  | Случайная строка для JWT         | —          |
| ACCESS_TOKEN_EXPIRE_MINUTES | Время жизни токена (минуты)      | 480 (8 ч)  |
| DEBUG                       | Включить SQL echo                | false      |
