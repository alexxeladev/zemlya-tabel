#!/usr/bin/env bash
# deploy.sh — обновление уже установленного препрода до текущего кода ОДНОЙ командой.
#
#   ./deploy.sh          пересобрать образы из текущего кода → миграции → перезапуск
#   ./deploy.sh --pull   сначала git pull --ff-only, затем то же самое
#
# Обычный цикл обновления с dev:  git push (на dev)  →  на препроде:  git pull && ./deploy.sh
# .env.preprod и данные БД (том tabel-preprod-data) сохраняются.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env.preprod"
COMPOSE_FILE="$ROOT/docker-compose.preprod.yml"

DC() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
err() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
getenv() { grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2-; }
health_ok() { DC exec -T web wget -q -O /dev/null http://localhost/health 2>/dev/null; }

command -v docker >/dev/null 2>&1 || { err "docker не найден."; exit 1; }
docker compose version >/dev/null 2>&1 || { err "docker compose (v2) не найден."; exit 1; }
docker info >/dev/null 2>&1 || { err "docker daemon не запущен."; exit 1; }
[[ -f "$ENV_FILE" ]] || { err ".env.preprod не найден — сначала запусти ./install.sh"; exit 1; }

if [[ "${1:-}" == "--pull" ]]; then
  say "git pull --ff-only…"
  git -C "$ROOT" pull --ff-only
fi

POSTGRES_USER="$(getenv POSTGRES_USER)"
POSTGRES_DB="$(getenv POSTGRES_DB)"
PREPROD_HTTP_PORT="$(getenv PREPROD_HTTP_PORT)"; PREPROD_HTTP_PORT="${PREPROD_HTTP_PORT:-8080}"

say "Сборка образов…"
DC build

say "Postgres…"
DC up -d db
for i in $(seq 1 30); do
  if DC exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then break; fi
  [[ $i -eq 30 ]] && { err "БД не поднялась за 30с."; exit 1; }
  sleep 1
done

say "Миграции (alembic upgrade head)…"
DC run --rm backend alembic upgrade head

say "Перезапуск backend + web…"
DC up -d

echo "  Жду готовности приложения…"
for i in $(seq 1 30); do
  if health_ok; then echo "  Приложение отвечает."; break; fi
  [[ $i -eq 30 ]] && { err "Приложение не ответило за 30с. Смотри: DC logs -f."; break; }
  sleep 1
done

printf '\n\033[1;32m✔ Препрод обновлён.\033[0m  http://<host>:%s\n' "$PREPROD_HTTP_PORT"
