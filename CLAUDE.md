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
- **Auth:** JWT Bearer (python-jose), хеширование passlib[bcrypt]
- **Тесты:** pytest + httpx, SQLite in-memory для изоляции
- **Линтер:** ruff
- **Frontend:** React (не реализован)

## Структура

```
backend/
  app/
    main.py         — FastAPI app, CORS, роутеры, /health
    config.py       — Pydantic Settings (DATABASE_URL, SECRET_KEY, CORS_ORIGINS, ...)
    database.py     — engine, SessionLocal, Base, get_db()
    cli.py          — CLI: python -m app.cli create-admin ...
    models/
      users.py      — User, UserRole (enum)
      departments.py
      companies.py
      schedules.py
      employees.py
      audit_log.py  — AuditLog (append-only)
    schemas/
      auth.py       — LoginRequest, TokenResponse, ChangePasswordRequest
      user.py       — UserBase, UserCreate, UserRead, UserUpdate
    routers/
      auth.py       — POST /api/auth/login, /auth/change-password, GET /api/auth/me
      users.py      — CRUD /api/users (admin only)
    core/
      security.py   — hash_password, verify_password, create_access_token, decode_token
      deps.py       — get_current_user, require_role(*roles)
      audit.py      — log_action(db, actor, entity_type, entity_id, action, ...)
  alembic/          — миграции
  tests/
    conftest.py     — client, db_session (SQLite), admin_user, manager_user fixtures
    test_auth.py
    test_users.py
  pyproject.toml
  alembic.ini
  docker-compose.dev.yml
  .env.example
```

## Команды

```bash
# БД (Docker)
cd backend && docker compose -f docker-compose.dev.yml up -d

# Установка
cd backend && pip install -e ".[dev]"

# Запуск
uvicorn app.main:app --reload

# Создать первого админа
python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"

# Миграции
alembic upgrade head
alembic current
alembic revision --autogenerate -m "describe change"

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
- `XxxRead(XxxBase)` — поля для чтения (id, created_at, вложенные объекты), `from_attributes=True`

### Миграции
- **Только через Alembic**, никогда `Base.metadata.create_all()` в проде.
- `alembic revision --autogenerate -m "короткое описание"` — создать миграцию.
- Коммитить файл миграции вместе с изменениями модели.
- В `alembic/env.py` — `import app.models` чтобы все модели были зарегистрированы в metadata.

### Soft delete
Пользователи (и другие сущности с FK из audit_log) **никогда не удаляются физически** — `is_active=False`.

### Audit log
- Вызывать `log_action(...)` из `app.core.audit` при каждом CRUD и смене статуса.
- `log_action` делает `db.add()` — коммит вызывается снаружи вместе с основным изменением.

### Роутеры
- Регистрировать в `app/main.py` через `app.include_router(...)`.
- Префикс `/api/...` (не `/api/v1` — версионирование добавим позже при необходимости).

## Подводные камни

### Email-адреса в тестах
Всегда использовать `@example.com` или `@example.org` (RFC 2606, зарезервированы для тестов).
**Никогда не использовать `.local`** — он зарезервирован под mDNS и отвергается валидатором `pydantic[email]` с ошибкой «special-use or reserved name».

### Права на файлы при инициализации проектов
Никогда не запускать `npm`, `pip` и другие пакетные менеджеры через `sudo`.
Если файлы всё же созданы от root — исправляется одной командой:
```bash
sudo chown -R <user>:<user> <папка>
```

## Производственный календарь

- **Источник:** xmlcalendar.ru — `https://xmlcalendar.ru/data/ru/{year}/calendar.json`
- **Формула нормы:** `Норма = workdays × hours_per_shift − short_days`
- Все вычисления норм — только через `app.services.calendar`, не дублировать формулу в роутерах.
- В тестах **обязательно мокать** `app.services.calendar.fetch_calendar_from_remote` через `monkeypatch` — никаких реальных сетевых запросов.
- Тип дня в строке `days`: обычное число → выходной/праздник, `N+` → праздничный (тоже нерабочий), `N*` → сокращённый рабочий.

## Переменные окружения

| Переменная                  | Описание                              | Default                              |
|-----------------------------|---------------------------------------|--------------------------------------|
| DATABASE_URL                | postgresql+psycopg://...              | tabel:tabel@localhost:5432/tabel     |
| SECRET_KEY                  | Случайная строка для JWT              | change-me                            |
| ACCESS_TOKEN_EXPIRE_MINUTES | Время жизни токена (минуты)           | 480 (8 ч)                            |
| DEBUG                       | Включить SQL echo                     | false                                |
| CORS_ORIGINS                | Разрешённые origins, через запятую    | http://localhost:5173                |

## Timesheet

- Одна запись = (employee, work_date, company, hours)
- `hours=0` не существует — при сохранении с hours=0 запись удаляется из БД
- Все мутации только через `upsert_cell` сервиса (audit log там)
- Фильтрация по department_id на бэке принудительная для manager
- Один сотрудник может иметь несколько ячеек в один день на разные компании
- Unique constraint: `(employee_id, work_date, company_id)`

## Правило 3 попыток

Если ты столкнулся с проблемой которую не получается решить (тест падает, ошибка установки, синтаксическая ошибка которую исправить не выходит, миграция не применяется и т.п.) — даю тебе 3 попытки.

После 3 неудачных попыток одной и той же задачи:
1. ОСТАНОВИСЬ. Не пробуй дальше.
2. Кратко опиши пользователю что пытался сделать (3 попытки в одну строку каждая).
3. Дай гипотезу почему не получается.
4. Жди указаний от пользователя.

Это правило важнее «довести задачу до конца». Лучше остановиться и спросить, чем сжечь токены на бесполезные попытки.