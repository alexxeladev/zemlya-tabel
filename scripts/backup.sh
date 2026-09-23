#!/usr/bin/env bash
# backup.sh — бэкап базы «Табеля»: снять, проверить, увезти на внешнее
# хранилище, удалить лишнее по ротации. Этап 4 п.4.3.
#
#   ./scripts/backup.sh                     снять бэкап сейчас
#   ./scripts/backup.sh --status            что настроено и когда был последний бэкап
#   ./scripts/backup.sh --schedule          включить ежедневный бэкап (03:30) и
#                                           еженедельную проверку восстановления (вс 04:30)
#   ./scripts/backup.sh --schedule 02:00    то же, но в своё время
#   ./scripts/backup.sh --no-schedule       выключить расписание
#   ./scripts/backup.sh --where КУДА        куда увозить копию (см. ниже)
#   ./scripts/backup.sh --keep 30           сколько бэкапов хранить (по умолчанию 10)
#   ./scripts/backup.sh --dev               работать с дев-стендом, а не с препродом
#
# КУДА (--where), настройка сохраняется в .env.preprod, файлы руками править не надо:
#   local                       только на этом сервере (по умолчанию)
#   dir:/mnt/backup/tabel       внешний диск или сетевая папка, примонтированная к серверу
#   ssh:user@host:/srv/backup   другой сервер по SSH (нужен ключ без пароля)
#   s3:bucket/prefix            S3-совместимое хранилище (нужен aws cli и ключи доступа)
#
# Повторный запуск ничего не ломает: расписание не задваивается, уже снятые
# бэкапы не трогаются, настройки не сбрасываются.
set -euo pipefail

SUPPORTS_DEV=1   # у этого скрипта есть флаг --dev (см. ops-common.sh)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ops-common.sh"

MODE="run"; STACK_ARG="preprod"; SCHEDULE_AT="03:30"; NEW_WHERE=""; NEW_KEEP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --status)      MODE="status" ;;
    --schedule)    MODE="schedule"
                   [[ "${2:-}" =~ ^[0-2][0-9]:[0-5][0-9]$ ]] && { SCHEDULE_AT="$2"; shift; } ;;
    --no-schedule) MODE="unschedule" ;;
    # Значение обязательно: без него `shift` на пустом списке возвращал 1, и
    # скрипт умирал от set -e БЕЗ ЕДИНОГО СООБЩЕНИЯ (нашло ревью).
    --where)       [[ -n "${2:-}" ]] || { err "После --where нужно указать, куда увозить копию (local, dir:/путь, ssh:user@host:/путь, s3:бакет)."; exit 1; }
                   NEW_WHERE="$2"; shift ;;
    --keep)        [[ -n "${2:-}" ]] || { err "После --keep нужно указать число бэкапов, например: --keep 30"; exit 1; }
                   NEW_KEEP="$2"; shift ;;
    --dev)         STACK_ARG="dev" ;;
    -h|--help)     sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) err "Не понимаю аргумент «$1». Подсказка: ./scripts/backup.sh --help"; exit 1 ;;
  esac
  shift
done

need_docker
use_stack "$STACK_ARG"

# ── Настройки, которые меняет сам скрипт ─────────────────────────────────────

if [[ -n "$NEW_WHERE" ]]; then
  case "$NEW_WHERE" in
    local|none) env_set BACKUP_REMOTE ""; ok "Копия будет храниться только на этом сервере." ;;
    dir:*|ssh:*|s3:*)
      env_set BACKUP_REMOTE "$NEW_WHERE"
      ok "Внешнее хранилище: $NEW_WHERE"
      note "Проверить, что копия туда доезжает: ./scripts/backup.sh" ;;
    *) err "Не понимаю «$NEW_WHERE». Допустимо: local, dir:/путь, ssh:user@host:/путь, s3:бакет/префикс"; exit 1 ;;
  esac
fi
if [[ -n "$NEW_KEEP" ]]; then
  [[ "$NEW_KEEP" =~ ^[0-9]+$ && "$NEW_KEEP" -ge 1 ]] || { err "--keep ждёт число не меньше 1, получил «$NEW_KEEP»"; exit 1; }
  env_set BACKUP_KEEP "$NEW_KEEP"
  ok "Храню последние $NEW_KEEP бэкапов."
fi

DIR="$(backup_dir)"; KEEP="$(backup_keep)"; REMOTE="$(backup_remote)"
CRON_MARK="# zemlya-tabel backup"
# Строку PATH cron не даёт пометить комментарием (он ушёл бы в значение),
# поэтому при переустановке она вычищается по точному совпадению — иначе
# каждый повторный запуск добавлял бы её заново.
CRON_PATH_LINE="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ── Внешняя копия ────────────────────────────────────────────────────────────
# Каждая ветка сама говорит, чего ей не хватает: «нет rsync», «ключ не подошёл»,
# «не настроены ключи доступа» — вместо кода возврата непонятной утилиты.

