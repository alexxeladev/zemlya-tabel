#!/usr/bin/env python3
"""PostToolUse-хук: после записи файла миграции Alembic напомнить её применить.

CLAUDE.md → «Миграции Alembic — всегда применять после создания»: миграция без
`upgrade head` даёт расхождение модели и схемы, и API падает при старте. Правило
текстовое и забывается — хук повторяет его ровно в момент, когда оно нужно.
"""
import json
import sys

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

path = str((event.get("tool_input") or {}).get("file_path", ""))
if "/alembic/versions/" not in path or not path.endswith(".py"):
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "Записан файл миграции Alembic. До того как считать задачу готовой: "
            "`cd backend && .venv/bin/alembic upgrade head` и `.venv/bin/alembic current` "
            "(должен совпасть с head). Файл миграции коммитится вместе с изменением модели."
        ),
    }
}, ensure_ascii=False))
