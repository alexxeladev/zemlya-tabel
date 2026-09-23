#!/usr/bin/env bash
# enable-https.sh — включить HTTPS на входе в «Табель». Этап 4 п.4.4.
#
# Стек публикует HTTP: пароль и токен идут по сети открытым текстом. Ниже три
# способа это закрыть — выбирается один, остальное скрипт делает сам.
#
#   ./scripts/enable-https.sh --letsencrypt --domain tabel.example.ru --email it@example.ru
#         Бесплатный сертификат Let's Encrypt с автопродлением.
#         Нужно: домен указывает на этот сервер, порты 80 и 443 открыты снаружи.
#
#   ./scripts/enable-https.sh --cert /путь/fullchain.pem --key /путь/privkey.pem
#         Сертификат выдал ваш IT-отдел. Скрипт проверит срок и соответствие
#         ключу, разложит файлы и перезапустит вход.
#
#   ./scripts/enable-https.sh --behind-proxy
#         TLS снимает внешний прокси (Cloudflare, корпоративный балансировщик).
#         Стек останется на HTTP, но слушать будет только сам сервер, чтобы в
#         обход прокси было не зайти.
#
#   ./scripts/enable-https.sh --self-signed --domain tabel.local
#         Свой сертификат для ПРОВЕРКИ. Браузер будет ругаться — для людей не годится.
#
#   ./scripts/enable-https.sh --status     что включено и до какого числа годен сертификат
#   ./scripts/enable-https.sh --renew      продлить Let's Encrypt (ставится в расписание само)
#   ./scripts/enable-https.sh --off        вернуться к обычному HTTP
#
# Повторный запуск не ломает работающее: сертификат перевыпускается, только
# если истекает, расписание не задваивается, настройки не теряются.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ops-common.sh"

MODE=""; DOMAIN=""; EMAIL=""; CERT_IN=""; KEY_IN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --letsencrypt)  MODE="letsencrypt" ;;
    --cert)         MODE="cert"; CERT_IN="${2:-}"; shift ;;
    --key)          KEY_IN="${2:-}"; shift ;;
    --behind-proxy) MODE="proxy" ;;
    --self-signed)  MODE="selfsigned" ;;
    --renew)        MODE="renew" ;;
    --status)       MODE="status" ;;
    --off)          MODE="off" ;;
    --domain)       DOMAIN="${2:-}"; shift ;;
    --email)        EMAIL="${2:-}"; shift ;;
    -h|--help)      sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) err "Не понимаю аргумент «$1». Подсказка: ./scripts/enable-https.sh --help"; exit 1 ;;
  esac
  shift
done
[[ -n "$MODE" ]] || { sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 1; }

need_docker
use_stack preprod

CERT_DIR="$OPS_ROOT/deploy/certs"
CERT="$CERT_DIR/fullchain.pem"
KEY="$CERT_DIR/privkey.pem"
HTTPS_COMPOSE="$OPS_ROOT/docker-compose.https.yml"
PROXY_COMPOSE="$OPS_ROOT/docker-compose.behind-proxy.yml"
CRON_MARK="# zemlya-tabel https"

