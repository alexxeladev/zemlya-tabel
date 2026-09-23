#!/usr/bin/env bash
# verify-clean-install.sh — проверка п.4.1 этапа 4: воспроизводима ли установка.
#
#   ./scripts/verify-clean-install.sh              проверить бэкенд и фронтенд
#   ./scripts/verify-clean-install.sh --backend    только бэкенд
#   ./scripts/verify-clean-install.sh --frontend   только фронтенд
#   ./scripts/verify-clean-install.sh --refresh    пересобрать backend/requirements*.lock
#                                                  из pyproject.toml и прогнать тесты на новом составе
#   ./scripts/verify-clean-install.sh --quick      подмножество тестов вместо полного набора (~1 мин)
#
# Что делает: ставит зависимости С НУЛЯ в ЧИСТОМ контейнере того же образа, из
# которого собирается прод (FROM читается из backend/Dockerfile — не задан здесь
# отдельно, иначе разъедется), и прогоняет тесты. Рабочее дерево не трогается:
# исходники копируются в контейнер, node_modules и .venv не задействованы.
#
# Нужен доступ в интернет (pip и npm тянут пакеты). Нет доступа — скрипт скажет
# об этом прямо, а не упадёт непонятной ошибкой.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
err()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
note() { printf '  %s\n' "$*"; }

DO_BACKEND=1; DO_FRONTEND=1; REFRESH=0; QUICK=0
for arg in "$@"; do
  case "$arg" in
    --backend)  DO_FRONTEND=0 ;;
    --frontend) DO_BACKEND=0 ;;
    --refresh)  REFRESH=1 ;;
    --quick)    QUICK=1 ;;
    -h|--help)  sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) err "Неизвестный аргумент: $arg (см. --help)"; exit 1 ;;
  esac
done

DKR="docker"
have_docker() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 && return 0
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then DKR="sudo docker"; return 0; fi
  return 1
}

# Образ берём из Dockerfile: проверять состав в другом Python бессмысленно —
# версии пакетов зависят от версии интерпретатора.
image_from_dockerfile() {  # $1 = путь к Dockerfile
  grep -m1 -E '^FROM ' "$1" | awk '{print $2}'
}

ensure_image() {  # $1 = образ
  $DKR image inspect "$1" >/dev/null 2>&1 && return 0
  note "Образа $1 нет локально, тяну из реестра…"
  if ! $DKR pull "$1" >/dev/null 2>&1; then
    err "Не удалось скачать образ $1."
    err "Похоже, с этой машины нет доступа к реестру Docker (registry-1.docker.io)."
    err "Проверить: curl -sI https://registry-1.docker.io/v2/"
    err "Проверка чистой установки без сети невозможна — запусти её там, где сеть есть"
    err "(или дождись CI: .github/workflows/ci.yml делает ровно это на каждый push)."
    return 1
  fi
}

