"""Гонка «правка табеля × переход периода» — на PostgreSQL (task_stage1 п.1.5).

Проверка статуса периода и запись ячейки шли без блокировки: между ними период
могли отправить на проверку или закрыть, и правка сохранялась в уже
заблокированный месяц. На SQLite это не воспроизвести — там одно соединение и
нет блокировок строк, — поэтому тесты идут только с `TEST_POSTGRES_URL`:

    TEST_POSTGRES_URL=postgresql+psycopg://tabel:tabel@localhost:5432/tabel_test \\
        pytest -m postgres

Перекрытие транзакций здесь НЕ случайное: один из запросов останавливается
внутри своей транзакции (после проверок, до коммита), и второй запускается,
пока первый не закоммичен. Тест проверяет не только итог, но и то, что второй
запрос ЖДАЛ, — иначе зелёный результат был бы везением планировщика.
"""
import threading

import pytest

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from tests.conftest import get_token

pytestmark = pytest.mark.postgres

YEAR, MONTH = 2026, 5
WORK_DATE = "2026-05-05"
PASSWORD = "Test1234!"
BLOCKED_FOR = 1.5   # сколько ждём, чтобы убедиться, что второй запрос стоит на блокировке
GIVE_UP = 20        # страховка от вечного ожидания, если что-то пошло не так


class _Pause:
    """Остановить запрос внутри транзакции и отпустить по команде теста."""

    def __init__(self):
        self.reached = threading.Event()
        self.release = threading.Event()

    def wrap(self, func, *, before: bool):
        def wrapper(*args, **kwargs):
            if before:
                self._hold()
                return func(*args, **kwargs)
            result = func(*args, **kwargs)
            self._hold()
            return result
        return wrapper

    def _hold(self):
        self.reached.set()
        assert self.release.wait(GIVE_UP), "тест не отпустил остановленный запрос"


class _Call(threading.Thread):
    def __init__(self, func):
        super().__init__(daemon=True)
        self._func = func
        self.response = None
        self.error = None

    def run(self):
        try:
            self.response = self._func()
        except Exception as exc:  # noqa: BLE001 — отдаём тесту как есть
            self.error = exc

    def finish(self):
        self.join(GIVE_UP)
        assert not self.is_alive(), "запрос не завершился"
        assert self.error is None, f"запрос упал: {self.error!r}"
        return self.response


@pytest.fixture
def world(pg_sessions):
    """Отдел с сотрудником и сотрудник без отдела; периоды мая в `draft`."""
    db = pg_sessions()
    dept = Department(name="Отдел гонки", code="RACE", is_active=True)
    company = Company(code="RC", name="Race Co", is_active=True)
    db.add_all([dept, company])
    db.commit()
    admin = Employee(
        full_name="Админ Гонки", email="race.admin@example.com",
        hashed_password=hash_password(PASSWORD), role="admin", is_active=True,
        must_change_password=False, is_system_admin=True,
    )
    in_dept = Employee(full_name="Сотрудник Отдела", is_active=True, department_id=dept.id)
    no_dept = Employee(full_name="Сотрудник Без Отдела", is_active=True)
    db.add_all([admin, in_dept, no_dept])
    db.commit()
    periods = {
        "dept": TimesheetPeriod(department_id=dept.id, year=YEAR, month=MONTH, status="draft"),
        "no_dept": TimesheetPeriod(department_id=None, year=YEAR, month=MONTH, status="draft"),
    }
    db.add_all(periods.values())
    db.commit()
    data = {
        "company_id": company.id,
        "dept": {"employee_id": in_dept.id, "period_id": periods["dept"].id,
                 "transition": "submit", "locked_status": "pending_review"},
        # Период «Без отдела» закрывается прямо из draft — это и есть буквальная
        # гонка «правка × закрытие» из задачи.
        "no_dept": {"employee_id": no_dept.id, "period_id": periods["no_dept"].id,
                    "transition": "close", "locked_status": "closed"},
    }
    db.close()
    return data


