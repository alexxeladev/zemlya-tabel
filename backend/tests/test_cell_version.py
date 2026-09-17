"""Два редактора одной ячейки: версия записи и отказ вместо молчаливой
перезаписи (task_stage1 п.1.5).

Клиент присылает версию ячейки, которую видел (`expected_version`; 0 — «ячейки
нет»). Разошлась с базой — 409, в базе ничего не меняется. Поле необязательное:
без него поведение прежнее (батч, автозаполнение, старый клиент).
"""
import pytest

from app.models.timesheet_entries import TimesheetEntry
from tests.test_cell_company_change import (  # noqa: F401 — общие фикстуры ячейки
    WORK_DATE,
    _day,
    admin,
    company_a,
    company_b,
    dept,
    headers,
    worker,
)

URL = "/api/timesheet/cell"


def _cell(worker, company, hours, **extra) -> dict:
    return {"employee_id": worker.id, "work_date": WORK_DATE,
            "company_id": company.id, "hours": hours, **extra}


def test_entry_carries_a_version_that_grows_with_every_edit(client, headers, worker, company_a):
    created = client.put(URL, json=_cell(worker, company_a, 8), headers=headers).json()
    updated = client.put(URL, json=_cell(worker, company_a, 7), headers=headers).json()

    assert created["version"] == 1
    assert updated["version"] == 2


def test_month_response_carries_versions(client, headers, worker, company_a):
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    month = client.get("/api/timesheet/2026/5", headers=headers).json()
    assert [e["version"] for e in month["entries"]] == [1]


def test_stale_editor_is_refused_and_nothing_changes(client, headers, db_session, worker, company_a):
    """Оба открыли ячейку с 8 ч (версия 1). Первый поставил 6, второй шлёт 7."""
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    first = client.put(URL, json=_cell(worker, company_a, 6, expected_version=1), headers=headers)

    second = client.put(URL, json=_cell(worker, company_a, 7, expected_version=1), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "6" in second.json()["detail"], "в отказе должно быть названо текущее значение"
    assert _day(db_session, worker) == {company_a.id: 6}


def test_fresh_version_is_accepted(client, headers, db_session, worker, company_a):
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    resp = client.put(URL, json=_cell(worker, company_a, 7, expected_version=1), headers=headers)

    assert resp.status_code == 200
    assert _day(db_session, worker) == {company_a.id: 7}


def test_creating_a_cell_someone_already_created_is_refused(
    client, headers, db_session, worker, company_a,
):
    """Оба видели пустой день (версия 0); второй не затирает часы первого."""
    client.put(URL, json=_cell(worker, company_a, 8, expected_version=0), headers=headers)

    resp = client.put(URL, json=_cell(worker, company_a, 4, expected_version=0), headers=headers)

    assert resp.status_code == 409
    assert _day(db_session, worker) == {company_a.id: 8}


def test_editing_a_cell_someone_deleted_is_refused(client, headers, db_session, worker, company_a):
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    client.put(URL, json=_cell(worker, company_a, 0), headers=headers)

    resp = client.put(URL, json=_cell(worker, company_a, 7, expected_version=1), headers=headers)

    assert resp.status_code == 409
    assert _day(db_session, worker) == {}


def test_stale_delete_is_refused(client, headers, db_session, worker, company_a):
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    client.put(URL, json=_cell(worker, company_a, 6), headers=headers)

    resp = client.put(URL, json=_cell(worker, company_a, 0, expected_version=1), headers=headers)

    assert resp.status_code == 409
    assert _day(db_session, worker) == {company_a.id: 6}


def test_without_expected_version_behaviour_is_unchanged(
    client, headers, db_session, worker, company_a,
):
    """Поле необязательное: батч, автозаполнение и старый клиент его не шлют."""
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    client.put(URL, json=_cell(worker, company_a, 6), headers=headers)

    resp = client.put(URL, json=_cell(worker, company_a, 7), headers=headers)

    assert resp.status_code == 200
    assert _day(db_session, worker) == {company_a.id: 7}


def test_stale_company_change_is_refused(client, headers, db_session, worker, company_a, company_b):
    """Перенос на другое юрлицо — та же правка ячейки: устаревший экран не
    переносит часы, которых уже нет."""
    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    client.put(URL, json=_cell(worker, company_a, 6), headers=headers)

    resp = client.put("/api/timesheet/cell/company", headers=headers, json={
        "employee_id": worker.id, "work_date": WORK_DATE,
        "old_company_id": company_a.id, "new_company_id": company_b.id,
        "expected_version": 1,
    })

    assert resp.status_code == 409
    assert _day(db_session, worker) == {company_a.id: 6}


def test_conflict_writes_nothing_to_audit_log(client, headers, db_session, worker, company_a):
    from app.models.audit_log import AuditLog

    client.put(URL, json=_cell(worker, company_a, 8), headers=headers)
    before = db_session.query(AuditLog).count()

    client.put(URL, json=_cell(worker, company_a, 7, expected_version=5), headers=headers)

    assert db_session.query(AuditLog).count() == before


def test_version_column_has_a_server_default(db_session, worker, company_a):
    """Демо-генератор пишет ячейки bulk-ом, мимо конструктора модели."""
    from datetime import date

    db_session.bulk_insert_mappings(TimesheetEntry, [{
        "employee_id": worker.id, "position_id": worker.primary_position.id,
        "work_date": date(2026, 5, 6), "company_id": company_a.id, "hours": 8,
    }])
    db_session.commit()
    assert db_session.query(TimesheetEntry).one().version == 1