# Копия считается уехавшей, только если на том конце файл ТОГО ЖЕ размера.
# Код возврата `cp`/`rsync`/`aws` этого не доказывает: оборванная передача,
# заполнившийся диск и «тихий» сетевой сбой дают ноль на выходе и обрезанный
# файл на месте (нашло ревью).
remote_size_ok() {  # $1 = локальный файл, $2 = размер на той стороне, $3 = куда
  local local_size; local_size="$(stat -c %s "$1" 2>/dev/null || echo 0)"
  if [[ -z "${2:-}" || "$2" == "0" ]]; then
    err "Копия в $3 не найдена после отправки — бэкап наружу НЕ уехал."
    return 1
  fi
  if [[ "$2" != "$local_size" ]]; then
    err "Копия в $3 отличается по размеру: здесь $local_size байт, там $2 — передача оборвалась."
    return 1
  fi
  return 0
}

copy_out() {  # $1 = файл дампа
  local file="$1" target="${REMOTE}"
  [[ -z "$target" ]] && return 0
  case "$target" in
    dir:*)
      local path="${target#dir:}"
      if [[ ! -d "$path" ]]; then
        err "Папка внешнего хранилища не найдена: $path"
        note "Если это внешний диск или сетевая папка — проверь, что она примонтирована."
        return 1
      fi
      # Пустая точка монтирования = диск отвалился. Писать туда — значит
      # складывать бэкапы в корневой диск и думать, что они снаружи.
      if ! mountpoint -q "$path" 2>/dev/null && [[ -z "$(ls -A "$path" 2>/dev/null)" ]]; then
        warn "Папка $path пуста и не является точкой монтирования — убедись, что диск на месте."
      fi
      cp "$file" "$path/" || { err "Не удалось скопировать бэкап в $path (нет прав?)"; return 1; }
      remote_size_ok "$file" "$(stat -c %s "$path/$(basename "$file")" 2>/dev/null || echo 0)" "$path" || return 1
      ls -1t "$path"/tabel_*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f || true
      ok "Копия увезена и совпала по размеру: $path"
      ;;
    ssh:*)
      local dest="${target#ssh:}"
      command -v rsync >/dev/null 2>&1 || { err "Нужен rsync: sudo apt install rsync"; return 1; }
      if ! rsync -q -e "ssh -o BatchMode=yes -o ConnectTimeout=15" "$file" "$dest/"; then
        err "Не удалось отправить бэкап на $dest"
        note "Чаще всего: не заведён SSH-ключ без пароля. Завести — ssh-keygen -t ed25519,"
        note "затем ssh-copy-id ${dest%%:*}. Проверить руками: ssh ${dest%%:*} ls"
        return 1
      fi
      local host="${dest%%:*}" path="${dest#*:}"
      # stat -c — GNU, stat -f — BSD/macOS, wc -c — везде. Без запасных
      # вариантов успешно уехавший бэкап на не-Linux приёмнике объявлялся бы
      # неуехавшим (нашло ревью).
      remote_size_ok "$file" \
        "$(ssh -o BatchMode=yes "$host" \
            "stat -c %s '$path/$(basename "$file")' 2>/dev/null || \
             stat -f %z '$path/$(basename "$file")' 2>/dev/null || \
             wc -c < '$path/$(basename "$file")' 2>/dev/null || echo 0")" \
        "$dest" || return 1
      ssh -o BatchMode=yes "$host" \
        "ls -1t '$path'/tabel_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f" || true
      ok "Копия увезена и совпала по размеру: $dest"
      ;;
    s3:*)
      local bucket="${target#s3:}"
      command -v aws >/dev/null 2>&1 || { err "Нужен aws cli: sudo apt install awscli"; return 1; }
      local endpoint=""; local ep; ep="$(env_get BACKUP_S3_ENDPOINT)"
      [[ -n "$ep" ]] && endpoint="--endpoint-url $ep"
      # shellcheck disable=SC2086
      if ! aws $endpoint s3 cp "$file" "s3://$bucket/" >/dev/null; then
        err "Не удалось загрузить бэкап в s3://$bucket"
        note "Проверь ключи доступа в .env.preprod (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)"
        note "и адрес хранилища BACKUP_S3_ENDPOINT, если оно не Amazon."
        return 1
      fi
      # shellcheck disable=SC2086
      remote_size_ok "$file" \
        "$(aws $endpoint s3 ls "s3://$bucket/$(basename "$file")" | awk 'NR==1 {print $3}')" \
        "s3://$bucket" || return 1
      # shellcheck disable=SC2086
      aws $endpoint s3 ls "s3://$bucket/" | awk '{print $4}' | grep '^tabel_.*\.sql\.gz$' \
        | sort -r | tail -n +"$((KEEP + 1))" \
        | while read -r old; do aws $endpoint s3 rm "s3://$bucket/$old" >/dev/null; done || true
      ok "Копия увезена и совпала по размеру: s3://$bucket"
      ;;
  esac
}

# ── Расписание (cron) ────────────────────────────────────────────────────────

show_schedule() {
  crontab -l 2>/dev/null | grep -F "$CRON_MARK" || true
}