@pytest.fixture
def headers(pg_client, world):
    return {"Authorization": f"Bearer {get_token(pg_client, 'race.admin@example.com', PASSWORD)}"}


def _edit(pg_client, headers, world, case, hours=8):
    return lambda: pg_client.put("/api/timesheet/cell", headers=headers, json={
        "employee_id": world[case]["employee_id"], "work_date": WORK_DATE,
        "company_id": world["company_id"], "hours": hours,
    })


def _transition(pg_client, headers, world, case):
    url = f"/api/timesheet/periods/{world[case]['period_id']}/{world[case]['transition']}"
    return lambda: pg_client.post(url, headers=headers)


def _state(pg_sessions, world, case):
    db = pg_sessions()
    try:
        status = db.get(TimesheetPeriod, world[case]["period_id"]).status
        hours = [
            int(e.hours) for e in db.query(TimesheetEntry)
            .filter_by(employee_id=world[case]["employee_id"]).all()
        ]
        return status, hours
    finally:
        db.close()


@pytest.mark.parametrize("case", ["dept", "no_dept"])
def test_edit_during_uncommitted_transition_is_rejected(
    pg_client, pg_sessions, headers, world, monkeypatch, case,
):
    """Приёмка: переход периода уже идёт (не закоммичен), в этот момент приходит
    правка. Раньше она читала прежний `draft` и сохранялась в месяц, который тут
    же становился закрытым. Теперь ждёт перехода и отклоняется."""
    from app.services import timesheet_periods as periods_service

    pause = _Pause()
    monkeypatch.setattr(
        periods_service, "log_action", pause.wrap(periods_service.log_action, before=True)
    )
    transition = _Call(_transition(pg_client, headers, world, case))
    transition.start()
    assert pause.reached.wait(GIVE_UP), "переход не дошёл до точки остановки"

    edit = _Call(_edit(pg_client, headers, world, case))
    edit.start()
    edit.join(BLOCKED_FOR)
    edit_waited = edit.is_alive()

    pause.release.set()
    assert transition.finish().status_code == 200
    edit_response = edit.finish()

    status, hours = _state(pg_sessions, world, case)
    assert status == world[case]["locked_status"]
    assert hours == [], "правка сохранилась в заблокированный месяц"
    assert edit_response.status_code == 409, edit_response.text
    assert edit_waited, "правка не ждала перехода — блокировки строки периода нет"


@pytest.mark.parametrize("case", ["dept", "no_dept"])
def test_transition_waits_for_uncommitted_edit(
    pg_client, pg_sessions, headers, world, monkeypatch, case,
):
    """Обратный порядок: правка уже прошла проверку статуса, но не закоммичена.
    Переход обязан её ДОЖДАТЬСЯ: иначе период закрылся бы «поверх» незавершённой
    записи, и та легла бы в закрытый месяц следом."""
    from app.services import timesheet as timesheet_service

    pause = _Pause()
    monkeypatch.setattr(
        timesheet_service, "_upsert_cell_no_commit",
        pause.wrap(timesheet_service._upsert_cell_no_commit, before=False),
    )
    edit = _Call(_edit(pg_client, headers, world, case))
    edit.start()
    assert pause.reached.wait(GIVE_UP), "правка не дошла до точки остановки"

    transition = _Call(_transition(pg_client, headers, world, case))
    transition.start()
    transition.join(BLOCKED_FOR)
    transition_waited = transition.is_alive()

    pause.release.set()
    assert edit.finish().status_code == 200
    assert transition.finish().status_code == 200

    status, hours = _state(pg_sessions, world, case)
    assert status == world[case]["locked_status"]
    assert hours == [8], "правка, начатая до перехода, должна сохраниться"
    assert transition_waited, "переход не ждал незавершённую правку"