# Стек с нужными надстройками — тем же способом, каким его поднимает deploy.sh.
DCH() { $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$HTTPS_COMPOSE" "$@"; }
DCP() { $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$PROXY_COMPOSE" "$@"; }

# ── Проверки сертификата ─────────────────────────────────────────────────────

# openssl: с хоста, а если его там нет — из образа бэкенда (он собран на
# python:3.11-slim, openssl в нём есть; в nginx-alpine его НЕТ — проверено).
# Пути в аргументах пишутся как /certs/..., для хостового вызова они
# подставляются в реальный каталог.
ossl() {
  if command -v openssl >/dev/null 2>&1; then
    openssl "${@//\/certs/$CERT_DIR}"
  else
    $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps \
      -v "$CERT_DIR:/certs" --entrypoint openssl backend "$@"
  fi
}

cert_expires_at() { ossl x509 -enddate -noout -in /certs/fullchain.pem 2>/dev/null | cut -d= -f2 || true; }

cert_days_left() {
  local end; end="$(cert_expires_at)"
  [[ -n "$end" ]] || { printf '%s' ""; return; }
  local ts now; ts=$(date -d "$end" +%s 2>/dev/null || echo 0); now=$(date +%s)
  printf '%d' $(( (ts - now) / 86400 ))
}

check_pair() {  # сертификат и ключ — из одной пары?
  local c k
  c="$(ossl x509 -noout -pubkey -in /certs/fullchain.pem 2>/dev/null || true)"
  k="$(ossl pkey -pubout -in /certs/privkey.pem 2>/dev/null || true)"
  [[ -n "$c" && "$c" == "$k" ]]
}

install_cert_files() {  # $1 = сертификат, $2 = ключ
  mkdir -p "$CERT_DIR"
  cp "$1" "$CERT"; cp "$2" "$KEY"
  chmod 700 "$CERT_DIR"; chmod 644 "$CERT"; chmod 600 "$KEY"
  if ! check_pair; then
    err "Сертификат и ключ не подходят друг другу — это разные пары."
    note "Проверьте, что передали именно те файлы: fullchain.pem и privkey.pem от одного выпуска."
    return 1
  fi
  local left; left="$(cert_days_left)"
  if [[ -n "$left" && "$left" -lt 0 ]]; then
    err "Сертификат ПРОСРОЧЕН ($(cert_expires_at)). Включать его нельзя — сайт не откроется."
    return 1
  fi
  [[ -n "$left" ]] && note "Сертификат годен ещё $left дн (до $(cert_expires_at))"
  return 0
}

# ── Применение конфигурации ──────────────────────────────────────────────────

apply_https() {
  say "Перезапускаю вход по HTTPS"
  DCH up -d web >/dev/null

  # Проверка ПОСЛЕ подъёма, а не до: nginx -t разрешает имя backend в
  # compose-сети, и в отдельном контейнере без запущенного стека проверка
  # падала бы на ровном месте.
  say "Проверяю конфигурацию nginx"
  if ! DCH exec -T web nginx -t >/dev/null 2>&1; then
    err "nginx не принял конфигурацию с сертификатом:"
    DCH exec -T web nginx -t 2>&1 | sed 's/^/    /' || true
    warn "Возвращаю обычный HTTP, чтобы система осталась доступной."
    $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d web >/dev/null
    return 1
  fi
  ok "Конфигурация верна"
  env_set HTTPS_MODE "$1"
  [[ -n "$DOMAIN" ]] && env_set HTTPS_DOMAIN "$DOMAIN"
  ok "HTTPS включён"
  note "deploy.sh теперь поднимает стенд вместе с docker-compose.https.yml —"
  note "обновление кода HTTPS не выключит."
  echo
  note "Проверить снаружи: curl -I https://${DOMAIN:-<домен>}/ready"
}

# ── Режимы ───────────────────────────────────────────────────────────────────

# Только выпуск и раскладка файлов, без применения конфигурации: этим же
# пользуется ветка Let's Encrypt, которой нужен временный сертификат.
make_selfsigned_cert() {  # $1 = домен
  mkdir -p "$CERT_DIR"
  local tmp; tmp="$(mktemp -d)"
  if command -v openssl >/dev/null 2>&1; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
      -keyout "$tmp/privkey.pem" -out "$tmp/fullchain.pem" \
      -subj "/CN=$1" -addext "subjectAltName=DNS:$1" >/dev/null 2>&1
  else
    $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps \
      -v "$tmp:/out" --entrypoint openssl backend \
      req -x509 -newkey rsa:2048 -nodes -days 365 \
      -keyout /out/privkey.pem -out /out/fullchain.pem \
      -subj "/CN=$1" -addext "subjectAltName=DNS:$1" >/dev/null 2>&1
  fi
  local rc=0
  install_cert_files "$tmp/fullchain.pem" "$tmp/privkey.pem" || rc=1
  rm -rf "$tmp"
  return "$rc"
}

mode_selfsigned() {
  [[ -n "$DOMAIN" ]] || { err "Нужен --domain: имя, на которое выписать сертификат."; exit 1; }
  warn "Самоподписанный сертификат: браузер будет показывать предупреждение."
  note "Он годится, чтобы проверить настройку, но не для работы людей."
  make_selfsigned_cert "$DOMAIN" || exit 1
  apply_https selfsigned
}

mode_cert() {
  [[ -n "$CERT_IN" && -n "$KEY_IN" ]] || { err "Нужны оба файла: --cert и --key"; exit 1; }
  [[ -f "$CERT_IN" ]] || { err "Не найден файл сертификата: $CERT_IN"; exit 1; }
  [[ -f "$KEY_IN" ]]  || { err "Не найден файл ключа: $KEY_IN"; exit 1; }
  say "Проверяю сертификат"
  install_cert_files "$CERT_IN" "$KEY_IN" || exit 1
  local left; left="$(cert_days_left)"
  if [[ -n "$left" && "$left" -lt 30 ]]; then
    warn "До конца срока $left дн — запросите у IT продление заранее."
  fi
  note "Продление НЕ автоматическое: когда получите новые файлы, повторите эту же команду."
  apply_https cert
}

mode_letsencrypt() {
  [[ -n "$DOMAIN" ]] || { err "Нужен --domain: имя, по которому сервер доступен из интернета."; exit 1; }
  [[ -n "$EMAIL" ]]  || { err "Нужен --email: на него Let's Encrypt пришлёт предупреждение об истечении."; exit 1; }

  say "Проверяю, что домен указывает на этот сервер"
  local resolved; resolved="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -n1 || true)"
  if [[ -z "$resolved" ]]; then
    err "Домен $DOMAIN не разрешается в адрес — Let's Encrypt не сможет его проверить."
    note "Заведите A-запись на адрес этого сервера и повторите через несколько минут."
    exit 1
  fi
  note "$DOMAIN → $resolved"
  note "Если это не адрес ЭТОГО сервера, выпуск не пройдёт: проверка идёт по HTTP на порт 80."

  # Чтобы nginx поднялся с TLS-конфигурацией, сертификат нужен уже сейчас —
  # иначе он не стартует и подтверждать домен будет нечем. Кладём временный
  # самоподписанный и заменяем настоящим сразу после выпуска.
  if [[ ! -f "$CERT" ]]; then
    # Своя генерация, а не вызов mode_selfsigned: тот в конце применяет
    # конфигурацию и при неудаче делает exit из функции — с подавленным выводом
    # это давало пустой выход с кодом 1 (нашло ревью).
    say "Временный сертификат, чтобы поднять вход на 80/443"
    if ! make_selfsigned_cert "$DOMAIN"; then
      err "Не удалось создать временный сертификат — выпуск Let's Encrypt невозможен."
      note "Нужен openssl на сервере либо собранный образ backend."
      exit 1
    fi
  fi
  DCH up -d web >/dev/null

  say "Выпускаю сертификат Let's Encrypt"
  if ! DCH run --rm certbot certonly --webroot -w /var/www/certbot \
        -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email --non-interactive; then
    err "Выпуск сертификата не прошёл."
    note "Самые частые причины:"
    note "  • порт 80 закрыт снаружи (проверка домена идёт именно по нему);"
    note "  • домен указывает на другой сервер;"
    note "  • исчерпан лимит выпусков Let's Encrypt — повторите через час."
    note "Сейчас вход работает по самоподписанному сертификату: браузер ругается, но система доступна."
    exit 1
  fi
  copy_letsencrypt_files || exit 1
  apply_https letsencrypt
  install_renewal
}

copy_letsencrypt_files() {
  # -L: в live/ лежат символьные ссылки на archive/, копировать надо содержимое.
  if ! DCH run --rm --entrypoint sh certbot -c \
      "cp -L /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/letsencrypt/live-copy/fullchain.pem && \
       cp -L /etc/letsencrypt/live/$DOMAIN/privkey.pem  /etc/letsencrypt/live-copy/privkey.pem"; then
    err "Сертификат выпущен, но файлы не скопировались в deploy/certs."
    return 1
  fi
  chmod 600 "$KEY" 2>/dev/null || true
  ok "Сертификат на месте (годен до $(cert_expires_at))"
}

install_renewal() {
  command -v crontab >/dev/null 2>&1 || {
    warn "cron не установлен — автопродление не поставлено."
    note "Сертификат Let's Encrypt живёт 90 дней; продлевать: ./scripts/enable-https.sh --renew"
    return 0
  }
  local self="$OPS_ROOT/scripts/enable-https.sh"
  {
    crontab -l 2>/dev/null | grep -vF "$CRON_MARK" || true
    printf '17 3 * * 1 %s --renew >> %s/https-renew.log 2>&1 %s\n' \
      "$self" "$(backup_dir)" "$CRON_MARK"
  } | crontab -
  mkdir -p "$(backup_dir)"
  ok "Автопродление: по понедельникам в 03:17 (журнал $(backup_dir)/https-renew.log)"
}

mode_renew() {
  DOMAIN="${DOMAIN:-$(env_get HTTPS_DOMAIN)}"
  [[ -n "$DOMAIN" ]] || { err "Не задан домен — нечего продлевать."; exit 1; }
  say "Продление сертификата для $DOMAIN"
  local left; left="$(cert_days_left)"
  note "Сейчас годен ещё ${left:-?} дн"
  # certbot сам не станет продлевать, пока не подошёл срок, — повторный запуск безопасен.
  DCH run --rm certbot renew --webroot -w /var/www/certbot --non-interactive || {
    err "Продление не прошло — смотри вывод выше. Сайт продолжает работать на текущем сертификате."
    exit 1
  }
  copy_letsencrypt_files || exit 1
  DCH exec -T web nginx -s reload >/dev/null 2>&1 || DCH up -d web >/dev/null
  ok "Готово, годен до $(cert_expires_at)"
}

mode_proxy() {
  say "Режим «TLS снимает внешний прокси»"
  DCP up -d web >/dev/null
  env_set HTTPS_MODE proxy
  local port; port="$(env_get PREPROD_HTTP_PORT)"; port="${port:-8080}"
  # `$(env_get ... || echo 8080)` не работает: env_get всегда завершается
  # успехом, и в сообщении оставался пустой порт (нашло ревью).
  ok "Вход стека слушает только 127.0.0.1:$port"
  echo
  note "Что должен делать прокси, чтобы система работала правильно:"
  note "  1. Терминировать TLS и перенаправлять HTTP → HTTPS."
  note "  2. Передавать заголовки: X-Forwarded-Proto: https, X-Real-IP: <адрес клиента>,"
  note "     X-Forwarded-For. По X-Real-IP пишется адрес в журнале неудачных входов —"
  note "     без него все попытки будут выглядеть пришедшими с одного адреса."
  note "  3. Держать Host исходным (проксирование без подмены имени)."
  note "  4. Не резать длинные ответы: табель отдела — сотни килобайт JSON."
  note "  5. Проверка живости прокси — на /health, готовности — на /ready (503 = не пускать трафик)."
  echo
  warn "Проверьте, что снаружи порт 8080 действительно закрыт: curl -I http://<адрес>:8080/"
  note "Если прокси стоит на другой машине — верните обычную публикацию порта"
  note "(./scripts/enable-https.sh --off) и закройте порт межсетевым экраном."
}

mode_off() {
  say "Возврат на обычный HTTP"
  env_set HTTPS_MODE ""
  $DKR compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d web >/dev/null
  command -v crontab >/dev/null 2>&1 && { crontab -l 2>/dev/null | grep -vF "$CRON_MARK" | crontab - || true; }
  ok "Стенд снова на HTTP. Сертификаты в deploy/certs не удалены."
}

mode_status() {
  local mode; mode="$(env_get HTTPS_MODE)"
  say "HTTPS — состояние"
  case "${mode:-}" in
    letsencrypt) note "Режим: сертификат Let's Encrypt с автопродлением" ;;
    cert)        note "Режим: сертификат вашего IT-отдела" ;;
    selfsigned)  warn "Режим: САМОПОДПИСАННЫЙ сертификат — для людей не годится" ;;
    proxy)       note "Режим: TLS снимает внешний прокси, стек слушает только 127.0.0.1" ;;
    *)           warn "HTTPS не включён: пароли и токены идут по сети открытым текстом."
                 note "Выбрать способ: ./scripts/enable-https.sh --help"; return 0 ;;
  esac
  [[ -n "$(env_get HTTPS_DOMAIN)" ]] && note "Домен: $(env_get HTTPS_DOMAIN)"
  if [[ -f "$CERT" ]]; then
    local left; left="$(cert_days_left)"
    note "Сертификат годен до $(cert_expires_at) (ещё ${left:-?} дн)"
    if [[ -n "$left" && "$left" -lt 14 ]]; then
      warn "Меньше двух недель до истечения."
      [[ "$mode" == "letsencrypt" ]] && note "Продлить сейчас: ./scripts/enable-https.sh --renew" \
                                     || note "Запросите новый файл у IT и повторите --cert/--key"
    fi
  fi
  if command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -qF "$CRON_MARK"; then
    note "Автопродление: включено"
  elif [[ "$mode" == "letsencrypt" ]]; then
    warn "Автопродление НЕ включено — сертификат кончится через 90 дней."
  fi
}

case "$MODE" in
  letsencrypt) mode_letsencrypt ;;
  cert)        mode_cert ;;
  selfsigned)  mode_selfsigned ;;
  proxy)       mode_proxy ;;
  renew)       mode_renew ;;
  off)         mode_off ;;
  status)      mode_status ;;
esac
