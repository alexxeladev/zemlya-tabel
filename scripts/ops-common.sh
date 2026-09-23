# shellcheck shell=bash
# ops-common.sh — общая часть скриптов эксплуатации (этап 4 п.4.3).
# Сам по себе не запускается: его подключают backup.sh, restore-check.sh, restore.sh.
#
# Здесь живёт ровно то, что иначе разъехалось бы между тремя скриптами: как найти
# стенд, как прочитать и ЗАПИСАТЬ настройку в файл окружения (чтобы заказчику не
# приходилось править файлы руками), как снять дамп и как его проверить.

OPS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
note() { printf '  %s\n' "$*"; }

DKR="docker"

need_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    err "Docker на этой машине не найден."
    note "Система работает в контейнерах, без Docker бэкап снять нечем."
    note "Поставить: ./install.sh (он ставит Docker сам)."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
      DKR="sudo docker"
    else
      err "Docker установлен, но не отвечает (демон не запущен или нет прав)."
      note "Запустить: sudo systemctl start docker"
      note "Права без sudo: sudo usermod -aG docker \$USER, затем перезайти в систему."
      exit 1
    fi
  fi
}

# ── Стенд: препрод (по умолчанию) или дев ────────────────────────────────────
# Дев-режим нужен, чтобы эти же скрипты можно было проверить на машине
# разработчика, не трогая рабочий стенд.

