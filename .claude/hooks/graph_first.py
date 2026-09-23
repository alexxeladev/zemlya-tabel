#!/usr/bin/env python3
"""PreToolUse-хук на Read/Grep/Glob: код проекта читается ПОСЛЕ графа.

Правило CLAUDE.md «Любое чтение и поиск по backend/app и frontend/src
начинается с graphify» было нарушено 23.09.2026 (этап 3) — текст не остановил.
Пока в сессии не было `graphify query/path/explain` (отметку ставит
`graph_mark.py`), чтение этих каталогов отклоняется. Одобрено заказчиком.
"""
import json
import os
import sys

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

tool_input = event.get("tool_input") or {}
target = " ".join(
    str(tool_input.get(key, "")) for key in ("file_path", "path", "pattern", "glob")
)
if "/backend/app/" not in target and "/frontend/src/" not in target \
        and not target.rstrip("/").endswith(("/backend/app", "/frontend/src")):
    sys.exit(0)

session = str(event.get("session_id", "unknown"))
if os.path.exists(f"/tmp/claude-graphify-{session}"):
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "Сначала граф (CLAUDE.md → «Работа с графом кода»): в этой сессии ещё не "
            "было `graphify query` / `path` / `explain`. Спросите граф, где лежит "
            "нужное, потом читайте код."
        ),
    }
}, ensure_ascii=False))