# ── Бэкенд ────────────────────────────────────────────────────────────────────
backend_check() {
  local img; img="$(image_from_dockerfile "$BACKEND/Dockerfile")"
  say "Бэкенд: чистая установка в $img"
  have_docker || { err "docker недоступен — без него чистую установку не изолировать."; return 1; }
  ensure_image "$img" || return 1

  local pytest_args="-q"
  [[ "$QUICK" == "1" ]] && pytest_args="-q tests/test_payroll.py tests/test_work_schedule.py tests/test_timesheet.py"

  local out; out="$(mktemp -d)"
  trap 'rm -rf "$out"' RETURN

  if [[ "$REFRESH" == "1" ]]; then
    say "Режим --refresh: состав собирается заново из pyproject.toml"
    $DKR run --rm -v "$BACKEND:/src:ro" -v "$out:/out" "$img" bash -c '
      set -e
      mkdir -p /work && cp -r /src/app /src/tests /src/alembic /src/alembic.ini /src/pyproject.toml /work/
      cd /work
      pip install -q --no-cache-dir .        > /dev/null
      pip freeze --exclude-editable | grep -v "^zemlya-tabel-backend" | sort > /out/runtime.txt
      pip install -q --no-cache-dir ".[dev]" > /dev/null
      pip freeze --exclude-editable | grep -v "^zemlya-tabel-backend" | sort > /out/dev.txt
      python -c "import sys; print(\"%d.%d.%d\" % sys.version_info[:3])" > /out/pyver.txt
    ' || { err "Установка из pyproject.toml не прошла (скорее всего нет сети до PyPI)."; return 1; }

    local pyver; pyver="$(cat "$out/pyver.txt")"
    {
      printf '# Закреплённый состав ПРОДА (runtime) — этап 4 п.4.1.\n#\n'
      printf '# Собран: scripts/verify-clean-install.sh --refresh\n'
      printf '# Образ: %s, Python %s, дата %s\n' "$img" "$pyver" "$(date +%Y-%m-%d)"
      printf '# Диапазоны версий — в pyproject.toml («что нужно»); здесь — «что именно стоит».\n'
      printf '# Ставить: pip install -r requirements.lock && pip install --no-deps .\n\n'
      cat "$out/runtime.txt"
    } > "$BACKEND/requirements.lock"
    {
      printf '# Состав для РАЗРАБОТКИ И ТЕСТОВ = прод + инструменты (этап 4 п.4.1).\n#\n'
      printf '# Собран: scripts/verify-clean-install.sh --refresh, %s\n' "$(date +%Y-%m-%d)"
      printf '# Прод-часть подключается строкой ниже, а не копируется: два списка\n'
      printf '# одних и тех же пакетов разъехались бы.\n\n'
      printf -- '-r requirements.lock\n\n'
      printf '# ── Инструменты (в прод-образ не попадают) ──\n'
      comm -13 "$out/runtime.txt" "$out/dev.txt"
    } > "$BACKEND/requirements-dev.lock"
    ok "Переписаны backend/requirements.lock и requirements-dev.lock"
  fi

  say "Ставлю ровно то, что в requirements-dev.lock, и прогоняю тесты"
  note "Полный набор — это ~1100 тестов, минут 20–30. Нужен быстрый ответ — флаг --quick."
  $DKR run --rm -v "$BACKEND:/src:ro" "$img" bash -c "
    set -e
    mkdir -p /work && cp -r /src/app /src/tests /src/alembic /src/alembic.ini /src/pyproject.toml \
                            /src/requirements.lock /src/requirements-dev.lock /work/
    cd /work
    pip install -q --no-cache-dir -r requirements-dev.lock
    pip install -q --no-cache-dir --no-deps .
    pip check
    python -m pytest $pytest_args
  " || { err "Чистая установка бэкенда НЕ прошла — смотри вывод выше."; return 1; }
  ok "Бэкенд: состав из lock-файла ставится с нуля и проходит тесты"
}

# ── Фронтенд ──────────────────────────────────────────────────────────────────
# package-lock.json уже фиксирует весь транзитивный состав, а `npm ci` ставит
# строго по нему и падает при расхождении с package.json. Проверяем именно это:
# установка с нуля + сборка + тесты, в копии дерева (node_modules проекта цел).
frontend_check() {
  say "Фронтенд: npm ci из package-lock.json + сборка + тесты"
  [[ -f "$FRONTEND/package-lock.json" ]] || { err "Нет frontend/package-lock.json — фиксировать нечего."; return 1; }

  if command -v npm >/dev/null 2>&1; then
    # В самом дереве, а не в копии: часть фронт-тестов сверяется с ИСХОДНИКАМИ
    # БЭКА (зеркала расчёта), и в отрыве от репозитория они не находят файлы.
    # `npm ci` и так ставит с нуля: он сносит node_modules и ставит строго по
    # package-lock.json.
    note "node_modules будет переустановлен с нуля (npm ci) — это и есть проверка."
    ( cd "$FRONTEND" && npm ci --no-audit --no-fund && npm run build && npm test ) \
      || { err "Фронтенд: чистая установка/сборка НЕ прошла (или нет сети до registry.npmjs.org)."; return 1; }
  else
    local img; img="$(image_from_dockerfile "$FRONTEND/Dockerfile")"
    have_docker || { err "Нет ни npm, ни docker — нечем проверять фронтенд."; return 1; }
    ensure_image "$img" || return 1
    # Монтируем ВЕСЬ репозиторий: фронт-тесты читают исходники бэка (зеркала).
    $DKR run --rm -v "$ROOT:/repo" -w /repo/frontend "$img" sh -c '
      set -e
      npm ci --no-audit --no-fund && npm run build && npm test
    ' || { err "Фронтенд: чистая установка/сборка НЕ прошла."; return 1; }
  fi
  ok "Фронтенд: package-lock.json ставится с нуля, сборка и тесты проходят"
}

RC=0
[[ "$DO_BACKEND"  == "1" ]] && { backend_check  || RC=1; }
[[ "$DO_FRONTEND" == "1" ]] && { frontend_check || RC=1; }

if [[ "$RC" == "0" ]]; then
  printf '\n\033[1;32m✔ Установка воспроизводима.\033[0m\n'
else
  printf '\n\033[1;31m✗ Проверка не пройдена — см. сообщения выше.\033[0m\n'
fi
exit "$RC"
