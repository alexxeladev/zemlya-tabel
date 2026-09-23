"""Интеграционный прогон на PostgreSQL: миграции, уникальные индексы, восстановление.

Этап 4 п.4.5. Быстрый набор идёт на SQLite со схемой из `create_all` — он не
доказывает ни применимость миграций, ни защиту от дублей в рабочей базе:

* частичные уникальные индексы периодов SQLite просто не строит (объявлены
  через `ddl_if(dialect="postgresql")`), и дубль там пройдёт молча;
* блокировок строк на SQLite нет, конкурентную вставку не воспроизвести;
* восстановление из дампа проверяется только настоящими pg_dump/psql.

Запуск (схему поднимает `alembic upgrade head`, см. фикстуру `pg_engine`):

    TEST_POSTGRES_URL=postgresql+psycopg://tabel:tabel@localhost:5432/tabel_test \\
        pytest -m postgres

или одной командой: `scripts/test-postgres.sh`.
"""
import threading
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.models.companies import Company
from app.models.dashboard_cache import DashboardMonthCache, DataVersion
from app.models.departments import Department
from app.models.employees import Employee
from app.models.timesheet_periods import TimesheetPeriod
from app.services.readiness import migration_head
from tests.conftest import PG_URL

pytestmark = pytest.mark.postgres

YEAR, MONTH = 2026, 7
BLOCKED_FOR = 1.5   # столько ждём, чтобы убедиться: вторая вставка стоит на блокировке
GIVE_UP = 20


# ── Схема действительно построена миграциями ─────────────────────────────────

def test_schema_is_built_by_migrations(pg_sessions):
    """Если бы фикстура строила схему `create_all`, прогон снова ничего не
    говорил бы о миграциях — проверяем отметку Alembic в самой базе."""
    db = pg_sessions()
    try:
        applied = db.execute(text("select version_num from alembic_version")).scalar()
    finally:
        db.close()
    assert applied == migration_head()


