# Задача 1.2 — модели БД, миграции, базовая авторизация

## Контекст

Проект `zemlya-tabel` уже инициализирован (структура папок, FastAPI скелет, Alembic, health-check). Эта задача добавляет фундаментальные сущности и систему входа в систему.

Не трогаем то что не относится к этой задаче (фронтенд, табели, экспорт). Только модели + auth + базовый CRUD пользователей.

## Что нужно сделать

### 1. Модели БД (`backend/app/models/`)

Создать SQLAlchemy 2.x модели в стиле `Mapped[...]` (typed columns). Каждая модель — отдельный файл, плюс `__init__.py` который их экспортирует. Все модели наследуются от общего `Base` (он уже в `database.py`).

**Общие правила для всех моделей:**
- Primary key: `id: Mapped[int] = mapped_column(primary_key=True)`
- Поля `created_at` и `updated_at` (server_default=func.now(), onupdate=func.now())
- Имена таблиц: snake_case множественное число (`users`, `departments` и т.д.)
- Использовать `relationship()` для связей, `back_populates` с обеих сторон
- Внешние ключи именовать `<entity>_id`, например `department_id`

**Перечень сущностей:**

#### `users.py` — пользователи системы
- `email` (str, unique, индекс)
- `full_name` (str)
- `hashed_password` (str)
- `role` (enum: `admin`, `manager`, `accountant`, `employee`)
- `is_active` (bool, default True)
- `must_change_password` (bool, default True) — флаг для смены при первом входе
- `department_id` (FK на departments, nullable — у админа/бухгалтера нет привязки)
- `employee_id` (FK на employees, nullable — связь с карточкой сотрудника, если есть)
- `last_login_at` (datetime, nullable)

#### `departments.py` — отделы
- `name` (str, unique)
- `code` (str, unique, короткий код)

#### `companies.py` — юрлица
- `code` (str, unique, 1-5 символов)
- `name` (str)
- `inn` (str, nullable)

#### `schedules.py` — графики работы
- `name` (str, unique) — например "5/2", "2/2"
- `hours_per_shift` (int)
- `description` (str, nullable)

#### `employees.py` — карточки сотрудников
- `tab_number` (str, unique, nullable) — табельный номер
- `full_name` (str)
- `position` (str, nullable) — должность
- `department_id` (FK на departments)
- `schedule_id` (FK на schedules)
- `default_company_id` (FK на companies)
- `rate` (numeric(12, 2)) — оклад в месяц
- `is_active` (bool, default True)
- `hire_date` (date, nullable)
- `dismissal_date` (date, nullable)

#### `audit_log.py` — журнал изменений
- `actor_id` (FK на users)
- `entity_type` (str) — например "employee", "timesheet_entry"
- `entity_id` (int, nullable) — id затронутой сущности
- `action` (str) — "create", "update", "delete", "status_change"
- `before` (JSONB, nullable)
- `after` (JSONB, nullable)
- `reason` (str, nullable)
- `created_at` (только этот, без updated_at, append-only)
- Индексы: по `entity_type+entity_id`, по `actor_id`, по `created_at`

### 2. Миграции

Сгенерировать первую миграцию через `alembic revision --autogenerate -m "create core tables"`. Проверить что в неё попали все таблицы. Применить `alembic upgrade head`.

Для локальной разработки настроить чтобы база была локальная Postgres. **Если Postgres не установлен** — поднять через Docker compose:

Создать `backend/docker-compose.dev.yml`:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: tabel
      POSTGRES_PASSWORD: tabel
      POSTGRES_DB: tabel
    ports:
      - "5432:5432"
    volumes:
      - tabel-data:/var/lib/postgresql/data
volumes:
  tabel-data:
```

Обновить README с командой `docker compose -f docker-compose.dev.yml up -d` для локального запуска БД.

В `.env.example` положить рабочий `DATABASE_URL=postgresql+psycopg://tabel:tabel@localhost:5432/tabel`.

### 3. Auth — core слой

Создать отдельный модуль `backend/app/core/security.py`:
- `hash_password(plain: str) -> str` через passlib bcrypt
- `verify_password(plain: str, hashed: str) -> bool`
- `create_access_token(subject: str | int, extra: dict = None) -> str` — JWT через python-jose, секрет из settings, exp из ACCESS_TOKEN_EXPIRE_MINUTES
- `decode_token(token: str) -> dict` — возвращает payload или поднимает исключение

Создать `backend/app/core/deps.py`:
- `get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User` — извлекает пользователя из JWT
- `require_role(*roles)` — фабрика декораторов: `require_role("admin")`, `require_role("admin", "accountant")` и т.д. Если роль не подходит — HTTPException 403.

### 4. Auth — Pydantic схемы

