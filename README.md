# zemlya-tabel

Система учёта рабочего времени для девелоперской группы «Земля МО».

Стек: FastAPI + PostgreSQL (backend), React + Vite (frontend). Экспорт Т-13 и
ведомости в Excel, ролевая модель, workflow периодов, расчёт ЗП с распределением
по юрлицам.

## Установка на сервер (preprod/prod) — одной командой

Полный контейнерный стек (**Postgres + backend + nginx со статикой фронта и
прокси `/api`**) поднимается одним скриптом. На сервере нужны только **Linux,
git и sudo** — **Docker `install.sh` поставит сам**, если его нет (официальный
скрипт get.docker.com). Зависимости backend/frontend ставятся внутри Docker-образов.

### Первая установка

```bash
git clone https://github.com/alexxeladev/zemlya-tabel.git
cd zemlya-tabel
./install.sh
```

`install.sh` сделает всё сам:

1. установит Docker, если его нет (get.docker.com, нужен sudo);
2. создаст `.env.preprod` и **сгенерирует секреты** (`SECRET_KEY`, пароль БД,
   пароль админа), если файла ещё нет;
3. соберёт образы;
4. поднимет Postgres → накатит миграции (`alembic upgrade head`);
5. создаст первичного администратора;
6. поднимет backend + web;
7. напечатает **URL и логин/пароль админа**.

После установки приложение доступно на `http://<IP-или-домен-сервера>:8080`
(порт задаётся `PREPROD_HTTP_PORT` в `.env.preprod`). При первом входе система
попросит сменить пароль.

> `.env.preprod` содержит секреты и в git **не коммитится**. Хочешь задать логин
> админа / порт заранее — скопируй `.env.preprod.example` в `.env.preprod` и
> заполни **до** запуска `./install.sh` (тогда он ничего не перегенерирует).

### Обновление (цикл dev → preprod)

На dev-машине — `git push`. На сервере:

```bash
git pull && ./deploy.sh
# или одной командой:
./deploy.sh --pull
```

`deploy.sh` пересоберёт образы из текущего кода → накатит новые миграции →
перезапустит backend + web. Данные БД (том `tabel-preprod-data`) и `.env.preprod`
сохраняются.

Подробности, команды диагностики и путь к prod — в [docs/DEPLOY.md](docs/DEPLOY.md).

## Запуск в dev-режиме — одной командой

Для локальной разработки (Postgres в Docker, backend/frontend с hot-reload на
хосте):

```bash
./dev.sh           # Postgres → миграции → backend :8000 → frontend :5173
./dev.sh --seed    # то же + сброс данных и заливка тестовых перед стартом
```

Ниже — те же шаги вручную, по компонентам.

## Запуск backend локально

### 1. База данных (PostgreSQL через Docker)

```bash
cd backend
docker compose -f docker-compose.dev.yml up -d
```

### 2. Установка зависимостей

```bash
cd backend
pip install -e ".[dev]"
```

### 3. Настройка окружения

```bash
cp .env.example .env
# Заполнить SECRET_KEY (остальные значения из .env.example уже рабочие для docker-compose.dev.yml)
```

### 4. Миграции

```bash
cd backend
alembic upgrade head
```

### 5. Создание первого администратора

```bash
cd backend
python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
```

### 6. Запуск сервера

```bash
cd backend
uvicorn app.main:app --reload
```

API доступен на http://localhost:8000  
Документация: http://localhost:8000/docs

### 7. Тесты

```bash
cd backend
pytest
# или
pytest -v --tb=short
```

## Запуск frontend локально

### 1. Установка зависимостей

```bash
cd frontend
npm install
```

### 2. Dev-сервер

```bash
cd frontend
npm run dev
# открывает http://localhost:5173
```

Требует запущенного backend на http://localhost:8000.

### 3. Продакшн-сборка

```bash
cd frontend
npm run build
# артефакты в frontend/dist/
```
