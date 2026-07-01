# Развёртывание (preprod / prod)

Контейнерный стек: **Postgres + backend (FastAPI/uvicorn) + web (nginx: статика фронта + прокси `/api` на backend)**. Всё поднимается одной командой. Фронт и API — на одном origin, CORS не нужен.

## Требования на сервере

- Docker Engine + Docker Compose v2 (`docker compose version`)
- git
- Доступ в интернет на этапе сборки (npm/pip) и для загрузки производственного календаря

## Первая установка

```bash
git clone <repo> zemlya-tabel && cd zemlya-tabel
./install.sh
```

`install.sh` сам:
1. проверит Docker;
2. создаст `.env.preprod` и **сгенерирует секреты** (`SECRET_KEY`, пароль БД, пароль админа), если файла ещё нет;
3. соберёт образы;
4. поднимет Postgres → накатит миграции (`alembic upgrade head`);
5. создаст первичного админа;
6. поднимет backend + web;
7. напечатает URL и логин/пароль админа.

Открывать: `http://<IP-или-домен-сервера>:8080` (порт меняется через `PREPROD_HTTP_PORT` в `.env.preprod`). При первом входе система попросит сменить пароль.

`.env.preprod` в git **не коммитится** (там секреты). Хочешь задать значения заранее — скопируй `.env.preprod.example` в `.env.preprod` и заполни до запуска `install.sh` (тогда он ничего не перегенерирует).

## Обновление (цикл dev → preprod)

На dev: коммит и `git push`. На сервере:

```bash
git pull && ./deploy.sh
# или одной командой:
./deploy.sh --pull
```

`deploy.sh` пересоберёт образы из текущего кода → накатит новые миграции → перезапустит backend+web. Данные БД (том `tabel-preprod-data`) и `.env.preprod` сохраняются.

## Prod

Тот же репозиторий и те же скрипты, отдельный хост и свой `.env.preprod` (можно переименовать/держать `.env.prod` и указывать `--env-file` — но по умолчанию скрипты ждут `.env.preprod`). Повышение с preprod до prod = смена env-файла, код и образы те же.

> Дальнейший шаг для prod (когда понадобится): собирать образы в CI и пушить в GitHub Container Registry (GHCR), а на хостах делать `docker compose pull` — тогда preprod и prod крутят байт-в-байт один образ и откат делается сменой тега. Пока не настроено; текущая схема (build из git на сервере) этого не требует.

## Полезные команды

```bash
DC="docker compose --env-file .env.preprod -f docker-compose.preprod.yml"

$DC ps                 # статус сервисов
$DC logs -f            # логи (Ctrl+C — выйти)
$DC logs -f backend    # логи только backend
$DC down               # остановить (данные БД в томе сохраняются)
$DC down -v            # ОСТОРОЖНО: снести вместе с данными БД

# Ручные операции в контейнере backend:
$DC run --rm backend alembic current
$DC run --rm backend python -m app.cli reset-password --email admin@zemlya-mo.ru --new-password NEWPASS
```
