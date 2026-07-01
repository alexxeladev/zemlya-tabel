#!/usr/bin/env bash
# install.sh — первичная установка препрод-стека zemlya-tabel ОДНОЙ командой.
#
#   ./install.sh
#
# Делает всё: ставит Docker если его нет (get.docker.com, нужен sudo) →
# создаёт .env.preprod с генерацией секретов (если файла нет) → собирает образы
# (зависимости backend/frontend ставятся внутри образов) → поднимает Postgres →
# миграции (alembic upgrade head) → создаёт первичного админа → поднимает
# backend+web (nginx: статика + прокси /api) → печатает URL и данные для входа.
#
# Идемпотентна: повторный запуск не перезатирает .env.preprod и не пересоздаёт
# админа. Для обновления уже установленного препрода используй ./deploy.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env.preprod"
COMPOSE_FILE="$ROOT/docker-compose.preprod.yml"

DKR="docker"   # может стать "sudo docker" после свежей установки (до релогина)
DC() { $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
err() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
# Читает значение ключа из .env.preprod (всё после первого '='), без source.
getenv() { grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2-; }
gen_secret() {  # $1 = число байт → hex-строка (shell/URL-safe)
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else python3 -c "import secrets,sys;print(secrets.token_hex(int(sys.argv[1])))" "$1"; fi
}
# Health-check без зависимости от host-утилит: busybox wget внутри web-контейнера.
health_ok() { DC exec -T web wget -q -O /dev/null http://localhost/health 2>/dev/null; }

# ── 0. Docker: проверка + авто-установка при отсутствии ──
# Ставит Docker Engine + compose-плагин официальным скриптом get.docker.com
# (автоопределяет дистрибутив). Требует root или sudo. Отключить: AUTO_INSTALL_DOCKER=0.
ensure_docker() {
  command -v docker >/dev/null 2>&1 && return 0
  err "docker не найден."
  [[ "${AUTO_INSTALL_DOCKER:-1}" == "1" ]] || { err "Авто-установка отключена (AUTO_INSTALL_DOCKER=0). Поставь Docker вручную."; exit 1; }
  [[ "$(uname -s)" == "Linux" ]] || { err "Авто-установка Docker только для Linux. Вручную: https://docs.docker.com/get-docker/"; exit 1; }

  local SUDO=""
  if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || { err "Нужен root или sudo для установки Docker."; exit 1; }
    SUDO="sudo"
  fi

  say "Устанавливаю Docker (официальный скрипт get.docker.com)…"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO /tmp/get-docker.sh https://get.docker.com
  else
    err "Нет curl/wget — нечем скачать установщик Docker."; exit 1
  fi
  $SUDO sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh

  say "Запускаю docker-демон…"
  $SUDO systemctl enable --now docker 2>/dev/null || $SUDO service docker start 2>/dev/null || true
  if [[ -n "$SUDO" ]]; then
    $SUDO usermod -aG docker "$USER" 2>/dev/null || true
    echo "  Добавил '$USER' в группу docker (без sudo заработает после релогина)."
  fi
}
ensure_docker

# Если демон недоступен под текущим пользователем (свежая установка, ещё не в группе
# docker), но доступен под sudo — в этой сессии зовём docker через sudo.
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DKR="sudo docker"
    echo "  (в этой сессии docker вызывается через sudo — до релогина)"
  fi
fi

$DKR compose version >/dev/null 2>&1 || { err "docker compose (v2) не найден."; exit 1; }
$DKR info >/dev/null 2>&1 || { err "docker daemon не запущен. Подними его и повтори."; exit 1; }

# ── 1. .env.preprod ──
if [[ -f "$ENV_FILE" ]]; then
  say "Использую существующий .env.preprod (секреты не трогаю)."
else
  say "Создаю .env.preprod и генерирую секреты…"
  SECRET_KEY="$(gen_secret 32)"
  DB_PASS="$(gen_secret 24)"
  ADMIN_PASS="$(gen_secret 12)"
  cat > "$ENV_FILE" <<EOF
PREPROD_HTTP_PORT=8080
POSTGRES_USER=tabel
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=tabel
DATABASE_URL=postgresql+psycopg://tabel:${DB_PASS}@db:5432/tabel
SECRET_KEY=${SECRET_KEY}
ACCESS_TOKEN_EXPIRE_MINUTES=480
DEBUG=false
CORS_ORIGINS=http://localhost:8080
VITE_API_URL=
ADMIN_EMAIL=admin@zemlya-mo.ru
ADMIN_PASSWORD=${ADMIN_PASS}
ADMIN_FULL_NAME=Администратор
EOF
  chmod 600 "$ENV_FILE"
  echo "  Готово: $ENV_FILE (chmod 600, в git не попадёт)."
fi

POSTGRES_USER="$(getenv POSTGRES_USER)"
POSTGRES_DB="$(getenv POSTGRES_DB)"
PREPROD_HTTP_PORT="$(getenv PREPROD_HTTP_PORT)"; PREPROD_HTTP_PORT="${PREPROD_HTTP_PORT:-8080}"
ADMIN_EMAIL="$(getenv ADMIN_EMAIL)"
ADMIN_PASSWORD="$(getenv ADMIN_PASSWORD)"
ADMIN_FULL_NAME="$(getenv ADMIN_FULL_NAME)"

# ── 2. Сборка образов ──
say "Сборка образов (backend, web)…"
DC build

# ── 3. Postgres ──
say "Запуск Postgres…"
DC up -d db
echo "  Жду готовности БД…"
for i in $(seq 1 30); do
  if DC exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    echo "  БД готова."; break
  fi
  [[ $i -eq 30 ]] && { err "БД не поднялась за 30с."; exit 1; }
  sleep 1
done

# ── 4. Миграции ──
say "Миграции (alembic upgrade head)…"
DC run --rm backend alembic upgrade head

# ── 5. Первичный админ (идемпотентно) ──
say "Создание первичного админа…"
if DC run --rm backend python -m app.cli create-admin \
      --email "$ADMIN_EMAIL" --password "$ADMIN_PASSWORD" --full-name "$ADMIN_FULL_NAME"; then
  ADMIN_CREATED=1
else
  ADMIN_CREATED=0
  echo "  Админ уже существует (или ошибка выше) — пропускаю."
fi

# ── 6. Запуск всего стека ──
say "Запуск backend + web…"
DC up -d
echo "  Жду готовности приложения…"
for i in $(seq 1 30); do
  if health_ok; then echo "  Приложение отвечает."; break; fi
  [[ $i -eq 30 ]] && { err "Приложение не ответило за 30с. Смотри: ./deploy.sh логи или DC logs."; break; }
  sleep 1
done

# ── Итог ──
printf '\n\033[1;32m✔ Препрод развёрнут.\033[0m\n'
printf '  Открывать:  http://<IP-или-домен-сервера>:%s   (локально: http://localhost:%s)\n' \
  "$PREPROD_HTTP_PORT" "$PREPROD_HTTP_PORT"
if [[ "${ADMIN_CREATED}" == "1" ]]; then
  printf '  Логин:      %s\n  Пароль:     %s\n' "$ADMIN_EMAIL" "$ADMIN_PASSWORD"
  printf '  (при первом входе система попросит сменить пароль)\n'
else
  printf '  Логин:      %s  (создан ранее — пароль прежний)\n' "$ADMIN_EMAIL"
fi
cat <<EOF

  Обновить в будущем:  git pull && ./deploy.sh
  Логи:                docker compose --env-file .env.preprod -f docker-compose.preprod.yml logs -f
  Остановить:          docker compose --env-file .env.preprod -f docker-compose.preprod.yml down
EOF
