from __future__ import annotations

import pytest

from app.services.calendar import (
    CalendarFetchError,
    norm_hours_for_period,
    parse_days_string,
    short_days_in_month,
    workdays_in_month,
)
from tests.conftest import get_token

CALENDAR_2026 = {
    "year": 2026,
    "months": [
        {"month": 5, "days": "1,2,3,8*,9,10,11+,16,17,23,24,30,31"},
        {"month": 1, "days": "1,2,3,4,5,6,7,8,9+,10,11,17,18,24,25,31"},
    ],
}

CALENDAR_2026_FULL = {
    "year": 2026,
    "months": [
        {"month": 1, "days": "1,2,3,4,5,6,7,8,9+,10,11,17,18,24,25,31"},
        {"month": 2, "days": "1,7,8,14,15,21,22,23,28"},
        {"month": 3, "days": "7,8,9,14,15,21,22,28,29"},
        {"month": 4, "days": "4,5,11,12,18,19,25,26"},
        {"month": 5, "days": "1,2,3,8*,9,10,11+,16,17,23,24,30,31"},
        {"month": 6, "days": "6,7,13,14,20,21,27,28"},
        {"month": 7, "days": "4,5,11,12,18,19,25,26"},
        {"month": 8, "days": "1,2,8,9,15,16,22,23,29,30"},
        {"month": 9, "days": "5,6,12,13,19,20,26,27"},
        {"month": 10, "days": "3,4,10,11,17,18,24,25,31"},
        {"month": 11, "days": "1,3,4,7,8,14,15,21,22,28,29"},
        {"month": 12, "days": "5,6,12,13,19,20,26,27,31"},
    ],
}


# ── Unit tests: parser ────────────────────────────────────────────────────────

def test_parse_basic():
    non_working, short = parse_days_string("1,2,3,8*,9+,10")
    assert non_working == {1, 2, 3, 9, 10}
    assert short == {8}


def test_parse_empty():
    assert parse_days_string("") == (set(), set())


def test_parse_short_only():
    non_working, short = parse_days_string("8*")
    assert non_working == set()
    assert short == {8}


def test_parse_with_spaces():
    non_working, short = parse_days_string(" 1 , 2 , 8* ")
    assert non_working == {1, 2}
    assert short == {8}


# ── Unit tests: norm calculations ─────────────────────────────────────────────

def test_workdays_in_month():
    # May 2026: 31 days - 12 non-working = 19
    assert workdays_in_month(CALENDAR_2026, 2026, 5) == 19


def test_short_days_in_month():
    assert short_days_in_month(CALENDAR_2026, 5) == 1


def test_norm_hours_for_period():
    # 19 workdays * 8h - 1 short day = 151
    assert norm_hours_for_period(CALENDAR_2026, 2026, 5, 8) == 151


# ── Integration tests ─────────────────────────────────────────────────────────

@pytest.fixture
def admin_token(client, admin_user):
    return get_token(client, "admin@example.com", "admin123")


@pytest.fixture
def manager_token(client, manager_user):
    return get_token(client, "manager@example.com", "manager123")


@pytest.fixture
def mock_remote_ok(monkeypatch):
    async def _mock(year: int) -> dict:
        return {**CALENDAR_2026_FULL, "year": year}

    monkeypatch.setattr("app.services.calendar.fetch_calendar_from_remote", _mock)
    monkeypatch.setattr("app.routers.calendar.ensure_calendar.__wrapped__" , None, raising=False)


@pytest.fixture
def mock_remote_fail(monkeypatch):
    async def _mock(year: int) -> dict:
        raise CalendarFetchError("xmlcalendar.ru недоступен")

    monkeypatch.setattr("app.services.calendar.fetch_calendar_from_remote", _mock)


def test_get_calendar_loads_from_remote(client, admin_token, monkeypatch):
    async def _mock(year: int) -> dict:
        return {**CALENDAR_2026_FULL, "year": year}

    monkeypatch.setattr("app.services.calendar.fetch_calendar_from_remote", _mock)

    resp = client.get("/api/calendar/2026", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] == 2026
    assert len(data["months"]) == 12
    assert data["source"] == "remote"


def test_get_calendar_remote_unavailable_returns_404(client, admin_token, monkeypatch):
    async def _fail(year: int) -> dict:
        raise CalendarFetchError("недоступен")

    monkeypatch.setattr("app.services.calendar.fetch_calendar_from_remote", _fail)

    resp = client.get("/api/calendar/2026", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404
    assert "Загрузите вручную" in resp.json()["detail"]


def test_load_calendar_non_admin_forbidden(client, manager_token, monkeypatch):
    async def _mock(year: int) -> dict:
        return {**CALENDAR_2026_FULL, "year": year}

    monkeypatch.setattr("app.services.calendar.fetch_calendar_from_remote", _mock)

    resp = client.post(
        "/api/calendar/2026/load", headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 403


def test_import_calendar_manual(client, admin_token):
    payload = {
        "year": 2026,
        "months": CALENDAR_2026_FULL["months"],
    }
    resp = client.post(
        "/api/calendar/import",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["year"] == 2026
    assert data["source"] == "manual"


def test_import_calendar_wrong_month_count(client, admin_token):
    payload = {
        "year": 2026,
        "months": [{"month": 1, "days": "1,2,3"}],
    }
    resp = client.post(
        "/api/calendar/import",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


def test_get_month_summary(client, admin_token, monkeypatch):
    async def _mock(year: int) -> dict:
        return {**CALENDAR_2026_FULL, "year": year}

    monkeypatch.setattr("app.services.calendar.fetch_calendar_from_remote", _mock)

    resp = client.get(
        "/api/calendar/2026/5/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] == 2026
    assert data["month"] == 5
    assert data["workdays"] == 19
    assert data["short_days"] == 1
    assert data["norm_hours_8h"] == 151
    assert len(data["days"]) == 31


def test_import_then_get_returns_cached(client, admin_token):
    """После импорта GET не обращается к remote."""
    payload = {"year": 2025, "months": CALENDAR_2026_FULL["months"]}
    client.post(
        "/api/calendar/import",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Если бы GET ходил в сеть, он упал бы (remote не замокан)
    resp = client.get("/api/calendar/2025", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["source"] == "manual"
