#!/usr/bin/env python3
"""PostToolUse-хук на Bash: отметить, что в сессии спросили граф.

Отметка — файл /tmp/claude-graphify-<session_id>; её проверяют
`graph_first.py` и `guard_bash.py` перед чтением кода проекта.
"""
import json
import re
import sys

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

command = str((event.get("tool_input") or {}).get("command", ""))
if re.search(r"\bgraphify\s+(query|path|explain)\b", command):
    session = str(event.get("session_id", "unknown"))
    with open(f"/tmp/claude-graphify-{session}", "w") as f:
        f.write(command[:200])
