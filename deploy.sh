#!/usr/bin/env bash
# deploy.sh — обновление уже установленного препрода до текущего кода ОДНОЙ командой.
#
#   ./deploy.sh              бэкап БД → пересборка образов → миграции → перезапуск
#   ./deploy.sh --pull       сначала git pull --ff-only, затем то же самое
#   ./deploy.sh --no-backup  пропустить авто-бэкап БД (флаги можно совмещать)
#
# Перед миграциями делается pg_dump в $BACKUP_DIR (по умолчанию ~/backups/zemlya-tabel),
# хранятся последние $BACKUP_KEEP (по умолчанию 10). Оба параметра можно задать
# в .env.preprod (BACKUP_DIR=..., BACKUP_KEEP=...). Если бэкап не удался — деплой
# прерывается (обойти: --no-backup).
#
# Обычный цикл обновления с dev:  git push (на dev)  →  на препроде:  git pull && ./deploy.sh
# .env.preprod и данные БД (том tabel-preprod-data) сохраняются.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env.preprod"
COMPOSE_FILE="$ROOT/docker-compose.preprod.yml"

DKR="docker"   # может стать "sudo docker", если пользователь ещё не в группе docker
DC() { $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
err() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
getenv() { grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2-; }
health_ok() { DC exec -T web wget -q -O /dev/null http://localhost/health 2>/dev/null; }

command -v docker >/dev/null 2>&1 || { err "docker не найден — сначала запусти ./install.sh"; exit 1; }
# Свежая установка до релогина: демон доступен только под sudo.
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then DKR="sudo docker"; fi
fi
$DKR compose version >/dev/null 2>&1 || { err "docker compose (v2) не найден."; exit 1; }
$DKR info >/dev/null 2>&1 || { err "docker daemon не запущен."; exit 1; }
[[ -f "$ENV_FILE" ]] || { err ".env.preprod не найден — сначала запусти ./install.sh"; exit 1; }

DO_PULL=0
DO_BACKUP=1
for arg in "$@"; do
  case "$arg" in
    --pull) DO_PULL=1 ;;
    --no-backup) DO_BACKUP=0 ;;
    *) err "Неизвестный аргумент: $arg (доступно: --pull, --no-backup)"; exit 1 ;;
  esac
done

if [[ "$DO_PULL" == "1" ]]; then
  say "git pull --ff-only…"
  git -C "$ROOT" pull --ff-only
fi

POSTGRES_USER="$(getenv POSTGRES_USER)"
POSTGRES_DB="$(getenv POSTGRES_DB)"
PREPROD_HTTP_PORT="$(getenv PREPROD_HTTP_PORT)"; PREPROD_HTTP_PORT="${PREPROD_HTTP_PORT:-8080}"
BACKUP_DIR="$(getenv BACKUP_DIR)"; BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/zemlya-tabel}"
BACKUP_KEEP="$(getenv BACKUP_KEEP)"; BACKUP_KEEP="${BACKUP_KEEP:-10}"

say "Сборка образов…"
DC build

say "Postgres…"
DC up -d db
for i in $(seq 1 30); do
  if DC exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then break; fi
  [[ $i -eq 30 ]] && { err "БД не поднялась за 30с."; exit 1; }
  sleep 1
done

# ── Бэкап БД перед миграциями (pg_dump | gzip) с ротацией ──
if [[ "$DO_BACKUP" == "1" ]]; then
  say "Бэкап БД перед миграциями…"
  mkdir -p "$BACKUP_DIR"
  DUMP="$BACKUP_DIR/tabel_$(date +%Y%m%d_%H%M%S).sql.gz"
  if DC exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DUMP" && [[ -s "$DUMP" ]]; then
    echo "  Сохранён: $DUMP ($(du -h "$DUMP" | cut -f1))"
    # Ротация: оставляем последние BACKUP_KEEP.
    ls -1t "$BACKUP_DIR"/tabel_*.sql.gz 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -f || true
    echo "  Храню последние $BACKUP_KEEP бэкапов в $BACKUP_DIR."
  else
    rm -f "$DUMP"
    err "Бэкап не удался (pg_dump упал или пустой дамп). Деплой прерван."
    err "Обойти (если уверен): ./deploy.sh --no-backup"
    exit 1
  fi
else
  say "Бэкап пропущен (--no-backup)."
fi

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
