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


# ── По итогам ревью ───────────────────────────────────────────────────────────

from app.models.timesheet_periods import TimesheetPeriod  # noqa: E402


def _close(db_session, dept, year=YEAR, month=MONTH):
    db_session.add(TimesheetPeriod(department_id=dept.id, year=year, month=month, status="closed"))
    db_session.commit()


class TestClosedMonths:
    def test_pay_type_change_is_refused_when_title_is_in_a_closed_month(
        self, client, users, db_session, gbr_place, guard_dept, rodionov,
    ):
        """Снапшота расчёта нет: смена способа оплаты переписала бы ведомость,
        которую бухгалтерия уже видела. Отказ с перечнем месяцев."""
        dispatcher = _title_id(db_session, "Диспетчер")
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, job_title_id=dispatcher,
                          rate=Decimal("5000"), days=FIRST_HALF)
        db_session.commit()
        _close(db_session, guard_dept)

        resp = client.patch(f"{URL}/{dispatcher}", json={"pay_type": "salary"}, headers=_auth(client, "admin"))

        assert resp.status_code == 409, resp.text
        assert f"{MONTH:02d}.{YEAR}" in resp.json()["detail"]
        db_session.expire_all()
        assert db_session.get(GuardJobTitle, dispatcher).pay_type == "per_shift"
        assert build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH).rows[0].accrued_total == Decimal("75000.00")

    def test_rename_in_a_closed_month_is_fine(self, client, users, db_session, gbr_place, guard_dept, rodionov):
        """Название — подпись, деньги не трогает."""
        dispatcher = _title_id(db_session, "Диспетчер")
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, job_title_id=dispatcher, days=FIRST_HALF)
        db_session.commit()
        _close(db_session, guard_dept)
        resp = client.patch(f"{URL}/{dispatcher}", json={"name": "Диспетчер ПЦН"}, headers=_auth(client, "admin"))
        assert resp.status_code == 200, resp.text

    def test_usage_is_split_by_period_status(self, client, users, db_session, gbr_place, guard_dept, rodionov):
        dispatcher = _title_id(db_session, "Диспетчер")
        create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                          position=rodionov.primary_position, job_title_id=dispatcher, days=FIRST_HALF)
        db_session.commit()
        _close(db_session, guard_dept)
        me = next(t for t in _titles(client) if t["id"] == dispatcher)
        assert me["usage_count"] == 1 and me["closed_usage_count"] == 1