install_schedule() {
  command -v crontab >/dev/null 2>&1 || {
    err "На сервере нет cron — расписание ставить нечем."
    note "Поставить: sudo apt install cron && sudo systemctl enable --now cron"
    exit 1
  }
  local hh="${SCHEDULE_AT%%:*}" mm="${SCHEDULE_AT##*:}"
  # Флаг стенда уезжает в расписание: иначе задание, заведённое для дев-стенда,
  # каждую ночь ходило бы в препрод (или наоборот).
  local flag=""; [[ "$STACK" == "dev" ]] && flag=" --dev"
  local self="$OPS_ROOT/scripts/backup.sh$flag" check="$OPS_ROOT/scripts/restore-check.sh$flag"
  local logdir; logdir="$DIR"
  mkdir -p "$logdir"
  # Старые записи убираем и ставим заново — поэтому повторный запуск не плодит дубли.
  {
    crontab -l 2>/dev/null | grep -vF "$CRON_MARK" | grep -vxF "$CRON_PATH_LINE" || true
    printf '%s\n' "$CRON_PATH_LINE"
    printf '%s %s * * * %s >> %s/backup.log 2>&1 %s\n' "$mm" "$hh" "$self" "$logdir" "$CRON_MARK"
    printf '30 4 * * 0 %s >> %s/restore-check.log 2>&1 %s\n' "$check" "$logdir" "$CRON_MARK"
  } | crontab -
  ok "Расписание включено."
  note "Бэкап — каждый день в $SCHEDULE_AT, журнал: $logdir/backup.log"
  note "Проверка восстановления — по воскресеньям в 04:30, журнал: $logdir/restore-check.log"
}

remove_schedule() {
  command -v crontab >/dev/null 2>&1 || { warn "cron не установлен — выключать нечего."; return 0; }
  crontab -l 2>/dev/null | grep -vF "$CRON_MARK" | grep -vxF "$CRON_PATH_LINE" | crontab - || true
  ok "Расписание выключено. Бэкапы больше не снимаются сами."
}

# ── Состояние ────────────────────────────────────────────────────────────────

status() {
  say "Бэкапы «Табеля» — состояние"
  note "Стенд:            $STACK"
  note "Папка бэкапов:    $DIR"
  note "Хранить копий:    $KEEP"
  note "Внешняя копия:    ${REMOTE:-нет (только этот сервер)}"
  if [[ -z "$REMOTE" ]]; then
    warn "Бэкапы лежат рядом с системой. Сгорит сервер — сгорят и они."
    note "Включить внешнюю копию: ./scripts/backup.sh --where dir:/mnt/backup"
    note "                        ./scripts/backup.sh --where ssh:user@host:/srv/backup"
    note "                        ./scripts/backup.sh --where s3:bucket/tabel"
  fi

  local last count
  last="$(latest_dump)"
  count=$(ls -1 "$DIR"/tabel_*.sql.gz 2>/dev/null | wc -l | tr -d ' ' || true)
  if [[ -z "$last" ]]; then
    warn "Бэкапов нет ни одного. Снять сейчас: ./scripts/backup.sh"
  else
    note "Последний бэкап:  $(basename "$last"), $(human_size "$last"), $(human_age "$last")"
    note "Всего бэкапов:    $count"
    local age_days; age_days=$(( ( $(date +%s) - $(date -r "$last" +%s) ) / 86400 ))
    (( age_days >= 2 )) && warn "Свежего бэкапа нет уже $age_days дн — расписание точно работает?"
  fi

  if [[ -n "$(show_schedule)" ]]; then
    note "Расписание:       включено"
    show_schedule | sed 's/^/    /'
  else
    warn "Расписание выключено — бэкапы сами не снимаются."
    note "Включить: ./scripts/backup.sh --schedule"
  fi
  echo
  note "Проверить, что бэкап разворачивается: ./scripts/restore-check.sh"
}

# ── Снятие бэкапа ────────────────────────────────────────────────────────────

run_backup() {
  require_db
  say "Снимаю бэкап базы «$PG_DB» ($STACK)"
  local file="$DIR/tabel_$(date +%Y%m%d_%H%M%S).sql.gz"
  make_dump "$file" || exit 1
  ok "Бэкап снят: $file ($(human_size "$file"))"

  # Ротация локально.
  local removed
  removed=$(ls -1t "$DIR"/tabel_*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | wc -l | tr -d ' ' || true)
  ls -1t "$DIR"/tabel_*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f || true
  note "Храню последние $KEEP; удалено старых: ${removed:-0}"

  if [[ -n "$REMOTE" ]]; then
    say "Увожу копию на внешнее хранилище"
    copy_out "$file" || {
      err "Бэкап снят, но НАРУЖУ НЕ УЕХАЛ — он остался только на этом сервере."
      exit 1
    }
  else
    warn "Внешнее хранилище не настроено — копия осталась на этом же сервере."
    note "Настроить: ./scripts/backup.sh --where dir:/mnt/backup (или ssh:/s3:, см. --help)"
  fi
  echo
  ok "Готово."
}

case "$MODE" in
  status)     status ;;
  schedule)   install_schedule; echo; status ;;
  unschedule) remove_schedule ;;
  run)        run_backup ;;
esac
