"""Период на проверке у бухгалтера (`pending_review`) не меняется под ним.

Негативные тесты на ВСЕ записи в месяц обычного табеля: часы, батч, перенос
юрлица, код отсутствия, ночная смена, автозаполнение. Правило одно —
`can_edit_cells` (правится только `draft`), воронка одна — `_check_period_lock`;
тест держит, что ни одна точка входа её не обходит. Для вахты то же правило
проверяет `test_vahta_assignment_guards.py`.
"""
from datetime import date

import pytest

from app.models.employee_absences import EmployeeAbsence
from app.models.night_shifts import NightShift
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_periods import TimesheetPeriod
from tests.test_cell_company_change import (  # noqa: F401 — общие фикстуры ячейки
    WORK_DATE,
    _add,
    _body,
    _day,
    _hours,
    admin,
    company_a,
    company_b,
    dept,
    headers,
    worker,
)

LOCKED = ["pending_review", "closed"]
MAY = {"year": 2026, "months": [{"month": 5, "days": "3,4,10,11,17,18,24,25,31"}]}


@pytest.fixture
def locked(request, db_session, dept, worker, company_a):
    """Сотрудник с 8 ч на 5 мая; период мая — в статусе из параметра."""
    _hours(db_session, worker, company_a, 8)
    return _add(db_session, TimesheetPeriod(
        department_id=dept.id, year=2026, month=5, status=request.param,
    ))


def _cell(worker, company, hours, work_date=WORK_DATE):
    return {"employee_id": worker.id, "work_date": work_date,
            "company_id": company.id, "hours": hours}


@pytest.mark.parametrize("locked", LOCKED, indirect=True)
class TestNothingIsWrittenIntoALockedMonth:
    def test_hours(self, client, headers, db_session, locked, worker, company_a):
        resp = client.put("/api/timesheet/cell", json=_cell(worker, company_a, 5), headers=headers)
        assert resp.status_code == 409
        assert _day(db_session, worker) == {company_a.id: 8}

    def test_new_cell(self, client, headers, db_session, locked, worker, company_b):
        resp = client.put("/api/timesheet/cell", json=_cell(worker, company_b, 4), headers=headers)
        assert resp.status_code == 409

    def test_delete_hours(self, client, headers, db_session, locked, worker, company_a):
        resp = client.put("/api/timesheet/cell", json=_cell(worker, company_a, 0), headers=headers)
        assert resp.status_code == 409
        assert _day(db_session, worker) == {company_a.id: 8}

    def test_batch(self, client, headers, db_session, locked, worker, company_a):
        resp = client.post("/api/timesheet/cells/batch", headers=headers, json={"entries": [
            _cell(worker, company_a, 4, "2026-05-06"), _cell(worker, company_a, 4, "2026-05-07"),
        ]})
        assert resp.status_code == 409
        assert _day(db_session, worker) == {company_a.id: 8}

    def test_company_change(self, client, headers, db_session, locked, worker, company_a, company_b):
        resp = client.put("/api/timesheet/cell/company",
                          json=_body(worker, company_a, company_b), headers=headers)
        assert resp.status_code == 409
        assert _day(db_session, worker) == {company_a.id: 8}

    def test_absence(self, client, headers, db_session, locked, worker, company_a):
        resp = client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": WORK_DATE, "kind": "sick",
        })
        assert resp.status_code == 409
        assert db_session.query(EmployeeAbsence).count() == 0
        assert _day(db_session, worker) == {company_a.id: 8}, "код отсутствия стёр бы часы дня"

    def test_night_shift(self, client, headers, db_session, locked, worker):
        worker.primary_position.has_night_shifts = True
        db_session.commit()
        resp = client.put("/api/timesheet/night-shift", headers=headers, json={
            "employee_id": worker.id, "position_id": worker.primary_position.id,
            "work_date": WORK_DATE, "value": True,
        })
        assert resp.status_code == 409
        assert db_session.query(NightShift).count() == 0

    def test_autofill_skips_the_month(self, client, headers, db_session, locked, worker, company_a):
        db_session.add(ProductionCalendar(year=2026, data=MAY, source="manual"))
        schedule = _add(db_session, Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8))
        worker.primary_position.schedule_id = schedule.id
        worker.primary_position.company_id = company_a.id
        db_session.commit()

        resp = client.post("/api/timesheet/autofill/apply", headers=headers,
                           json={"year": 2026, "month": 5, "department_id": None})

        assert resp.status_code == 422  # ни одного draft-периода — заполнять нечего
        assert _day(db_session, worker) == {company_a.id: 8}


def test_draft_month_is_still_editable(client, headers, db_session, dept, worker, company_a):
    _add(db_session, TimesheetPeriod(department_id=dept.id, year=2026, month=5, status="draft"))
    resp = client.put("/api/timesheet/cell", json=_cell(worker, company_a, 5), headers=headers)
    assert resp.status_code == 200
    assert date.fromisoformat(resp.json()["work_date"]) == date(2026, 5, 5)
