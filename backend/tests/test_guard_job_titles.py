"""Справочник должностей охраны (вместо четырёх зашитых в код).

Должность — строка справочника; от неё расчёту нужен только способ оплаты.
Тесты: CRUD и инварианты справочника, права, подбор «по умолчанию» для поста и
экипажа, расчёт по своей должности, снятая должность в истории, штат.
"""
from decimal import Decimal

import pytest

from app.models.guard_assignments import GuardAssignment
from app.models.guard_job_titles import GuardJobTitle
from app.services.guard_duty import create_assignment
from app.services.payroll_statement import build_payroll_statement
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
    FIRST_HALF,
    MONTH,
    YEAR,
    _auth,
    _title_id,
    companies,
    crew,
    gbr_place,
    guard_dept,
    guard_post,
    other_dept,
    rodionov,
    site,
    users,
    zone,
)

URL = "/api/vahta/job-titles"


def _titles(client, role="admin", **params):
    return client.get(URL, params=params, headers=_auth(client, role)).json()


class TestReference:
    def test_defaults_are_seeded(self, client, users):
        names = [t["name"] for t in _titles(client)]
        assert names == ["Охранник", "ГБР", "Диспетчер", "Начальник охраны"]
        by_name = {t["name"]: t for t in _titles(client)}
        assert by_name["Охранник"]["default_for_post"] and by_name["ГБР"]["default_for_crew"]
        assert by_name["Начальник охраны"]["pay_type"] == "salary"

    def test_create_custom_title(self, client, users):
        resp = client.post(URL, json={"name": "Старший смены", "pay_type": "per_shift"},
                           headers=_auth(client, "manager"))
        assert resp.status_code == 201, resp.text
        assert resp.json()["pay_type_label"] == "Ставка за смену"
        # Новая — в конец списка, не первой.
        assert [t["name"] for t in _titles(client)][-1] == "Старший смены"

    def test_duplicate_name_is_rejected_case_insensitively(self, client, users):
        resp = client.post(URL, json={"name": "охранник"}, headers=_auth(client, "admin"))
        assert resp.status_code == 422

    def test_unknown_pay_type_is_rejected(self, client, users):
        resp = client.post(URL, json={"name": "Сторож", "pay_type": "hourly"}, headers=_auth(client, "admin"))
        assert resp.status_code == 422

    def test_default_flag_moves_not_duplicates(self, client, users, db_session):
        dispatcher = _title_id(db_session, "Диспетчер")
        resp = client.patch(f"{URL}/{dispatcher}", json={"default_for_post": True}, headers=_auth(client, "admin"))
        assert resp.status_code == 200
        flags = {t["name"]: t["default_for_post"] for t in _titles(client)}
        assert flags == {"Охранник": False, "ГБР": False, "Диспетчер": True, "Начальник охраны": False}

    def test_default_title_cannot_be_deactivated_or_deleted(self, client, users, db_session):
        guard = _title_id(db_session, "Охранник")
        assert client.patch(f"{URL}/{guard}", json={"is_active": False}, headers=_auth(client, "admin")).status_code == 422
        assert client.delete(f"{URL}/{guard}", headers=_auth(client, "admin")).status_code == 422

    def test_used_title_is_deactivated_not_deleted(self, client, users, db_session, gbr_place, rodionov):
        dispatcher = _title_id(db_session, "Диспетчер")
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, job_title_id=dispatcher, days=FIRST_HALF)
        db_session.commit()
        resp = client.delete(f"{URL}/{dispatcher}", headers=_auth(client, "admin"))
        assert resp.json() == {"result": "deactivated"}
        assert "Диспетчер" not in [t["name"] for t in _titles(client)]
        assert "Диспетчер" in [t["name"] for t in _titles(client, include_inactive=True)]
        # Строка табеля живёт и подписана снятой должностью.
        row = db_session.query(GuardAssignment).one()
        assert row.job_title_name == "Диспетчер"

    def test_unused_title_is_deleted(self, client, users, db_session):
        dispatcher = _title_id(db_session, "Диспетчер")
        assert client.delete(f"{URL}/{dispatcher}", headers=_auth(client, "admin")).json() == {"result": "deleted"}
        assert db_session.get(GuardJobTitle, dispatcher) is None

    def test_timekeeper_reads_but_does_not_edit(self, client, users):
        assert client.get(URL, headers=_auth(client, "timekeeper")).status_code == 200
        assert client.post(URL, json={"name": "Сторож"}, headers=_auth(client, "timekeeper")).status_code == 403

    def test_employee_has_no_access(self, client, users):
        assert client.get(URL, headers=_auth(client, "employee")).status_code == 403

    def test_change_is_in_reference_audit(self, client, users, db_session):
        from app.models.reference_changes import ReferenceChange

        chief = _title_id(db_session, "Начальник охраны")
        client.patch(f"{URL}/{chief}", json={"name": "Начальник службы"}, headers=_auth(client, "admin"))
        rows = (
            db_session.query(ReferenceChange)
            .filter_by(entity_type="guard_job_title", field="name").all()
        )
        assert [(r.old_value, r.new_value) for r in rows] == [("Начальник охраны", "Начальник службы")]


