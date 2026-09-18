#!/usr/bin/env python3
"""Печатает, какие проверки гнать по изменённым файлам рабочего дерева.

Изменённые = `git diff HEAD` + неотслеживаемые. Бэкенд-модуль `app/<pkg>/<name>.py`
тянет тесты, которые его импортируют, и `tests/test_<name>*.py`. Полный pytest
идёт ~30 минут, поэтому здесь только затронутое — полный прогон отдельно, в фоне.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = ROOT / "backend/tests"

# Файлы протокола блокировок: после правки — PG-набор (на SQLite гонку не поймать).
PG_TRIGGERS = ("services/timesheet.py", "services/timesheet_periods.py",
               "services/guard_duty.py", "services/guard_month.py", "app/main.py")

# Зеркала бэк ↔ фронт: правишь одно — проверь второе (CLAUDE.md, «Сквозные правила»).
MIRRORS = [
    ("backend/app/services/distribution.py", "frontend/src/utils/distribution.ts"),
    ("backend/app/services/payroll_statement.py", "frontend/src/utils/overtime.ts"),
    ("backend/app/services/payroll_statement.py", "frontend/src/pages/admin/PayrollPage.tsx"),
    ("backend/app/services/employment_period.py", "frontend/src/utils/employment.ts"),
]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


changed = sorted(set(
    git("diff", "--name-only", "HEAD").split() + git("ls-files", "--others", "--exclude-standard").split()
))
if not changed:
    print("Изменений в рабочем дереве нет — проверять нечего.")
    sys.exit(0)

test_sources = {p.name: p.read_text(errors="ignore") for p in TESTS.glob("test_*.py")}
backend_tests, pg, frontend, migrations = set(), False, False, False
for f in changed:
    if f.startswith("backend/tests/test_") and f.endswith(".py"):
        backend_tests.add(Path(f).name)
    elif f.startswith("backend/app/") and f.endswith(".py"):
        name = Path(f).stem
        pkg = Path(f).parent.name
        mod = re.compile(rf"\bapp\.{pkg}\.{name}\b|\bfrom app\.{pkg} import [^\n]*\b{name}\b")
        backend_tests |= {t for t, src in test_sources.items() if mod.search(src)}
        backend_tests |= {t for t in test_sources if t.startswith(f"test_{name}")}
        pg = pg or f.endswith(PG_TRIGGERS)
    elif f.startswith("backend/alembic/versions/"):
        migrations = True
    elif f.startswith("frontend/src/"):
        frontend = True

print("Изменено файлов:", len(changed))
if migrations:
    print("\n# Миграции\ncd backend && .venv/bin/alembic upgrade head && .venv/bin/alembic current")
if backend_tests:
    print(f"\n# Бэкенд: {len(backend_tests)} файл(ов) тестов (по 1–2 мин на файл; много — run_in_background)")
    print("cd backend && .venv/bin/pytest -q " + " ".join(f"tests/{t}" for t in sorted(backend_tests)))
    print("cd backend && .venv/bin/ruff check " + " ".join(
        f.removeprefix("backend/") for f in changed if f.startswith("backend/") and f.endswith(".py")))
if pg:
    print("\n# Протокол блокировок затронут — PG-набор (нужен Postgres в Docker)")
    print("cd backend && TEST_POSTGRES_URL=postgresql+psycopg://tabel:tabel@localhost:5432/tabel_test "
          ".venv/bin/python -m pytest -m postgres -q")
if frontend:
    print("\n# Фронт (типы — ТОЛЬКО через build: tsc --noEmit ошибок не видит)")
    print("cd frontend && npm test && npm run build")
for back, front in MIRRORS:
    if (back in changed) != (front in changed):
        done, other = (back, front) if back in changed else (front, back)
        print(f"\n⚠ Зеркало: изменён {done}, но не {other} — проверить, не разъехалось ли правило.")