В `backend/app/schemas/auth.py`:
- `LoginRequest` (email, password)
- `TokenResponse` (access_token, token_type="bearer", must_change_password: bool)
- `ChangePasswordRequest` (current_password, new_password)

В `backend/app/schemas/user.py`:
- `UserBase` (email, full_name, role, department_id, employee_id, is_active)
- `UserCreate(UserBase)` (+ password)
- `UserRead(UserBase)` (+ id, must_change_password, last_login_at; from_attributes=True)
- `UserUpdate` (все поля optional, кроме обязательной валидации)

### 5. Auth — роутер

`backend/app/routers/auth.py`:
- `POST /auth/login` — принимает `LoginRequest`, проверяет пароль, обновляет `last_login_at`, возвращает `TokenResponse`. Если пользователь неактивен — 403.
- `POST /auth/change-password` — для авторизованных, проверяет current_password, обновляет на new_password (с хешированием), сбрасывает `must_change_password=False`.
- `GET /auth/me` — возвращает текущего `UserRead`.

### 6. Управление пользователями — роутер

`backend/app/routers/users.py` — все эндпойнты доступны **только** для роли `admin`:
- `POST /users` — создать пользователя. Принимает `UserCreate`. Хеширует пароль, ставит `must_change_password=True`. Возвращает `UserRead`.
- `GET /users` — список всех пользователей. Параметры query: `role`, `department_id`, `is_active`.
- `GET /users/{id}` — конкретный пользователь.
- `PATCH /users/{id}` — обновить (`UserUpdate`).
- `POST /users/{id}/reset-password` — сбрасывает пароль на новый временный, ставит `must_change_password=True`. Возвращает новый временный пароль в ответе (одноразово).
- `DELETE /users/{id}` — мягкое удаление: `is_active=False` (не физическое удаление, чтобы аудит не сломался).

Все действия admin записываются в audit_log (добавить хелпер `log_action(db, actor, entity_type, entity_id, action, before=None, after=None, reason=None)` в `core/audit.py`).

### 7. Подключить роутеры

В `backend/app/main.py` подключить:
```python
app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(users_router, prefix="/api/users", tags=["users"])
```

### 8. CORS

В `main.py` добавить CORS middleware с `allow_origins=["http://localhost:5173"]` (под Vite — фронтенд будет позже). Из settings взять список (`CORS_ORIGINS`, через запятую в .env).

### 9. Seed первого админа

`backend/app/cli.py` — простой скрипт через typer или argparse:
```bash
python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
```

Создаёт пользователя с ролью admin, `must_change_password=True`. Если такой email уже есть — ошибка. Документировать команду в README.

### 10. Тесты

В `backend/tests/`:
- `conftest.py` — фикстура `client` (TestClient) и `db_session` с SQLite in-memory для изоляции
- `test_auth.py`:
  - login со правильным паролем возвращает токен
  - login с неправильным паролем — 401
  - login неактивного — 403
  - change-password сбрасывает флаг must_change_password
  - /auth/me требует токен
- `test_users.py`:
  - создание пользователя требует admin
  - manager не может создать пользователя — 403
  - список пользователей фильтруется по role и department_id
  - reset-password возвращает новый временный пароль

### 11. Документация

- Обновить `CLAUDE.md`: добавить разделы про модели, конвенции (audit_log на каждую правку, мягкое удаление), команды (`alembic revision`, `python -m app.cli create-admin`).
- Обновить `README.md`: команды для запуска БД через docker-compose, создания админа, прогона тестов.
- В `docs/decisions.md` добавить раздел про soft delete и audit log.

### 12. Коммит и пуш

Один-два логичных коммита:
- `feat(db): core models with migrations`
- `feat(auth): JWT login, password management, user CRUD`

Пушнуть на main.

## Acceptance criteria

После выполнения:

```bash
# БД поднимается
cd backend && docker compose -f docker-compose.dev.yml up -d

# Миграции применяются
alembic upgrade head

# Можно создать админа
python -m app.cli create-admin --email admin@test.local --password admin123 --full-name "Test Admin"

# Запуск
uvicorn app.main:app --reload

# Логин работает
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.local","password":"admin123"}'
# → возвращает {"access_token":"...","token_type":"bearer","must_change_password":true}

# /auth/me работает с токеном
curl http://localhost:8000/api/auth/me -H "Authorization: Bearer <TOKEN>"

# Тесты проходят
pytest
# → все зелёные
```

## Что НЕ делать

- Не реализовывать табели, графики работы по дням, экспорт — это следующие задачи
- Не делать LDAP/SSO — только локальная auth с email+password
- Не делать UI / фронтенд — будет позже
- Не реализовывать сложную ролевую модель сверх того что описано (role-based access по простому Depends достаточно)
- Не оптимизировать преждевременно

## В конце

Покажи структуру проекта (`tree -L 3 -I '__pycache__|.venv|node_modules'`) и пришли результат прогона `pytest -v`.
