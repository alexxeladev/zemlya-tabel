#!/usr/bin/env python3
"""PreToolUse-хук на Bash: механические ограничители вместо обещаний.

Появился 23.09.2026 после этапа 3: правило «сначала граф» было записано в
CLAUDE.md и всё равно нарушено — текст не остановил. Заказчик одобрил три
ограничителя (правка — только с его согласия):

1. **Разрушительные команды отклоняются всегда**: dropdb, DROP/TRUNCATE в psql,
   `app.cli reset-data`, `docker volume rm`, `docker compose down -v`,
   `git reset --hard`, `./deploy.sh`. Нужно выполнить — человек запускает сам
   через `! <команда>`. `git push` здесь НЕ запрещён (решение заказчика).
2. **Миграции и изменяющий SQL — только при свежем бэкапе**: `alembic
   upgrade/downgrade` и psql с INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/TRUNCATE
   отклоняются, если в ~/backups нет дампа моложе часа.
3. **Сначала граф**: cat/grep/sed/head/tail/rg/awk по `backend/app` и
   `frontend/src` отклоняются, пока в сессии не было `graphify query/path/explain`
   (отметку ставит `graph_mark.py`).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

command = str((event.get("tool_input") or {}).get("command", ""))
session = str(event.get("session_id", "unknown"))


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)


# ── 1. Разрушительные команды ────────────────────────────────────────────────
uses_sql = re.search(r"\b(psql|sqlite3)\b", command) is not None
DESTRUCTIVE = [
    (r"\bdropdb\b", "dropdb"),
    (r"\bapp\.cli\s+reset-data\b", "reset-data"),
    (r"\bdocker\s+volume\s+rm\b", "docker volume rm"),
    (r"\bdocker[\s-]compose\b[^|;&]*\bdown\b[^|;&]*(\s-v\b|--volumes)", "docker compose down -v"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"(^|[\s;&|])\./deploy\.sh\b", "./deploy.sh"),
]
for pattern, label in DESTRUCTIVE:
    if re.search(pattern, command):
        deny(
            f"Команда «{label}» запрещена хуком проекта (.claude/hooks/guard_bash.py): "
            "она необратима. Если она действительно нужна — спросите пользователя, "
            f"он выполнит её сам через `! <команда>`."
        )
if uses_sql and re.search(r"\b(DROP\s+(DATABASE|TABLE|SCHEMA)|TRUNCATE)\b", command, re.I):
    deny(
        "DROP/TRUNCATE через psql запрещены хуком проекта (.claude/hooks/guard_bash.py). "
        "Если это действительно нужно — спросите пользователя, он выполнит сам."
    )

# ── 2. Миграции и изменяющий SQL — только при свежем бэкапе ─────────────────
writes_db = re.search(r"\balembic\s+(upgrade|downgrade)\b", command) or (
    uses_sql
    and re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE)\b", command, re.I)
)
if writes_db:
    backups = Path.home() / "backups"
    fresh = False
    if backups.is_dir():
        limit = time.time() - 3600
        for pattern in ("**/*.sql", "**/*.sql.gz", "**/*.dump"):
            for f in backups.glob(pattern):
                try:
                    if f.stat().st_mtime >= limit and f.stat().st_size > 0:
                        fresh = True
                        break
                except OSError:
                    continue
            if fresh:
                break
    if not fresh:
        deny(
            "Миграция или изменяющий SQL без свежего бэкапа: в ~/backups нет дампа "
            "моложе часа (CLAUDE.md → «Бэкапы БД»). Сначала снимите дамп: "
            "docker exec -t $(docker ps -q -f ancestor=postgres:16) pg_dump -U tabel tabel "
            "> ~/backups/before_<задача>_$(date +%s).sql"
        )

# ── 3. Сначала граф ─────────────────────────────────────────────────────────
# Сама команда graphify — не чтение кода, даже если в имени узла есть путь
# `backend/app`, а вывод обрезан `| head` (иначе граф нельзя было бы спросить).
is_graphify = re.search(r"\bgraphify\b", command) is not None
reads_code = not is_graphify and re.search(
    r"\b(cat|grep|rg|sed|head|tail|less|awk)\b", command
) and re.search(
    r"(backend/app\b|frontend/src\b|(^|[\s'\"=])app/(services|routers|models|schemas|core)\b"
    r"|(^|[\s'\"=])src/(pages|components|utils|api|store|types|hooks)\b)",
    command,
)
if reads_code and not os.path.exists(f"/tmp/claude-graphify-{session}"):
    deny(
        "Сначала граф (CLAUDE.md → «Работа с графом кода»): в этой сессии ещё не было "
        "`graphify query` / `path` / `explain`. Спросите граф, где лежит нужное, потом "
        "читайте код."
    )

sys.exit(0)
