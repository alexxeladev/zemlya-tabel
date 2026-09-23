#!/usr/bin/env bash
# restore.sh — ВОССТАНОВЛЕНИЕ рабочей базы из бэкапа. Этап 4 п.4.3.
#
#   ./scripts/restore.sh                      восстановить из самого свежего бэкапа
#   ./scripts/restore.sh путь/к/файлу.sql.gz  восстановить из конкретного
#   ./scripts/restore.sh --yes                не переспрашивать (для аварийного сценария)
#   ./scripts/restore.sh --dev                дев-стенд вместо препрода
#
# Операция ЗАМЕЩАЮЩАЯ: текущее содержимое базы будет заменено содержимым бэкапа.
# Всё, что внесли после снятия бэкапа, пропадёт. Поэтому скрипт:
#   1) сначала снимает страховочный дамп ТЕКУЩЕЙ базы (его не удаляет ротация);
#   2) проверяет, что выбранный бэкап вообще разворачивается (restore-check.sh);
#   3) останавливает приложение, чтобы никто не писал в базу во время замены;
#   4) заменяет базу и ГАСИТ КЭШ ДАШБОРДА;
#   5) поднимает приложение и дожидается готовности (/ready).
#
# Про кэш: дашборд хранит помесячные итоги в таблицах `dashboard_month_cache` и
# `data_versions`. В дампе они едут вместе с данными и им соответствуют, но
# приложение, продолжающее работать во время подмены базы, показывало бы
# посчитанное по ДРУГИМ данным. Поэтому кэш гасится явно, а приложение
# перезапускается — на старте оно сбрасывает кэш ещё раз.
set -euo pipefail

SUPPORTS_DEV=1   # у этого скрипта есть флаг --dev (см. ops-common.sh)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ops-common.sh"

STACK_ARG="preprod"; DUMP_ARG=""; ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)     STACK_ARG="dev" ;;
    --yes|-y)  ASSUME_YES=1 ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*)        err "Не понимаю аргумент «$1»"; exit 1 ;;
    *)         DUMP_ARG="$1" ;;
  esac
  shift
done

need_docker
use_stack "$STACK_ARG"
require_db

DUMP="${DUMP_ARG:-$(latest_dump)}"
[[ -n "$DUMP" ]] || { err "Бэкапов нет — восстанавливать не из чего."; exit 1; }
[[ -f "$DUMP" ]] || { err "Файл не найден: $DUMP"; exit 1; }

say "Восстановление базы «$PG_DB» ($STACK)"
note "Из файла: $DUMP ($(human_size "$DUMP"), снят $(human_age "$DUMP"))"
note "Сейчас в базе: сотрудников $(psql_value "$PG_DB" 'select count(*) from employees'), " \
     "ячеек табеля $(psql_value "$PG_DB" 'select count(*) from timesheet_entries')"

if [[ "$ASSUME_YES" != "1" ]]; then
  warn "Содержимое базы будет ЗАМЕНЕНО. Всё, что внесли после $(human_age "$DUMP"), пропадёт."
  printf '  Напиши ВОССТАНОВИТЬ и нажми Enter (или Ctrl+C, чтобы отменить): '
  read -r answer
  [[ "$answer" == "ВОССТАНОВИТЬ" ]] || { note "Отменено, ничего не изменилось."; exit 1; }
fi

# 1. Страховка: дамп текущего состояния. Имя отличается от обычных бэкапов,
#    поэтому ротация его не удалит.
say "Снимаю страховочный дамп текущей базы"
SAFETY="$(backup_dir)/before_restore_$(date +%Y%m%d_%H%M%S).sql.gz"
make_dump "$SAFETY" || { err "Страховочный дамп не снялся — восстановление отменено."; exit 1; }
ok "Страховка: $SAFETY"
note "Если восстановление окажется ошибкой — вернуться: ./scripts/restore.sh $SAFETY"

# 2. Бэкап действительно разворачивается.
say "Проверяю выбранный бэкап перед заменой"
if ! "$OPS_ROOT/scripts/restore-check.sh" ${STACK_ARG:+$([[ "$STACK_ARG" == "dev" ]] && echo --dev)} "$DUMP"; then
  err "Выбранный бэкап не прошёл проверку — базу не трогаю."
  exit 1
fi

# 3. Приложение останавливаем: пока оно пишет, базу не подменить (и её нельзя
#    удалить — Postgres не даст при открытых соединениях).
say "Останавливаю приложение"
DC stop backend web >/dev/null 2>&1 || true
ok "Остановлено (пользователи сейчас видят ошибку — это ожидаемо)"

# 4. Замена базы.
say "Заменяю базу содержимым бэкапа"
psql_db postgres <<SQL >/dev/null
select pg_terminate_backend(pid) from pg_stat_activity where datname = '$PG_DB' and pid <> pg_backend_pid();
drop database if exists "$PG_DB";
create database "$PG_DB";
SQL

if ! gunzip -c "$DUMP" | psql_db "$PG_DB" >/dev/null; then
  err "Восстановление НЕ завершилось. База сейчас в неполном состоянии."
  note "Вернуть как было: ./scripts/restore.sh $SAFETY"
  DC start backend web >/dev/null 2>&1 || true
  exit 1
fi
ok "Данные восстановлены"

# 5. Кэш дашборда.
say "Гашу кэш дашборда"
# Через DO-блок, а не голым truncate: в дампе, снятом до миграции b6c7d8e9f0a1,
# таблицы кэша ещё нет. Голый truncate упал бы под ON_ERROR_STOP уже ПОСЛЕ
# подмены базы — приложение осталось бы погашенным, а человек увидел бы ошибку
# psql вместо объяснения. Нашло ревью.
psql_db "$PG_DB" <<'SQL' >/dev/null
do $$
begin
  if to_regclass('public.dashboard_month_cache') is not null then
    execute 'truncate table dashboard_month_cache';
  end if;
end $$;
SQL
ok "Кэш пуст — дашборд пересчитает месяцы по восстановленным данным"

# 6. Подъём и готовность.
say "Поднимаю приложение"
DC start backend web >/dev/null 2>&1 || DC up -d >/dev/null 2>&1 || true

if [[ "$STACK_ARG" != "dev" ]]; then
  for i in $(seq 1 30); do
    # Спрашиваем backend НАПРЯМУЮ, а не через nginx: при включённом HTTPS на
    # 80-м порту стоит редирект на 443, и проверка получала бы 301 вместо
    # ответа готовности (нашло ревью).
    if DC exec -T web wget -q -O /dev/null http://backend:8000/ready 2>/dev/null; then
      ok "Приложение готово (база отвечает, версия схемы совпадает с кодом)"
      break
    fi
    if [[ $i -eq 30 ]]; then
      err "Приложение не отвечает готовностью за 30 с."
      note "Скорее всего, бэкап старше текущего кода и нужны миграции:"
      note "  docker compose --env-file .env.preprod -f docker-compose.preprod.yml run --rm backend alembic upgrade head"
      note "Посмотреть причину: docker compose --env-file .env.preprod -f docker-compose.preprod.yml exec -T web wget -qO- http://backend:8000/ready"
      exit 1
    fi
    sleep 1
  done
fi

echo
ok "Восстановление завершено."
note "Сотрудников в базе: $(psql_value "$PG_DB" 'select count(*) from employees')"
note "Ячеек табеля:       $(psql_value "$PG_DB" 'select count(*) from timesheet_entries')"
note "Состояние до восстановления сохранено: $SAFETY"