use_stack() {  # $1 = preprod|dev
  STACK="$1"
  if [[ "$STACK" == "dev" ]]; then
    ENV_FILE=""
    COMPOSE_FILE="$OPS_ROOT/backend/docker-compose.dev.yml"
    PG_USER="tabel"; PG_DB="tabel"
    DC() { $DKR compose -f "$COMPOSE_FILE" "$@"; }
    DEFAULT_BACKUP_DIR="$HOME/backups/zemlya-tabel-dev"
  else
    ENV_FILE="$OPS_ROOT/.env.preprod"
    COMPOSE_FILE="$OPS_ROOT/docker-compose.preprod.yml"
    if [[ ! -f "$ENV_FILE" ]]; then
      err "Не найден файл настроек $ENV_FILE — похоже, препрод на этой машине не устанавливали."
      note "Первая установка: ./install.sh"
      # Подсказку про дев-стенд показывают только те скрипты, где он есть
      # (бэкап и восстановление); у enable-https дев-режима нет.
      [[ "${SUPPORTS_DEV:-0}" == "1" ]] && note "Проверить скрипт на дев-стенде: добавь флаг --dev"
      exit 1
    fi
    PG_USER="$(env_get POSTGRES_USER)"; PG_USER="${PG_USER:-tabel}"
    PG_DB="$(env_get POSTGRES_DB)";     PG_DB="${PG_DB:-tabel}"
    DC() { $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
    DEFAULT_BACKUP_DIR="$HOME/backups/zemlya-tabel"
  fi
}

env_get() {  # $1 = ключ; пусто, если не задан
  [[ -n "${ENV_FILE:-}" && -f "$ENV_FILE" ]] || return 0
  # `|| true`: grep по отсутствующему ключу возвращает 1 и при set -e убивает
  # скрипт прямо на подстановке.
  grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2- || true
}

env_set() {  # $1 = ключ, $2 = значение. Файл правит СКРИПТ, а не человек.
  if [[ -z "${ENV_FILE:-}" ]]; then
    err "На дев-стенде настройки не сохраняются — задай их флагами команды."
    return 1
  fi
  # Правку делает python, а не sed: в значении бывают «/», «&» и прочее, что
  # sed истолковал бы по-своему и молча испортил файл настроек.
  KEY="$1" VALUE="$2" FILE="$ENV_FILE" python3 - <<'PYEOF'
import os, pathlib
key, value, path = os.environ["KEY"], os.environ["VALUE"], pathlib.Path(os.environ["FILE"])
lines = path.read_text().splitlines() if path.exists() else []
for i, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[i] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")
PYEOF
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

# Настройки: переменная окружения (если задана явно при запуске) → файл
# окружения стенда → значение по умолчанию. Окружение первым — чтобы разово
# проверить другое хранилище, не переписывая настройку стенда.

backup_dir() {
  local dir; dir="${BACKUP_DIR:-$(env_get BACKUP_DIR)}"
  printf '%s' "${dir:-$DEFAULT_BACKUP_DIR}"
}

backup_keep() {
  local keep; keep="${BACKUP_KEEP:-$(env_get BACKUP_KEEP)}"
  printf '%s' "${keep:-10}"
}

backup_remote() {
  local r; r="${BACKUP_REMOTE:-$(env_get BACKUP_REMOTE)}"
  printf '%s' "${r:-}"
}

# ── База ─────────────────────────────────────────────────────────────────────

db_up() {
  # grep -c, а не -q: `-q` закрывает канал на первом совпадении, `docker compose`
  # ловит SIGPIPE, и при set -o pipefail запущенная база выглядела бы
  # остановленной. Тот же капкан, что обойдён в make_dump (нашло ревью).
  local running; running="$(DC ps --status running 2>/dev/null | grep -c '\bdb\b' || true)"
  [[ "${running:-0}" -ge 1 ]]
}

require_db() {
  if ! db_up; then
    err "Контейнер базы данных не запущен."
    if [[ "$STACK" == "dev" ]]; then
      note "Поднять: docker compose -f backend/docker-compose.dev.yml up -d"
    else
      note "Поднять: docker compose --env-file .env.preprod -f docker-compose.preprod.yml up -d db"
      note "Или целиком обновить стенд: ./deploy.sh"
    fi
    exit 1
  fi
  if ! DC exec -T db pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    err "База запущена, но не отвечает на запросы (pg_isready). Подожди полминуты и повтори."
    exit 1
  fi
}

psql_db() {  # $1 = база, дальше SQL со stdin
  DC exec -T db psql -U "$PG_USER" -d "$1" -v ON_ERROR_STOP=1 -q
}

psql_value() {  # $1 = база, $2 = запрос → одно значение (пусто, если запрос не прошёл)
  # `|| true`: при set -e + pipefail упавший запрос убил бы скрипт прямо на
  # подстановке, вместо того чтобы дать вызывающему сказать, что не так.
  DC exec -T db psql -U "$PG_USER" -d "$1" -tAc "$2" 2>/dev/null | tr -d '\r' || true
}

# Снять дамп и ПРОВЕРИТЬ его. Пустой или оборванный файл бэкапом не считается.
make_dump() {  # $1 = путь к .sql.gz
  local out="$1"
  mkdir -p "$(dirname "$out")"
  if ! DC exec -T db pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$out"; then
    rm -f "$out"
    err "pg_dump не отработал — дамп не снят."
    return 1
  fi
  if [[ ! -s "$out" ]]; then
    rm -f "$out"
    err "Дамп получился пустым — это не бэкап, файл удалён."
    return 1
  fi
  if ! gzip -t "$out" 2>/dev/null; then
    rm -f "$out"
    err "Дамп повреждён (не распаковывается) — файл удалён."
    return 1
  fi
  # grep -c, а не grep -q: с `-q` grep закрывает канал на первом совпадении,
  # gunzip получает SIGPIPE, и при `set -o pipefail` УДАЧНАЯ проверка выглядит
  # как провал — на этом уже поймались.
  local tables; tables=$(gunzip -c "$out" | grep -c 'CREATE TABLE' || true)
  if [[ "${tables:-0}" -eq 0 ]]; then
    rm -f "$out"
    err "В дампе нет ни одной таблицы — выгрузка неполная, файл удалён."
    return 1
  fi
  note "В дампе таблиц: $tables"
  return 0
}

latest_dump() {  # печатает путь к самому свежему дампу или пусто
  local dir; dir="$(backup_dir)"
  ls -1t "$dir"/tabel_*.sql.gz 2>/dev/null | head -n1 || true
}

human_size() { du -h "$1" 2>/dev/null | cut -f1; }

human_age() {  # $1 = файл → «3 ч 20 мин назад»
  local now file_ts diff
  now=$(date +%s); file_ts=$(date -r "$1" +%s 2>/dev/null || echo "$now")
  diff=$(( now - file_ts ))
  if   (( diff < 3600 ));  then printf '%d мин назад' $(( diff / 60 ))
  elif (( diff < 86400 )); then printf '%d ч %d мин назад' $(( diff / 3600 )) $(( (diff % 3600) / 60 ))
  else printf '%d дн назад' $(( diff / 86400 ))
  fi
}