def test_concurrent_writers_do_not_block_each_other(
    pg_client, pg_sessions, headers, world, monkeypatch,
):
    """Блокировка записи — РАЗДЕЛЯЕМАЯ: два табельщика одного отдела не стоят
    друг за другом в очереди. Исключительная нужна только переходу."""
    from app.services import timesheet as timesheet_service

    pause = _Pause()
    real = timesheet_service._upsert_cell_no_commit
    calls = []

    def first_call_pauses(*args, **kwargs):
        calls.append(1)
        result = real(*args, **kwargs)
        if len(calls) == 1:
            pause._hold()
        return result

    monkeypatch.setattr(timesheet_service, "_upsert_cell_no_commit", first_call_pauses)
    first = _Call(_edit(pg_client, headers, world, "dept"))
    first.start()
    assert pause.reached.wait(GIVE_UP)

    second = _Call(lambda: pg_client.put("/api/timesheet/cell", headers=headers, json={
        "employee_id": world["dept"]["employee_id"], "work_date": "2026-05-06",
        "company_id": world["company_id"], "hours": 4,
    }))
    second.start()
    second.join(GIVE_UP)
    second_finished_while_first_open = not second.is_alive()

    pause.release.set()
    assert first.finish().status_code == 200
    assert second.finish().status_code == 200
    assert second_finished_while_first_open, "вторая запись ждала первую — блокировка не разделяемая"
    assert sorted(_state(pg_sessions, world, "dept")[1]) == [4, 8]


def test_two_simultaneous_editors_of_one_cell(pg_client, pg_sessions, headers, world, monkeypatch):
    """Два редактора ОДНОВРЕМЕННО правят ячейку, которую оба видели с версией 1.
    Первый остановлен после записи, до коммита; второй обязан дождаться и
    получить отказ, а не молча перезаписать часы первого."""
    from app.services import timesheet as timesheet_service

    assert _edit(pg_client, headers, world, "dept", hours=8)().status_code == 200

    pause = _Pause()
    real = timesheet_service._upsert_cell_no_commit
    calls = []

    def first_call_pauses(*args, **kwargs):
        calls.append(1)
        result = real(*args, **kwargs)
        if len(calls) == 1:
            pause._hold()
        return result

    monkeypatch.setattr(timesheet_service, "_upsert_cell_no_commit", first_call_pauses)

    def editor(hours):
        return lambda: pg_client.put("/api/timesheet/cell", headers=headers, json={
            "employee_id": world["dept"]["employee_id"], "work_date": WORK_DATE,
            "company_id": world["company_id"], "hours": hours, "expected_version": 1,
        })

    first = _Call(editor(6))
    first.start()
    assert pause.reached.wait(GIVE_UP)
    second = _Call(editor(7))
    second.start()
    second.join(BLOCKED_FOR)
    second_waited = second.is_alive()

    pause.release.set()
    assert first.finish().status_code == 200
    second_response = second.finish()

    assert second_response.status_code == 409, second_response.text
    assert _state(pg_sessions, world, "dept")[1] == [6], "часы первого редактора затёрты"
    assert second_waited, "второй редактор не ждал первого — конфликт пойман не блокировкой строки"


def _pause_first_upsert(monkeypatch):
    """Первый вызов записи ячейки останавливается ПОСЛЕ flush, до коммита."""
    from app.services import timesheet as timesheet_service

    pause = _Pause()
    real = timesheet_service._upsert_cell_no_commit
    calls = []

    def first_call_pauses(*args, **kwargs):
        calls.append(1)
        result = real(*args, **kwargs)
        if len(calls) == 1:
            pause._hold()
        return result

    monkeypatch.setattr(timesheet_service, "_upsert_cell_no_commit", first_call_pauses)
    return pause