class TestAssignments:
    def test_post_default_and_crew_default(self, client, users, db_session, guard_post, gbr_place, rodionov):
        guard2 = __import__("tests.test_vahta", fromlist=["_employee"])._employee(
            db_session, "Второй Охранник", rodionov.primary_position.department, "0000-90276")
        a = create_assignment(db_session, year=YEAR, month=MONTH, place=guard_post, position=rodionov.primary_position)
        b = create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place, position=guard2.primary_position)
        assert a.job_title_name == "Охранник" and b.job_title_name == "ГБР"

    def test_custom_title_drives_the_pay_type(self, client, users, db_session, gbr_place, rodionov):
        """Своя окладная должность считается как оклад: половина за полмесяца."""
        chief2 = client.post(URL, json={"name": "Заместитель начальника", "pay_type": "salary"},
                             headers=_auth(client, "admin")).json()["id"]
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, job_title_id=chief2,
                          rate=Decimal("100000"), days=FIRST_HALF)
        db_session.commit()
        row = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0]
        assert row.accrued_total == Decimal("50000.00")
        payroll = client.get(f"/api/timesheet/{YEAR}/{MONTH}/payroll", headers=_auth(client, "admin")).json()
        mine = next(r for r in payroll["employees"] if r["position_id"] == rodionov.primary_position.id)
        assert mine["position_title"] == "Заместитель начальника"

    def test_changing_pay_type_of_a_title_recalculates_open_month(
        self, client, users, db_session, gbr_place, rodionov,
    ):
        dispatcher = _title_id(db_session, "Диспетчер")
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, job_title_id=dispatcher,
                          rate=Decimal("5000"), days=FIRST_HALF)
        db_session.commit()
        before = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0].accrued_total
        assert before == Decimal("75000.00")  # 15 × 5000
        client.patch(f"{URL}/{dispatcher}", json={"pay_type": "salary"}, headers=_auth(client, "admin"))
        db_session.expire_all()
        after = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0].accrued_total
        assert after == Decimal("2500.00")  # оклад 5000: половина за полные 15 дней

    def test_inactive_title_cannot_be_assigned(self, client, users, db_session, gbr_place, rodionov):
        dispatcher = _title_id(db_session, "Диспетчер")
        client.patch(f"{URL}/{dispatcher}", json={"is_active": False}, headers=_auth(client, "admin"))
        resp = client.post("/api/vahta/assignments", json={
            "year": YEAR, "month": MONTH, "crew_id": gbr_place.id,
            "position_id": rodionov.primary_position.id, "job_title_id": dispatcher,
        }, headers=_auth(client, "admin"))
        assert resp.status_code == 422
        assert "снята" in resp.json()["detail"]

    def test_month_response_carries_title_id_name_and_pay_type(self, client, users, db_session, gbr_place, rodionov):
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, days=FIRST_HALF)
        db_session.commit()
        data = client.get(f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "admin")).json()
        row = data["zones"][0]["cards"][0]["rows"][0]
        assert row["job_title_id"] == _title_id(db_session, "ГБР")
        assert row["job_title_name"] == "ГБР" and row["pay_type"] == "per_shift"
        assert "kind" not in row and "kind_label" not in row


class TestStaff:
    def test_staff_row_resolves_title_by_position_name(self, client, users, db_session, rodionov):
        rodionov.primary_position.title = "Начальник охраны"
        rodionov.primary_position.pay_type = "salary"
        db_session.commit()
        rows = client.get("/api/vahta/staff", params={"year": YEAR, "month": MONTH},
                          headers=_auth(client, "admin")).json()
        me = next(r for r in rows if r["position_id"] == rodionov.primary_position.id)
        assert me["job_title_id"] == _title_id(db_session, "Начальник охраны")

    def test_staff_row_with_unknown_name_falls_back_by_pay_type(self, client, users, db_session, rodionov):
        rodionov.primary_position.title = "Кто-то из прошлого"
        db_session.commit()
        rows = client.get("/api/vahta/staff", params={"year": YEAR, "month": MONTH},
                          headers=_auth(client, "admin")).json()
        me = next(r for r in rows if r["position_id"] == rodionov.primary_position.id)
        assert me["job_title_id"] == _title_id(db_session, "Охранник")
        assert me["job_title_name"] == "Охранник"
