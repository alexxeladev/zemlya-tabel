"""
Условия труда изменениями по ПОЛЮ (ADR-001, task_terms_per_field).

Проверяется то, чего не мог прежний снимок всех полей: правка задним числом тем
же значением, независимость групп друг от друга и от запланированного будущего,
и цена дефекта в деньгах — выходные часы, оплаченные по коэффициенту ×0.

Сценарий с препрода (27.09.2026): бухгалтер поменяла коэффициент, дата по
умолчанию увела правку в следующий месяц, а вернуть её штатно было нельзя — в
карточке уже стояло нужное значение, и бэкенд считал, что менять нечего.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.position_terms import TERM_FIELDS
from app.models.positions import EmployeePosition
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.payroll import calculate_position_payroll
from app.services.position_terms import (
    TermChange,
    TermsNotEditableError,
    ensure_no_free_terms_edit,
    record_term_changes,
    terms_on,
)
from app.services.position_terms_view import group_change_values, terms_state
from tests.conftest import get_token

# Май 2026: 1–3, 9–11, 16–17, 23–24, 30–31 нерабочие; 20 рабочих дней по 8 ч.
MAY_REAL = {"year": 2026, "months": [{"month": 5, "days": "1,2,3,9,10,16,17,23,24,30,31"}]}
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (1, 2, 3, 9, 10, 16, 17, 23, 24, 30, 31)]
SATURDAY = date(2026, 5, 16)  # свой выходной по графику 5/2, не праздник


@pytest.fixture
def setup(db_session: Session, admin_user):
    cal = ProductionCalendar(year=2026, data=MAY_REAL, source="manual")
    sched = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday",
                     work_weekdays=[0, 1, 2, 3, 4])
    other = Schedule(name="6/1", hours_per_shift=9, schedule_type="weekday",
                     work_weekdays=[0, 1, 2, 3, 4, 5])
    zmo = Company(code="ZMO", name="Земля МО")
    db_session.add_all([cal, sched, other, zmo])
    db_session.commit()
    dept = Department(name="ИТО", code="ITO", head_company_id=zmo.id)
    db_session.add(dept)
    db_session.commit()
    emp = Employee(
        full_name="Окладник Иван", tab_number="T-0001", rate=Decimal("60000"),
        schedule_id=sched.id, department_id=dept.id, default_company_id=zmo.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return {"emp": emp, "pos": emp.primary_position, "dept": dept,
            "sched": sched, "other": other, "zmo": zmo, "admin": admin_user}


def _auth(client):
    return {"Authorization": f"Bearer {get_token(client, 'admin@example.com', 'admin123')}"}


def _change(db, position, group: str, day: date, **values):
    record_term_changes(db, position, [
        TermChange(effective_from=day, values=group_change_values(group, values)),
    ])
    db.commit()
    db.refresh(position)


def _hours(db, emp, position, days, hours=8):
    for day in days:
        db.add(TimesheetEntry(
            employee_id=emp.id, position_id=position.id,
            company_id=position.company_id or emp.default_company_id,
            work_date=day, hours=hours,
        ))
    db.commit()


def _payroll(db, emp, position):
    entries = db.query(TimesheetEntry).all()
    return calculate_position_payroll(emp, position, entries, MAY_REAL, 2026, 5)


# ── п.2: правка задним числом тем же значением ────────────────────────────────

class TestBackdatedChange:
    def test_same_value_earlier_date_is_recorded(self, db_session, setup):
        """Главное, чего не умел снимок: «то же 1.5, но с мая»."""
        pos = setup["pos"]
        _change(db_session, pos, "weekend", date(2026, 6, 1),
                weekend_pay_type="coefficient", weekend_coefficient=Decimal("1.5"))
        # Значение в карточке уже 1.5 — прежний код решил бы, что менять нечего.
        assert pos.weekend_coefficient == Decimal("1.50")

        _change(db_session, pos, "weekend", date(2026, 5, 1),
                weekend_pay_type="coefficient", weekend_coefficient=Decimal("1.5"))

        dates = sorted(
            c.effective_from for c in pos.term_changes
            if c.field == "weekend_coefficient" and not c.is_base
        )
        assert dates == [date(2026, 5, 1), date(2026, 6, 1)]
        assert terms_on(pos, date(2026, 5, 10)).weekend_coefficient == Decimal("1.50")

    def test_money_of_the_preprod_defect(self, db_session, setup):
        """Цена дефекта в деньгах: выходные часы по ×0 → по ×1.5.

        Коэффициент выходных 0, отработана суббота 16 мая (8 ч). Бухгалтер
        вводит 1.5, но дата по умолчанию уводит правку в июнь — май остаётся
        нулём. Затем она вносит ТО ЖЕ значение с 1 мая, и май пересчитывается.
        """
        emp, pos = setup["emp"], setup["pos"]
        _change(db_session, pos, "weekend", date(2026, 1, 1),
                weekend_pay_type="coefficient", weekend_coefficient=Decimal("0"))
        _hours(db_session, emp, pos, [SATURDAY])

        p = _payroll(db_session, emp, pos)
        assert p.off_schedule_hours == Decimal("8")
        assert p.off_schedule_amount == Decimal("0")

        # Правка ушла в июнь — май не изменился.
        _change(db_session, pos, "weekend", date(2026, 6, 1),
                weekend_pay_type="coefficient", weekend_coefficient=Decimal("1.5"))
        assert _payroll(db_session, emp, pos).off_schedule_amount == Decimal("0")

        # То же значение, но с мая: 8 ч × (60000/160) × 1.5 = 4500 ₽.
        _change(db_session, pos, "weekend", date(2026, 5, 1),
                weekend_pay_type="coefficient", weekend_coefficient=Decimal("1.5"))
        assert _payroll(db_session, emp, pos).off_schedule_amount == Decimal("4500")


# ── п.3: группы независимы, запланированное не двигается ──────────────────────

class TestGroupsAreIndependent:
    def test_earlier_change_keeps_planned_later_one(self, db_session, setup):
        """Запланировано 100 000 с декабря, правим сентябрь — декабрь на месте."""
        pos = setup["pos"]
        _change(db_session, pos, "pay", date(2026, 12, 1),
                pay_type="salary", rate=Decimal("100000"))
        _change(db_session, pos, "pay", date(2026, 9, 1),
                pay_type="salary", rate=Decimal("90000"))

        assert terms_on(pos, date(2026, 8, 31)).rate == Decimal("60000.00")
        assert terms_on(pos, date(2026, 9, 1)).rate == Decimal("90000.00")
        assert terms_on(pos, date(2026, 11, 30)).rate == Decimal("90000.00")
        assert terms_on(pos, date(2026, 12, 1)).rate == Decimal("100000.00")
        # Зеркало — ПОСЛЕДНЕЕ значение, а не только что введённое.
        assert pos.rate == Decimal("100000.00")

    def test_one_group_does_not_move_another(self, db_session, setup):
        pos = setup["pos"]
        _change(db_session, pos, "overtime", date(2026, 7, 1),
                overtime_coefficient=Decimal("1"))
        _change(db_session, pos, "pay", date(2026, 6, 1),
                pay_type="salary", rate=Decimal("80000"))

        assert terms_on(pos, date(2026, 6, 10)).overtime_coefficient == Decimal("1.50")
        assert terms_on(pos, date(2026, 7, 10)).overtime_coefficient == Decimal("1.00")
        assert terms_on(pos, date(2026, 7, 10)).rate == Decimal("80000.00")

    def test_schedule_change_keeps_its_own_history(self, db_session, setup):
        pos = setup["pos"]
        _change(db_session, pos, "schedule", date(2026, 5, 15),
                schedule_id=setup["other"].id)
        assert terms_on(pos, date(2026, 5, 14)).schedule.name == "5/2"
        assert terms_on(pos, date(2026, 5, 15)).schedule.name == "6/1"


# ── п.1 и п.4: свободно условия не правятся, обычное сохранение не пишет строк ─

class TestNoFreeEditing:
    def test_free_edit_of_terms_is_refused(self, setup):
        with pytest.raises(TermsNotEditableError):
            ensure_no_free_terms_edit(setup["pos"], {"rate": Decimal("90000")})

    def test_unchanged_values_are_not_an_edit(self, setup):
        """Форма шлёт поля целиком — отказ только на РАСХОЖДЕНИИ."""
        pos = setup["pos"]
        ensure_no_free_terms_edit(pos, {f: getattr(pos, f) for f in TERM_FIELDS})

    def test_employee_card_patch_refuses_terms(self, client, db_session, setup):
        h = _auth(client)
        emp = setup["emp"]
        r = client.patch(f"/api/employees/{emp.id}", headers=h, json={"rate": "90000"})
        assert r.status_code == 422
        assert "Изменить" in r.json()["detail"]
        r = client.patch(f"/api/employees/{emp.id}", headers=h,
                         json={"full_name": "Окладник Иван Петрович"})
        assert r.status_code == 200, r.text

    def test_ordinary_save_writes_no_change(self, client, db_session, setup):
        h = _auth(client)
        emp, pos = setup["emp"], setup["pos"]
        before = len(pos.term_changes)
        r = client.patch(f"/api/employees/{emp.id}/positions/{pos.id}", headers=h,
                         json={"title": "Ведущий инженер"})
        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert len(db_session.get(EmployeePosition, pos.id).term_changes) == before


# ── п.5 и п.6: несколько групп за раз, запланированное видно ──────────────────

class TestApi:
    def test_two_groups_two_dates_in_one_request(self, client, db_session, setup):
        h = _auth(client)
        emp, pos = setup["emp"], setup["pos"]
        r = client.post(f"/api/employees/{emp.id}/positions/{pos.id}/terms", headers=h,
                        json={"changes": [
                            {"group": "pay", "effective_from": "2026-06-01",
                             "pay_type": "salary", "rate": "70000"},
                            {"group": "overtime", "effective_from": "2026-07-01",
                             "overtime_coefficient": "2"},
                        ]})
        assert r.status_code == 200, r.text
        db_session.expire_all()
        pos = db_session.get(EmployeePosition, pos.id)
        assert terms_on(pos, date(2026, 6, 5)).rate == Decimal("70000.00")
        assert terms_on(pos, date(2026, 6, 5)).overtime_coefficient == Decimal("1.50")
        assert terms_on(pos, date(2026, 7, 5)).overtime_coefficient == Decimal("2.00")

    def test_state_shows_today_and_what_is_planned(self, db_session, setup):
        pos = setup["pos"]
        _change(db_session, pos, "pay", date(2026, 12, 1),
                pay_type="salary", rate=Decimal("100000"))
        state = terms_state(db_session, pos, today=date(2026, 9, 27))
        pay = next(g for g in state.groups if g.group == "pay")
        assert pay.label == "Тип оплаты и ставка"
        assert pay.current["rate"] == "60000.00"  # сегодня — старый оклад
        assert [(p.effective_from, p.values["rate"]) for p in pay.planned] == [
            (date(2026, 12, 1), "100000.00"),
        ]
        assert pay.planned[0].effective_label == "с 01.12.2026"

    def test_group_without_history_reads_the_position(self, db_session, setup):
        """У группы без изменений значение берётся с позиции (зеркало)."""
        state = terms_state(db_session, setup["pos"], today=date(2026, 9, 27))
        official = next(g for g in state.groups if g.group == "official")
        assert official.current == {"is_official": False, "official_salary": None}

    def test_unknown_group_is_422(self, client, setup):
        h = _auth(client)
        emp, pos = setup["emp"], setup["pos"]
        r = client.post(f"/api/employees/{emp.id}/positions/{pos.id}/terms", headers=h,
                        json={"changes": [{"group": "nonsense",
                                           "effective_from": "2026-06-01"}]})
        assert r.status_code == 422

    def test_empty_list_is_422(self, client, setup):
        h = _auth(client)
        emp, pos = setup["emp"], setup["pos"]
        r = client.post(f"/api/employees/{emp.id}/positions/{pos.id}/terms", headers=h,
                        json={"changes": []})
        assert r.status_code == 422

    def test_timekeeper_does_not_read_terms(self, client, db_session, setup):
        """Оклады и ставки — деньги: табельщику 403 (правило прежнее)."""
        emp = Employee(full_name="Табельщик", tab_number="T-0002",
                       email="tk@example.com", role="timekeeper",
                       hashed_password="x", must_change_password=False)
        db_session.add(emp)
        db_session.commit()
        from app.core.security import hash_password
        emp.hashed_password = hash_password("Test1234!")
        db_session.commit()
        token = get_token(client, "tk@example.com", "Test1234!")
        r = client.get(
            f"/api/employees/{setup['emp'].id}/positions/{setup['pos'].id}/terms",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


# ── п.7: закрытый месяц и месяц на проверке ───────────────────────────────────

class TestClosedMonths:
    @pytest.mark.parametrize("status_", ["closed", "pending_review"])
    def test_date_in_locked_month_is_409(self, client, db_session, setup, status_):
        db_session.add(TimesheetPeriod(
            department_id=setup["dept"].id, year=2026, month=5, status=status_,
        ))
        db_session.commit()
        h = _auth(client)
        emp, pos = setup["emp"], setup["pos"]
        r = client.post(f"/api/employees/{emp.id}/positions/{pos.id}/terms", headers=h,
                        json={"changes": [{"group": "pay", "effective_from": "2026-05-20",
                                           "pay_type": "salary", "rate": "90000"}]})
        assert r.status_code == 409
        assert "2026" in r.json()["detail"] or "05" in r.json()["detail"]


# ── Взаимоисключающие поля внутри группы ──────────────────────────────────────

class TestGroupValues:
    def test_pay_group_clears_other_bases(self):
        assert group_change_values("pay", {
            "pay_type": "hourly", "hour_rate": Decimal("450"), "rate": Decimal("60000"),
        }) == {
            "pay_type": "hourly", "rate": None, "shift_rate": None,
            "hour_rate": Decimal("450"),
        }

    def test_weekend_group_clears_the_other_kind(self):
        assert group_change_values("weekend", {
            "weekend_pay_type": "fixed_rate",
            "weekend_fixed_rate": Decimal("500"),
            "weekend_coefficient": Decimal("1.5"),
        }) == {
            "weekend_pay_type": "fixed_rate", "weekend_coefficient": None,
            "weekend_fixed_rate": Decimal("500"),
        }

    def test_official_salary_is_dropped_when_flag_is_off(self):
        assert group_change_values("official", {
            "is_official": False, "official_salary": Decimal("50000"),
        }) == {"is_official": False, "official_salary": None}


# ── п.10: изменение условий видно в «Журнале изменений» ───────────────────────

class TestJournal:
    def test_term_change_is_a_dated_journal_row(self, db_session, setup):
        from app.models.reference_changes import ReferenceChange
        from app.services.reference_audit import set_audit_actor

        set_audit_actor(db_session, setup["admin"])
        pos = setup["pos"]
        # Базовые значения нового места журнал строками не засыпает.
        assert db_session.query(ReferenceChange).filter_by(field="rate").count() == 0

        _change(db_session, pos, "pay", date(2026, 9, 1),
                pay_type="salary", rate=Decimal("90000"))

        rows = db_session.query(ReferenceChange).filter_by(field="rate").all()
        assert [(r.entity_type, r.old_value, r.new_value) for r in rows] == [
            ("employee_position", "60000", "90000 (с 01.09.2026)"),
        ]
        assert rows[0].employee_id == setup["emp"].id
        assert rows[0].actor_name == setup["admin"].full_name
        # Погашенная база чужого типа оплаты тоже видна — словом, а не пустотой.
        shift = db_session.query(ReferenceChange).filter_by(field="shift_rate").all()
        assert [r.new_value for r in shift] == ["не задано (с 01.09.2026)"]