def test_two_simultaneous_creators_of_one_cell(pg_client, pg_sessions, headers, world, monkeypatch):
    """Оба видели ПУСТОЙ день и одновременно создают ячейку. Второй INSERT ждёт
    на unique-ключе и падает на flush — это обязан быть 409, а не 500 (ревью)."""
    pause = _pause_first_upsert(monkeypatch)

    def creator(hours):
        return lambda: pg_client.put("/api/timesheet/cell", headers=headers, json={
            "employee_id": world["dept"]["employee_id"], "work_date": WORK_DATE,
            "company_id": world["company_id"], "hours": hours, "expected_version": 0,
        })

    first = _Call(creator(6))
    first.start()
    assert pause.reached.wait(GIVE_UP)
    second = _Call(creator(7))
    second.start()
    second.join(BLOCKED_FOR)
    pause.release.set()

    assert first.finish().status_code == 200
    second_response = second.finish()
    assert second_response.status_code == 409, second_response.text
    assert "6" in second_response.json()["detail"]
    assert _state(pg_sessions, world, "dept")[1] == [6]


def test_batch_racing_with_a_single_edit_is_409(pg_client, pg_sessions, headers, world, monkeypatch):
    pause = _pause_first_upsert(monkeypatch)
    first = _Call(_edit(pg_client, headers, world, "dept", hours=6))
    first.start()
    assert pause.reached.wait(GIVE_UP)
    batch = _Call(lambda: pg_client.post("/api/timesheet/cells/batch", headers=headers, json={
        "entries": [{"employee_id": world["dept"]["employee_id"], "work_date": WORK_DATE,
                     "company_id": world["company_id"], "hours": 7}]}))
    batch.start()
    batch.join(BLOCKED_FOR)
    pause.release.set()

    assert first.finish().status_code == 200
    assert batch.finish().status_code == 409
    assert _state(pg_sessions, world, "dept")[1] == [6]


def test_absence_racing_with_an_hours_edit_is_409_not_500(
    pg_client, pg_sessions, headers, world, monkeypatch,
):
    """Код отсутствия удаляет часы дня через ORM, а версия ячейки теперь стоит в
    WHERE этого DELETE: пока правка часов не закоммичена, удаление опаздывает."""
    assert _edit(pg_client, headers, world, "dept", hours=8)().status_code == 200
    pause = _pause_first_upsert(monkeypatch)
    edit = _Call(_edit(pg_client, headers, world, "dept", hours=6))
    edit.start()
    assert pause.reached.wait(GIVE_UP)
    absence = _Call(lambda: pg_client.put("/api/timesheet/absence", headers=headers, json={
        "employee_id": world["dept"]["employee_id"], "work_date": WORK_DATE, "kind": "sick"}))
    absence.start()
    absence.join(BLOCKED_FOR)
    pause.release.set()

    assert edit.finish().status_code == 200
    assert absence.finish().status_code == 409
    assert _state(pg_sessions, world, "dept")[1] == [6]


# ── Лимит попыток входа под параллельной пачкой (task_stage2_access п.2.6) ────

def test_parallel_login_burst_does_not_bypass_limit(pg_client, pg_sessions):
    """Счётчик читается ДО bcrypt, неудача пишется ПОСЛЕ: без сериализации по
    учётке вся пачка проходила проверку до первой записи, и за окно пролезало
    5 + размер пачки попыток. Засчитанных неудач должно быть ровно LIMIT."""
    from app.config import settings
    from app.models.login_failures import LoginFailure

    with pg_sessions() as db:
        db.add(Employee(full_name="Жертва перебора", email="burst@example.com",
                        role="accountant", hashed_password=hash_password(PASSWORD),
                        is_active=True))
        db.commit()

    burst = settings.LOGIN_MAX_FAILURES * 3
    codes: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(burst)

    def attempt():
        start.wait(timeout=GIVE_UP)
        resp = pg_client.post(
            "/api/auth/login", json={"email": "burst@example.com", "password": "wrong-pass"},
        )
        with lock:
            codes.append(resp.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(burst)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=GIVE_UP * 3)

    assert len(codes) == burst
    assert codes.count(401) == settings.LOGIN_MAX_FAILURES
    assert codes.count(429) == burst - settings.LOGIN_MAX_FAILURES
    with pg_sessions() as db:
        counted = db.query(LoginFailure).filter(
            LoginFailure.email == "burst@example.com",
            LoginFailure.reason == "wrong_password",
        ).count()
    assert counted == settings.LOGIN_MAX_FAILURES
