# Bootstrap проекта zemlya-tabel

Это новый проект. Создаём бэкенд системы учёта рабочего времени для девелоперской компании.

**Стек:** Python 3.11+, FastAPI, PostgreSQL, SQLAlchemy 2.x, Alembic для миграций, Pydantic v2, pytest для тестов.

## Что нужно сделать

### 1. Git

Инициализировать git-репозиторий, привязать к remote:
```
https://github.com/alexxeladev/zemlya-tabel.git
```

### 2. Структура проекта

```
zemlya-tabel/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI приложение
│   │   ├── config.py         # Pydantic Settings из .env
│   │   ├── database.py       # SQLAlchemy engine + session
│   │   ├── models/           # SQLAlchemy модели (пока пусто)
│   │   │   └── __init__.py
│   │   ├── schemas/          # Pydantic схемы
│   │   │   └── __init__.py
│   │   ├── routers/          # API роутеры
│   │   │   └── __init__.py
│   │   └── core/             # auth, security, deps
│   │       └── __init__.py
│   ├── alembic/              # миграции (alembic init)
│   ├── tests/
│   │   └── __init__.py
│   ├── pyproject.toml        # зависимости через uv или pip
│   ├── .env.example          # пример переменных окружения
│   └── alembic.ini
├── frontend/                  # пока создать пустую папку с .gitkeep
├── docs/
│   └── decisions.md          # сюда складываем архитектурные решения
├── CLAUDE.md                  # инструкции для будущих сессий Claude Code
├── README.md
└── .gitignore                 # стандартный Python + Node + IDE
```

### 3. Зависимости

В `backend/pyproject.toml` указать:

**Runtime:**
- fastapi
- uvicorn[standard]
- sqlalchemy>=2.0
- alembic
- psycopg[binary]  (драйвер PostgreSQL)
- pydantic>=2.0
- pydantic-settings
- python-jose[cryptography]  (JWT для auth позже)
- passlib[bcrypt]  (хеширование паролей)
- python-multipart  (form-data для логина)

**Dev:**
- pytest
- pytest-asyncio
- httpx
- ruff

### 4. config.py

В `backend/app/config.py` — Pydantic Settings, читающий из .env:
- DATABASE_URL (postgresql+psycopg://...)
- SECRET_KEY
- ACCESS_TOKEN_EXPIRE_MINUTES (по умолчанию 480 = 8 часов)
- DEBUG (bool)

### 5. main.py

В `backend/app/main.py` — минимальное FastAPI приложение с одним health-check эндпойнтом `GET /health` возвращающим `{"status": "ok"}`.

### 6. database.py

В `backend/app/database.py` — настроенный engine и `get_db()` dependency.

### 7. Alembic

Инициализировать Alembic в папке `backend/alembic/` (alembic init), настроить env.py чтобы он брал DATABASE_URL из settings.

### 8. .env.example

Создать с примерами всех переменных (с пустыми значениями SECRET_KEY и DATABASE_URL).

### 9. CLAUDE.md

Заполнить так чтобы будущие сессии понимали проект:
- Что это за проект (учёт рабочего времени, мульти-юрлица, ролевая модель)
- Стек
- Структура папок
- Команды для запуска (uvicorn, alembic upgrade head, pytest)
- Конвенции: имена таблиц snake_case множественное число (users, departments), Pydantic-схемы по три на сущность (XxxBase, XxxCreate, XxxRead)
- Обязательно: миграции через Alembic, не autocreate

### 10. docs/decisions.md

Заполнить так:

```markdown
# Архитектурные решения

## Контекст проекта
Система учёта рабочего времени для девелоперской группы «Земля МО».
Несколько юрлиц, между которыми распределяется зарплата сотрудника.

## Стек
- Backend: Python 3.11+, FastAPI, SQLAlchemy 2.x, PostgreSQL, Alembic
- Frontend: React (будет позже)
- Хостинг: on-premise, Ubuntu Server

## Роли
- Admin: всё
- Manager (Руководитель): видит только свой department
- Accountant (Бухгалтер): видит все departments, закрывает периоды, выгружает в 1С
- Employee (Сотрудник): видит только свои часы

## Workflow периода
Draft → Pending Review → Closed
После Closed правки только Admin, обязательный комментарий, audit log.

## Auth
На старте: ручное создание учёток админом + временный пароль, смена при первом входе. JWT в HTTP-Only куки.
В будущем: LDAP/SSO Yandex. Auth-слой отдельный, чтобы заменить провайдер.

## Audit log
Append-only таблица, поля: who, when, entity_type, entity_id, action, before, after, reason.
Логируем: CRUD всех сущностей, смены статуса периодов, правки часов.

## Расчёт ЗП
Оклад × (отработано / норма часов), переработка × 1.5, праздничные × 1.5.

## Изоляция данных
Employee принадлежит Department. Manager видит только свой Department.

## Экспорт в 1С
Этап 1: XML-файл, бухгалтер загружает вручную.
Этап 2: REST API в 1С (HTTP-сервис, опубликованный с 1С-стороны).
```

### 11. README.md

Короткий, только инструкция по запуску backend локально на dev-машине: установка зависимостей, копирование .env, миграции, запуск сервера, прогон тестов.

### 12. .gitignore

Стандартный, включить: `.env`, `__pycache__`, `.venv`, `node_modules`, `.vscode`, `.idea`.

### 13. Git

Сделать первый коммит `chore: bootstrap project structure` и запушить на main в GitHub.

## Acceptance criteria

После выполнения должно работать:

- `cd backend && pip install -e .` — устанавливает зависимости без ошибок
- `uvicorn app.main:app --reload` — запускает сервер
- `curl http://localhost:8000/health` — возвращает `{"status": "ok"}`
- `alembic current` — работает (показывает None, миграций ещё нет)
- `pytest` — проходит (тестов пока нет, должно быть "no tests ran")
- Репозиторий на GitHub содержит всю структуру

## Что НЕ делать в этой задаче

- Не создавать модели БД (это следующая задача)
- Не реализовывать auth (отдельная задача)
- Не подключать фронтенд
- Не настраивать Docker

## В конце

Покажи финальную структуру через `tree -I '__pycache__|.venv|node_modules' --gitignore` или `ls -R`.