class TestPositionsAreLinkedByKey:
    """Рабочее место ссылается на должность по FK, а не по названию: переименование
    и снятие должности штат не ломают (ревью)."""

    def test_rename_keeps_staff_on_the_same_title(self, client, users, db_session, rodionov):
        chief = _title_id(db_session, "Начальник охраны")
        client.patch(f"/api/vahta/staff/{rodionov.primary_position.id}",
                     json={"job_title_id": chief, "amount": "135000"}, headers=_auth(client, "admin"))
        client.patch(f"{URL}/{chief}", json={"name": "Начальник службы безопасности"}, headers=_auth(client, "admin"))

        rows = client.get("/api/vahta/staff", params={"year": YEAR, "month": MONTH}, headers=_auth(client, "admin")).json()
        me = next(r for r in rows if r["position_id"] == rodionov.primary_position.id)
        assert me["job_title_id"] == chief
        assert me["job_title_name"] == "Начальник службы безопасности"
        db_session.expire_all()
        assert db_session.get(type(rodionov.primary_position), rodionov.primary_position.id).title == "Начальник службы безопасности"

    def test_title_used_only_by_staff_is_not_physically_deleted(self, client, users, db_session, rodionov):
        dispatcher = _title_id(db_session, "Диспетчер")
        client.patch(f"/api/vahta/staff/{rodionov.primary_position.id}",
                     json={"job_title_id": dispatcher}, headers=_auth(client, "admin"))
        resp = client.delete(f"{URL}/{dispatcher}", headers=_auth(client, "admin"))
        assert resp.json() == {"result": "deactivated"}
        assert db_session.get(GuardJobTitle, dispatcher) is not None

    def test_editing_amount_does_not_rewrite_the_title(self, client, users, db_session, rodionov):
        """Правка суммы при снятой должности не подменяет должность фолбэком."""
        dispatcher = _title_id(db_session, "Диспетчер")
        client.patch(f"/api/vahta/staff/{rodionov.primary_position.id}",
                     json={"job_title_id": dispatcher, "amount": "4000"}, headers=_auth(client, "admin"))
        client.patch(f"{URL}/{dispatcher}", json={"is_active": False}, headers=_auth(client, "admin"))

        resp = client.patch(f"/api/vahta/staff/{rodionov.primary_position.id}",
                            json={"amount": "4500"}, headers=_auth(client, "admin"))

        assert resp.status_code == 200, resp.text
        assert resp.json()["job_title_name"] == "Диспетчер"
        assert Decimal(resp.json()["amount"]) == Decimal("4500")

    def test_legacy_position_without_link_is_matched_by_name_once(self, client, users, db_session, rodionov):
        """Позиции до миграции: название совпало с должностью — привязка ставится
        при первом чтении, неизвестное название — пусто, без подмены."""
        pos = rodionov.primary_position
        pos.job_title_id = None
        pos.title = "гбр"
        db_session.commit()
        rows = client.get("/api/vahta/staff", params={"year": YEAR, "month": MONTH}, headers=_auth(client, "admin")).json()
        me = next(r for r in rows if r["position_id"] == pos.id)
        assert me["job_title_id"] == _title_id(db_session, "ГБР")

        pos.job_title_id = None
        pos.title = "Кто-то из прошлого"
        db_session.commit()
        rows = client.get("/api/vahta/staff", params={"year": YEAR, "month": MONTH}, headers=_auth(client, "admin")).json()
        me = next(r for r in rows if r["position_id"] == pos.id)
        assert me["job_title_id"] is None and me["job_title_name"] == "Кто-то из прошлого"

    def test_staff_list_does_not_query_titles_per_row(self, client, users, db_session, guard_dept):
        from sqlalchemy import event

        from tests.conftest import engine
        from tests.test_vahta import _employee

        for i in range(6):
            _employee(db_session, f"Охранник Номер {i}", guard_dept, f"0000-9040{i}")
        seen = []
        listener = lambda conn, cur, stmt, *a: seen.append(stmt) if "guard_job_titles" in stmt else None  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            client.get("/api/vahta/staff", params={"year": YEAR, "month": MONTH}, headers=_auth(client, "admin"))
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert len(seen) <= 2, f"запросов к справочнику: {len(seen)} на 7 рабочих мест"


class TestReplaceAndStaffEdgeCases:
    def test_replacement_inherits_a_deactivated_title(self, client, users, db_session, gbr_place, rodionov):
        from app.services.guard_duty import replace_on_post
        from tests.test_vahta import _employee

        dispatcher = _title_id(db_session, "Диспетчер")
        a = create_assignment(db_session, year=YEAR, month=MONTH, place=gbr_place,
                              position=rodionov.primary_position, job_title_id=dispatcher, days=FIRST_HALF)
        db_session.commit()
        client.patch(f"{URL}/{dispatcher}", json={"is_active": False}, headers=_auth(client, "admin"))
        other = _employee(db_session, "Сменщик Иван", rodionov.primary_position.department, "0000-90277")

        kept, successor = replace_on_post(db_session, a, position=other.primary_position, from_day=5)

        assert successor is not None and successor.job_title_id == dispatcher

    def test_transfer_out_of_guard_does_not_need_a_title(self, client, users, db_session, rodionov, other_dept):
        resp = client.patch(f"/api/vahta/staff/{rodionov.primary_position.id}",
                            params={"confirm": True},
                            json={"department_id": other_dept.id, "job_title_id": 0}, headers=_auth(client, "admin"))
        assert resp.status_code == 200, resp.text

    def test_default_flag_and_deactivation_in_one_request_change_nothing(self, client, users, db_session):
        dispatcher = _title_id(db_session, "Диспетчер")
        resp = client.patch(f"{URL}/{dispatcher}", json={"is_active": False, "default_for_post": True},
                            headers=_auth(client, "admin"))
        assert resp.status_code == 422
        db_session.expire_all()
        t = db_session.get(GuardJobTitle, dispatcher)
        assert t.is_active is True and t.default_for_post is False

    def test_last_active_title_of_a_pay_type_cannot_be_deactivated(self, client, users, db_session):
        chief = _title_id(db_session, "Начальник охраны")
        resp = client.patch(f"{URL}/{chief}", json={"is_active": False}, headers=_auth(client, "admin"))
        assert resp.status_code == 422
        assert "оклад" in resp.json()["detail"].lower()