def test_ready_is_green_on_a_migrated_database(pg_client):
    """Проверка готовности (п.4.2) на настоящей мигрированной базе: только здесь
    видно, что версия из alembic_version совпадает с версией в коде."""
    resp = pg_client.get("/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["migrations"]["state"] == "ok"


# ── Частичные уникальные индексы периодов ────────────────────────────────────

def test_partial_unique_indexes_of_periods_exist(pg_sessions):
    db = pg_sessions()
    try:
        rows = dict(db.execute(text(
            "select indexname, indexdef from pg_indexes where tablename = 'timesheet_periods'"
        )).all())
    finally:
        db.close()

    assert "uq_period_dept_year_month" in rows, "потерян индекс периодов отдела"
    assert "uq_period_null_dept_year_month" in rows, "потерян индекс периодов «Без отдела»"
    assert "UNIQUE" in rows["uq_period_dept_year_month"].upper()
    assert "department_id IS NOT NULL" in rows["uq_period_dept_year_month"]
    assert "UNIQUE" in rows["uq_period_null_dept_year_month"].upper()
    assert "department_id IS NULL" in rows["uq_period_null_dept_year_month"]


def _department(db, name="Отдел индексов", code="IDX"):
    dept = Department(name=name, code=code, is_active=True)
    db.add(dept)
    db.commit()
    return dept.id


def test_second_period_of_a_department_in_one_month_is_rejected(pg_sessions):
    db = pg_sessions()
    try:
        dept_id = _department(db)
        db.add(TimesheetPeriod(department_id=dept_id, year=YEAR, month=MONTH, status="draft"))
        db.commit()
        db.add(TimesheetPeriod(department_id=dept_id, year=YEAR, month=MONTH, status="draft"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_second_period_without_department_is_rejected(pg_sessions):
    """Случай, который обычный unique НЕ ловит: в SQL NULL ≠ NULL, поэтому
    «Без отдела» пропустил бы сколько угодно периодов одного месяца. Его и
    закрывает второй частичный индекс."""
    db = pg_sessions()
    try:
        db.add(TimesheetPeriod(department_id=None, year=YEAR, month=MONTH, status="draft"))
        db.commit()
        db.add(TimesheetPeriod(department_id=None, year=YEAR, month=MONTH, status="draft"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_same_month_in_two_departments_is_allowed(pg_sessions):
    db = pg_sessions()
    try:
        first = _department(db, "Первый", "D1")
        second = _department(db, "Второй", "D2")
        db.add_all([
            TimesheetPeriod(department_id=first, year=YEAR, month=MONTH, status="draft"),
            TimesheetPeriod(department_id=second, year=YEAR, month=MONTH, status="draft"),
            TimesheetPeriod(department_id=first, year=YEAR, month=MONTH + 1, status="draft"),
        ])
        db.commit()
        assert db.query(TimesheetPeriod).count() == 3
    finally:
        db.close()


def test_parallel_creation_of_one_period_leaves_exactly_one(pg_sessions):
    """Период создаётся лениво при первом открытии месяца. Два запроса,
    пришедшие одновременно, вставляют его оба — второго обязан остановить
    индекс, а не удача планировщика. Поэтому вторая вставка запускается, пока
    первая НЕ закоммичена, и тест проверяет, что она ЖДАЛА.
    """
    first = pg_sessions()
    second = pg_sessions()
    failure: list[Exception] = []
    finished = threading.Event()

    def insert_second():
        try:
            second.add(
                TimesheetPeriod(department_id=None, year=YEAR, month=MONTH, status="draft")
            )
            second.commit()
        except Exception as exc:  # noqa: BLE001 — тест разбирает её сам
            failure.append(exc)
        finally:
            finished.set()

    try:
        first.add(TimesheetPeriod(department_id=None, year=YEAR, month=MONTH, status="draft"))
        first.flush()  # индекс захвачен, коммита ещё нет

        worker = threading.Thread(target=insert_second, daemon=True)
        worker.start()
        assert not finished.wait(BLOCKED_FOR), (
            "вторая вставка прошла, не дожидаясь первой, — индекс её не удержал"
        )

        first.commit()
        assert finished.wait(GIVE_UP), "вторая вставка не завершилась"
        worker.join(GIVE_UP)

        assert failure and isinstance(failure[0], IntegrityError), (
            f"вторая вставка обязана была получить отказ, получила: {failure!r}"
        )
    finally:
        second.rollback()
        second.close()
        first.close()

    db = pg_sessions()
    try:
        assert db.query(TimesheetPeriod).filter_by(
            department_id=None, year=YEAR, month=MONTH
        ).count() == 1
    finally:
        db.close()


# ── Восстановление из дампа ──────────────────────────────────────────────────

RATE = Decimal("54321.55")
CACHE_ROWS = [{"position_id": 1, "total_amount": "54321.55", "hours": 168}]
CACHE_VERSION = 7


@pytest.fixture
def seeded_database(pg_sessions):
    """Данные, по которым видно, что восстановилось именно содержимое:
    деньги (Decimal), закрытый период и КЭШ ДАШБОРДА с его счётчиками версий.

    Возвращает то, что РЕАЛЬНО лежит в базе после коммита, а не то, что мы
    записали: счётчик версий двигает слушатель сессии (`services/dashboard_cache`),
    и ожидаемое значение надо брать из базы-источника, иначе тест проверял бы
    не «дамп сохранил как было», а «мы угадали число».
    """
    db = pg_sessions()
    try:
        company = Company(code="RST", name="Restore Co", is_active=True)
        dept = Department(name="Отдел восстановления", code="RST", is_active=True)
        db.add_all([company, dept])
        db.commit()

        db.add(Employee(
            full_name="Восстановленный Сотрудник", tab_number="T-9001",
            is_active=True, department_id=dept.id, rate=RATE,
        ))
        db.add(TimesheetPeriod(
            department_id=dept.id, year=YEAR, month=MONTH, status="closed",
        ))
        db.add(DashboardMonthCache(
            year=YEAR, month=MONTH, month_version=CACHE_VERSION,
            reference_version=CACHE_VERSION, rows=CACHE_ROWS,
        ))
        db.add(DataVersion(key=f"month:{YEAR:04d}-{MONTH:02d}", version=CACHE_VERSION))
        db.commit()
        return {
            "cache_rows": db.execute(text(
                "select rows from dashboard_month_cache where year = :y and month = :m"
            ), {"y": YEAR, "m": MONTH}).scalar(),
            "data_version": db.execute(text(
                "select version from data_versions where key = :k"
            ), {"k": f"month:{YEAR:04d}-{MONTH:02d}"}).scalar(),
        }
    finally:
        db.close()


def test_dump_restores_into_another_database_and_data_is_readable(seeded_database, pg_tools):
    """Настоящая проверка бэкапа: pg_dump → ОТДЕЛЬНАЯ база → чтение данных.

    «Файл создан и непустой» бэкапом не является: так не ловятся ни оборванный
    дамп, ни несовместимая схема, ни пустая выгрузка при неверных правах.
    """
    url = make_url(PG_URL)
    target = f"{url.database}_restore_check"

    dump = pg_tools.dump()
    assert "CREATE TABLE" in dump and "timesheet_periods" in dump

    pg_tools.create_database(target)
    engine = None
    try:
        pg_tools.psql(target, dump)
        engine = create_engine(url.set(database=target))
        with engine.connect() as conn:
            # 1. Данные на месте и читаются.
            assert conn.execute(text(
                "select full_name from employees where tab_number = 'T-9001'"
            )).scalar() == "Восстановленный Сотрудник"
            # 2. Деньги не поехали: Decimal, а не float.
            rate = conn.execute(text(
                "select rate from employee_positions order by id limit 1"
            )).scalar()
            assert rate == RATE and isinstance(rate, Decimal)
            # 3. Статус периода сохранён.
            assert conn.execute(text(
                "select status from timesheet_periods"
            )).scalar() == "closed"
            # 4. Схема восстановилась на той же ревизии — иначе приложение
            #    поднимется над чужой схемой и проверка готовности это покажет.
            assert conn.execute(text(
                "select version_num from alembic_version"
            )).scalar() == migration_head()
            # 5. Уникальные индексы едут вместе с дампом, а не теряются.
            names = set(conn.execute(text(
                "select indexname from pg_indexes where tablename = 'timesheet_periods'"
            )).scalars().all())
            assert {"uq_period_dept_year_month", "uq_period_null_dept_year_month"} <= names
    finally:
        if engine is not None:
            engine.dispose()
        pg_tools.drop_database(target)


def test_restored_dump_carries_the_dashboard_cache(seeded_database, pg_tools):
    """Кэш дашборда и его счётчики версий — обычные таблицы, и в дамп базы они
    попадают вместе с данными, поэтому после восстановления согласованы с ними.
    Опасен другой случай — восстановление ПОД РАБОТАЮЩИМ приложением: там кэш
    остаётся от прежнего состояния, и его гасит скрипт восстановления
    (`scripts/restore.sh`) плюс сброс кэша на старте приложения.
    """
    url = make_url(PG_URL)
    target = f"{url.database}_restore_check"

    dump = pg_tools.dump()
    pg_tools.create_database(target)
    engine = None
    try:
        pg_tools.psql(target, dump)
        engine = create_engine(url.set(database=target))
        with engine.connect() as conn:
            rows = conn.execute(text(
                "select rows from dashboard_month_cache where year = :y and month = :m"
            ), {"y": YEAR, "m": MONTH}).scalar()
            assert rows == seeded_database["cache_rows"] == CACHE_ROWS
            assert conn.execute(text(
                "select version from data_versions where key = :k"
            ), {"k": f"month:{YEAR:04d}-{MONTH:02d}"}).scalar() == seeded_database["data_version"]
    finally:
        if engine is not None:
            engine.dispose()
        pg_tools.drop_database(target)
