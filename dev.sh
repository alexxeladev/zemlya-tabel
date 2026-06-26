#!/usr/bin/env bash
# dev.sh — запуск всей системы zemlya-tabel в dev-режиме одной командой.
#
#   ./dev.sh            запустить БД + бэкенд + фронтенд
#   ./dev.sh --seed     перед запуском очистить данные и залить тестовые
#                       (python -m app.cli reset-data --yes && seed-test-data)
#
# Поднимает: Postgres (Docker) → миграции (alembic) → backend (uvicorn :8000)
# → frontend (vite :5173). Ctrl+C корректно останавливает backend и frontend
# (Postgres остаётся в Docker — гасится `docker compose ... down`).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
SEED=0
[[ "${1:-}" == "--seed" ]] && SEED=1

# Активировать venv бэкенда, если есть.
if [[ -f "$BACKEND/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$BACKEND/.venv/bin/activate"
fi

echo "▶ 1/4  Postgres (Docker)…"
( cd "$BACKEND" && docker compose -f docker-compose.dev.yml up -d )

echo "▶ 2/4  Ждём готовности БД…"
for i in $(seq 1 30); do
  if ( cd "$BACKEND" && docker compose -f docker-compose.dev.yml exec -T db pg_isready -U tabel >/dev/null 2>&1 ); then
    echo "  БД готова."
    break
  fi
  [[ $i -eq 30 ]] && { echo "  ✗ БД не поднялась за 30с"; exit 1; }
  sleep 1
done

echo "▶ 3/4  Миграции (alembic upgrade head)…"
( cd "$BACKEND" && alembic upgrade head )

if [[ $SEED -eq 1 ]]; then
  echo "▶ 3.5  Сброс и заливка тестовых данных…"
  ( cd "$BACKEND" && python -m app.cli reset-data --yes && python -m app.cli seed-test-data )
fi

echo "▶ 4/4  Запуск backend (:8000) и frontend (:5173)…"

# Корректно гасим оба процесса по Ctrl+C.
PIDS=()
cleanup() {
  echo ""
  echo "⏹ Останавливаю backend/frontend…"
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "  Готово. Postgres продолжает работать (docker compose ... down — чтобы остановить)."
}
trap cleanup INT TERM

( cd "$BACKEND" && uvicorn app.main:app --reload ) &
PIDS+=($!)

( cd "$FRONTEND" && npm run dev ) &
PIDS+=($!)

echo ""
echo "  Backend:  http://localhost:8000  (docs: /docs)"
echo "  Frontend: http://localhost:5173"
echo "  Ctrl+C — остановить."
wait
