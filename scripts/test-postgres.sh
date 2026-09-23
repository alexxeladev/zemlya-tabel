#!/usr/bin/env bash
# test-postgres.sh — интеграционный прогон на PostgreSQL одной командой (п.4.5).
#
#   ./scripts/test-postgres.sh                 весь набор с маркером postgres
#   ./scripts/test-postgres.sh tests/test_pg_integration.py   только один файл
#
# Чем отличается от обычного `pytest`: схема строится НАСТОЯЩИМИ миграциями
# (`alembic upgrade head`), поэтому проверяются вещи, которых на SQLite нет
# вовсе — частичные уникальные индексы, блокировки строк и гонки транзакций,
# восстановление из дампа.
#
# Быстрый набор на SQLite этим НЕ заменяется: он идёт как шёл, `pytest` без
# переменной окружения пропускает postgres-тесты.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
note() { printf '  %s\n' "$*"; }

# База ОБЯЗАНА называться *_test: фикстура чистит её целиком (см. conftest).
DEFAULT_URL="postgresql+psycopg://tabel:tabel@localhost:5432/tabel_test"
export TEST_POSTGRES_URL="${TEST_POSTGRES_URL:-$DEFAULT_URL}"

PY="$BACKEND/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

# Postgres под рукой? На дев-машине он живёт в контейнере из backend/docker-compose.dev.yml.
if ! "$PY" - <<'PYEOF' 2>/dev/null
import os, socket
from urllib.parse import urlparse
u = urlparse(os.environ["TEST_POSTGRES_URL"].replace("postgresql+psycopg", "postgresql"))
socket.create_connection((u.hostname or "localhost", u.port or 5432), timeout=3).close()
PYEOF
then
  err "PostgreSQL по адресу из TEST_POSTGRES_URL не отвечает."
  note "Адрес: $TEST_POSTGRES_URL"
  note "Поднять дев-базу: docker compose -f backend/docker-compose.dev.yml up -d"
  note "Или указать свою: TEST_POSTGRES_URL=... ./scripts/test-postgres.sh"
  exit 1
fi

say "Интеграционный прогон на PostgreSQL"
note "База: $TEST_POSTGRES_URL (создаётся сама, схема — alembic upgrade head)"
cd "$BACKEND"
exec "$PY" -m pytest -m postgres "${@:--v}"
