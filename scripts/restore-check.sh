#!/usr/bin/env bash
# restore-check.sh — проверка, что бэкап РАЗВОРАЧИВАЕТСЯ. Этап 4 п.4.3.
#
#   ./scripts/restore-check.sh                    проверить самый свежий бэкап
#   ./scripts/restore-check.sh путь/к/файлу.sql.gz  проверить конкретный
#   ./scripts/restore-check.sh --dev              дев-стенд вместо препрода
#
# Что делает: разворачивает дамп во ВРЕМЕННУЮ базу рядом с рабочей, читает из
# неё данные и сверяет с рабочей базой, потом временную базу удаляет. Рабочая
# база не затрагивается ни в каком случае.
#
# Зачем так: «файл создан и непустой» ничего не доказывает. Оборванная выгрузка,
# дамп от другой версии схемы, выгрузка с неполными правами — всё это выглядит
# как нормальный файл и обнаруживается только при разворачивании.
#
# Возвращает 0, если бэкап пригоден, иначе — ненулевой код (для cron).
set -euo pipefail

SUPPORTS_DEV=1   # у этого скрипта есть флаг --dev (см. ops-common.sh)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ops-common.sh"

STACK_ARG="preprod"; DUMP_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)     STACK_ARG="dev" ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*)        err "Не понимаю аргумент «$1»"; exit 1 ;;
    *)         DUMP_ARG="$1" ;;
  esac
  shift
done

need_docker
use_stack "$STACK_ARG"
require_db

DUMP="${DUMP_ARG:-$(latest_dump)}"
if [[ -z "$DUMP" ]]; then
  err "Бэкапов нет — проверять нечего."
  note "Снять первый: ./scripts/backup.sh"
  exit 1
fi
if [[ ! -f "$DUMP" ]]; then
  err "Файл не найден: $DUMP"
  exit 1
fi

CHECK_DB="${PG_DB}_restore_check"
FAILED=0
fail() { err "$*"; FAILED=1; }

cleanup() {
  # Временную базу убираем всегда — в том числе если проверка упала.
  psql_db postgres <<SQL >/dev/null 2>&1 || true
drop database if exists "$CHECK_DB";
SQL
}
trap cleanup EXIT

say "Проверка восстановления"
note "Файл:      $DUMP ($(human_size "$DUMP"), $(human_age "$DUMP"))"
note "Временная база: $CHECK_DB (будет удалена по окончании)"

# 1. Файл вообще распаковывается.
if ! gzip -t "$DUMP" 2>/dev/null; then
  err "Файл повреждён: не распаковывается. Восстановиться из него НЕЛЬЗЯ."
  exit 1
fi

# 2. Разворачиваем в отдельную базу.
say "Разворачиваю дамп во временную базу"
psql_db postgres <<SQL >/dev/null
drop database if exists "$CHECK_DB";
create database "$CHECK_DB";
SQL

if ! gunzip -c "$DUMP" | psql_db "$CHECK_DB" >/dev/null; then
  err "Дамп не развернулся — восстановиться из этого файла не получится."
  note "Причина в выводе выше. Чаще всего: дамп снят от другой версии Postgres"
  note "или оборван (закончилось место на диске во время бэкапа)."
  exit 1
fi
ok "Дамп развернулся"

# 3. Читаем данные. Не «таблицы есть», а именно строки: пустая, но валидная
#    схема развернётся без единой ошибки и бэкапом при этом не является.
say "Читаю данные из восстановленной базы"

TABLES=(employees departments companies employee_positions timesheet_entries timesheet_periods)
for t in "${TABLES[@]}"; do
  restored="$(psql_value "$CHECK_DB" "select count(*) from $t")"
  live="$(psql_value "$PG_DB" "select count(*) from $t")"
  if [[ -z "$restored" ]]; then
    fail "Таблица $t в восстановленной базе не читается"
    continue
  fi
  printf '  %-22s восстановлено %-8s в рабочей базе %s\n' "$t:" "$restored" "${live:-?}"
done

# Сотрудники — главный признак «данные на месте»: система без них бессмысленна.
people="$(psql_value "$CHECK_DB" "select count(*) from employees")"
if [[ "${people:-0}" -eq 0 ]]; then
  fail "В восстановленной базе НЕТ НИ ОДНОГО сотрудника — бэкап пустой по сути"
fi

# Деньги читаются и остаются числами (а не текстом и не NULL после переноса).
money="$(psql_value "$CHECK_DB" "select coalesce(sum(rate), 0)::text from employee_positions")"
[[ -n "$money" ]] || fail "Суммы в восстановленной базе не читаются"
note "Сумма окладов в восстановленной базе: ${money:-?}"

# Версия схемы: дамп со старой схемой развернётся, но приложение на него не сядет.
rev_restored="$(psql_value "$CHECK_DB" "select version_num from alembic_version")"
rev_live="$(psql_value "$PG_DB" "select version_num from alembic_version")"
if [[ -z "$rev_restored" ]]; then
  fail "В дампе нет отметки версии схемы (alembic_version) — приложение на такую базу не поднимется"
elif [[ "$rev_restored" != "$rev_live" ]]; then
  warn "Версия схемы в бэкапе ($rev_restored) отличается от рабочей ($rev_live)."
  note "Это нормально для старого бэкапа: после восстановления надо выполнить миграции."
else
  note "Версия схемы: $rev_restored — совпадает с рабочей"
fi

# Уникальные индексы периодов: без них в восстановленной базе заведутся
# два периода одного месяца, и месяц будет закрываться дважды.
idx="$(psql_value "$CHECK_DB" "select count(*) from pg_indexes where tablename = 'timesheet_periods' and indexname like 'uq_period%'")"
[[ "${idx:-0}" -ge 2 ]] || fail "В восстановленной базе нет уникальных индексов периодов (найдено ${idx:-0} из 2)"

# Кэш дашборда — обычные таблицы, они едут в дампе вместе с данными.
cache="$(psql_value "$CHECK_DB" "select count(*) from dashboard_month_cache")"
note "Строк кэша дашборда в бэкапе: ${cache:-0} (после восстановления он гасится — см. restore.sh)"

echo
if [[ "$FAILED" == "0" ]]; then
  ok "Бэкап пригоден: развернулся в отдельную базу, данные читаются."
  note "Проверен файл: $DUMP"
  exit 0
fi
err "Бэкап НЕ прошёл проверку — восстановиться из него, скорее всего, не выйдет."
note "Сними свежий и проверь снова: ./scripts/backup.sh && ./scripts/restore-check.sh"
exit 1
