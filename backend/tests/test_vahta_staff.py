"""Штат охраны ведётся из модуля вахты (task_guard_ownership).

Владение — на уровне РАБОЧЕГО МЕСТА: охранную позицию правит только вахта,
обычную и самого человека — общий справочник. Запрет стоит на бэке.
"""
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.schedules import Schedule
from app.services.employee_import import COLUMNS
from app.services.guard_duty import create_assignment, quick_hire, set_days
from app.services.payroll import position_setup_issues
from app.services.payroll_statement import build_payroll_statement
from tests.conftest import get_token
from tests.test_vahta import (  # noqa: F401 — фикстуры справочника вахты
    _make_post,
    _make_site,
    _make_zone,
    companies,
    crew,
    guard_dept,
    guard_post,
    other_dept,
    site,
    zone,
)

YEAR, MONTH = 2026, 8


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client, admin_user) -> dict:
    return _auth(get_token(client, "admin@example.com", "admin123"))


def _user(db, email: str, role: str, depts: list[Department]) -> Employee:
    emp = Employee(
        full_name=f"QA {role}", email=email, hashed_password=hash_password("Test1234!"),
        role=role, is_active=True, must_change_password=False,
    )
    emp.managed_departments = depts
    db.add(emp)
    db.commit()
    return emp


@pytest.fixture
def guard_manager(client, db_session, guard_dept) -> dict:
    _user(db_session, "gm@example.com", "manager", [guard_dept])
    return _auth(get_token(client, "gm@example.com", "Test1234!"))


def _hire(client, headers, dept, kind="guard", amount="3500", **extra):
    return client.post(
        "/api/vahta/staff",
        json={"full_name": "Караулов Олег Петрович", "department_id": dept.id,
              "kind": kind, "amount": amount, **extra},
        headers=headers,
    )


@pytest.fixture
def guard_staff(client, admin, guard_dept) -> dict:
    """Посменный охранник, заведённый из вахты."""
    resp = _hire(client, admin, guard_dept)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def schedule(db_session) -> Schedule:
    s = Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8, is_active=True)
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture
def ordinary_emp(db_session, other_dept, schedule) -> Employee:
    emp = Employee(full_name="Инженеров Иван", tab_number="T-0010", is_active=True)
    db_session.add(emp)
    db_session.commit()
    pos = emp.ensure_primary_position()
    pos.department_id = other_dept.id
    pos.schedule_id = schedule.id
    pos.rate = Decimal("80000")
    db_session.commit()
    db_session.refresh(emp)
    return emp


# ── Создание из вахты: посменный охранник и начальник с окладом ───────────────

class TestCreate:
    def test_shift_guard(self, client, admin, guard_dept, db_session):
        resp = _hire(client, admin, guard_dept, kind="guard", amount="3500")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["pay_type"] == "per_shift"
        assert Decimal(body["amount"]) == Decimal("3500")
        pos = db_session.get(EmployeePosition, body["position_id"])
        assert pos.shift_rate == Decimal("3500") and pos.rate is None
        assert pos.title == "Охранник"

    def test_chief_on_salary(self, client, admin, guard_dept, db_session):
        resp = _hire(client, admin, guard_dept, kind="chief", amount="135000")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["pay_type"] == "salary"
        assert body["kind_label"] == "Начальник охраны"
        pos = db_session.get(EmployeePosition, body["position_id"])
        assert pos.rate == Decimal("135000") and pos.shift_rate is None

    def test_gbr_is_per_shift(self, client, admin, guard_dept):
        assert _hire(client, admin, guard_dept, kind="gbr").json()["pay_type"] == "per_shift"

    def test_unknown_kind_422(self, client, admin, guard_dept):
        assert _hire(client, admin, guard_dept, kind="boss").status_code == 422

    def test_tab_number_from_common_numbering(self, client, admin, guard_dept, ordinary_emp):
        """T-0010 у обычного сотрудника → охраннику T-0011: счётчик один."""
        assert _hire(client, admin, guard_dept).json()["tab_number"] == "T-0011"

    def test_tab_number_manual_and_unique(self, client, admin, guard_dept, ordinary_emp):
        ok = _hire(client, admin, guard_dept, tab_number="0000-777")
        assert ok.json()["tab_number"] == "0000-777"
        dup = _hire(client, admin, guard_dept, tab_number="T-0010")
        assert dup.status_code == 422
        assert "уже занят" in dup.json()["detail"]

    def test_empty_fields_are_fine(self, client, admin, guard_dept, db_session):
        """Без графика, коэффициентов, дат и даже суммы — сохраняется."""
        resp = _hire(client, admin, guard_dept, amount=None)
        assert resp.status_code == 201, resp.text
        pos = db_session.get(EmployeePosition, resp.json()["position_id"])
        assert pos.schedule_id is None

    def test_position_dates_saved(self, client, admin, guard_dept, db_session):
        resp = _hire(client, admin, guard_dept, hire_date="2026-08-10",
                     dismissal_date="2026-08-20")
        pos = db_session.get(EmployeePosition, resp.json()["position_id"])
        assert str(pos.hire_date) == "2026-08-10"
        # Даты человека ведёт справочник — вахта их не трогает.
        assert pos.employee.hire_date is None

    def test_non_guard_department_rejected(self, client, admin, other_dept):
        """Вахта не заводит людей в обычные подразделения."""
        assert _hire(client, admin, other_dept).status_code == 403

    def test_quick_hire_chief_gets_salary(self, db_session, guard_post):
        """Быстрый найм всем ставил посменную — начальник теперь на окладе."""
        _, position = quick_hire(
            db_session, full_name="Начальников Пётр", place=guard_post,
            rate=Decimal("135000"), kind="chief",
        )
        assert position.pay_type == "salary"
        assert position.rate == Decimal("135000")


def test_chief_placed_on_post_gets_salary_position(
    client, admin, db_session, guard_post, ordinary_emp
):
    """Постановка на пост заводит рабочее место — тип оплаты от должности строки."""
    resp = client.post(
        "/api/vahta/assignments",
        json={"year": YEAR, "month": MONTH, "post_id": guard_post.id,
              "kind": "chief", "employee_id": ordinary_emp.id, "rate": "120000"},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    emp = db_session.get(Employee, ordinary_emp.id)
    guard_positions = [p for p in emp.positions if p.department_id == guard_post.department_id]
    assert [p.pay_type for p in guard_positions] == ["salary"]


# ── Права экрана ──────────────────────────────────────────────────────────────

class TestAccess:
    def test_guard_manager_creates(self, client, guard_manager, guard_dept):
        assert _hire(client, guard_manager, guard_dept).status_code == 201

    def test_other_manager_forbidden(self, client, db_session, guard_dept, other_dept):
        _user(db_session, "om@example.com", "manager", [other_dept])
        headers = _auth(get_token(client, "om@example.com", "Test1234!"))
        assert _hire(client, headers, guard_dept).status_code == 403

    @pytest.mark.parametrize("role", ["accountant", "timekeeper"])
    def test_roles_without_staff_screen(self, client, db_session, guard_dept, role):
        _user(db_session, f"{role}@example.com", role, [guard_dept])
        headers = _auth(get_token(client, f"{role}@example.com", "Test1234!"))
        assert client.get("/api/vahta/staff", headers=headers).status_code == 403
        assert _hire(client, headers, guard_dept).status_code == 403

    def test_list(self, client, admin, guard_staff, ordinary_emp):
        rows = client.get("/api/vahta/staff", headers=admin).json()
        assert [r["position_id"] for r in rows] == [guard_staff["position_id"]]

    def test_list_shows_month_post(
        self, client, admin, db_session, guard_staff, guard_post
    ):
        pos = db_session.get(EmployeePosition, guard_staff["position_id"])
        create_assignment(db_session, year=YEAR, month=MONTH, place=guard_post, position=pos)
        db_session.commit()
        rows = client.get(
            "/api/vahta/staff", params={"year": YEAR, "month": MONTH}, headers=admin
        ).json()
        assert rows[0]["places"] == ["Green Wood · GW 1"]


# ── Общий справочник: видно, но не правится ───────────────────────────────────

class TestDirectoryReadOnly:
    def test_visible_in_directory(self, client, admin, guard_staff):
        emp_id = guard_staff["employee_id"]
        assert client.get(f"/api/employees/{emp_id}", headers=admin).status_code == 200
        ids = [e["id"] for e in client.get("/api/employees", headers=admin).json()]
        assert emp_id in ids

    def test_patch_position_rejected(self, client, admin, guard_staff, db_session):
        emp_id, pid = guard_staff["employee_id"], guard_staff["position_id"]
        resp = client.patch(
            f"/api/employees/{emp_id}/positions/{pid}",
            json={"shift_rate": "9999"}, headers=admin,
        )
        assert resp.status_code == 403
        assert "Вахта" in resp.json()["detail"]
        db_session.expire_all()
        assert db_session.get(EmployeePosition, pid).shift_rate == Decimal("3500")

    def test_patch_flat_compat_field_rejected(self, client, admin, guard_staff):
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}",
            json={"shift_rate": "9999"}, headers=admin,
        )
        assert resp.status_code == 403

    def test_transfer_out_via_directory_rejected(
        self, client, admin, guard_staff, other_dept
    ):
        """Из охраны переводит владелец — вахта, не справочник."""
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}/positions/{guard_staff['position_id']}",
            json={"department_id": other_dept.id}, headers=admin,
        )
        assert resp.status_code == 403

    def test_flat_transfer_out_rejected(self, client, admin, guard_staff, other_dept):
        """Плоское поле отдела пишет основную позицию — перевод из охраны и здесь закрыт."""
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}",
            json={"department_id": other_dept.id}, headers=admin,
        )
        assert resp.status_code == 403

    def test_person_save_does_not_touch_guard_base(
        self, client, admin, db_session, guard_staff
    ):
        """Сохранение ФИО не гасит «чужие» базы охранной позиции мимо вахты."""
        pos = db_session.get(EmployeePosition, guard_staff["position_id"])
        pos.rate = Decimal("1")  # след старых данных: две базы сразу
        db_session.commit()
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}",
            json={"full_name": "Караулов О."}, headers=admin,
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(EmployeePosition, guard_staff["position_id"]).rate == Decimal("1")

    def test_person_fields_editable(self, client, admin, guard_staff, guard_dept):
        """ФИО, таб. № и даты человека — справочник; форма шлёт и плоские поля
        без изменений, это не правка позиции."""
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}",
            json={"full_name": "Караулов Олег П.", "department_id": guard_dept.id,
                  "shift_rate": "3500", "pay_type": "per_shift"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["full_name"] == "Караулов Олег П."

    def test_access_grant_allowed(self, client, admin, guard_staff):
        resp = client.post(
            f"/api/employees/{guard_staff['employee_id']}/access",
            json={"email": "chief@example.com", "role": "manager",
                  "initial_password": "Test1234!"},
            headers=admin,
        )
        assert resp.status_code == 201

    def test_create_employee_in_guard_department_rejected(
        self, client, admin, guard_dept
    ):
        resp = client.post(
            "/api/employees",
            json={"full_name": "Новиков", "department_id": guard_dept.id},
            headers=admin,
        )
        assert resp.status_code == 403

    def test_add_guard_position_rejected(self, client, admin, ordinary_emp, guard_dept):
        resp = client.post(
            f"/api/employees/{ordinary_emp.id}/positions",
            json={"department_id": guard_dept.id, "pay_type": "per_shift"},
            headers=admin,
        )
        assert resp.status_code == 403

    def test_delete_guard_position_rejected(
        self, client, admin, db_session, ordinary_emp, guard_dept
    ):
        extra = EmployeePosition(
            employee_id=ordinary_emp.id, department_id=guard_dept.id, is_primary=False
        )
        db_session.add(extra)
        db_session.commit()
        resp = client.delete(
            f"/api/employees/{ordinary_emp.id}/positions/{extra.id}", headers=admin
        )
        assert resp.status_code == 403

    def test_company_shares_rejected(self, client, admin, guard_staff, companies):
        resp = client.put(
            f"/api/employees/{guard_staff['employee_id']}/company-shares",
            json={"position_id": guard_staff["position_id"],
                  "shares": [{"company_id": companies["ZMO"].id, "percent": "100"}]},
            headers=admin,
        )
        assert resp.status_code == 403

    def test_combiner_ordinary_position_still_editable(
        self, client, admin, db_session, ordinary_emp, guard_dept
    ):
        """Совместитель: охранная позиция закрыта, обычная правится как раньше."""
        db_session.add(EmployeePosition(
            employee_id=ordinary_emp.id, department_id=guard_dept.id, is_primary=False,
        ))
        db_session.commit()
        primary = ordinary_emp.primary_position
        resp = client.patch(
            f"/api/employees/{ordinary_emp.id}/positions/{primary.id}",
            json={"rate": "90000"}, headers=admin,
        )
        assert resp.status_code == 200, resp.text

    def test_import_rejects_guard_department(self, client, admin, guard_dept, companies):
        wb = Workbook()
        ws = wb.active
        ws.append([c.title for c in COLUMNS])
        row = [""] * len(COLUMNS)
        keys = [c.key for c in COLUMNS]
        row[keys.index("full_name")] = "Импортов Иван"
        row[keys.index("company")] = "ZMO"
        row[keys.index("department")] = guard_dept.name
        row[keys.index("pay_type")] = "посменная"
        row[keys.index("shift_rate")] = "3000"
        ws.append(row)
        buf = BytesIO()
        wb.save(buf)
        resp = client.post(
            "/api/employees/import",
            files={"file": ("e.xlsx", buf.getvalue(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        errors = resp.json()["rows"][0]["errors"]
        assert any("Вахта" in e for e in errors)


# ── Переводы ──────────────────────────────────────────────────────────────────

class TestTransfer:
    def test_out_of_guard_warns_then_saves(
        self, client, admin, guard_staff, other_dept, db_session
    ):
        url = f"/api/vahta/staff/{guard_staff['position_id']}"
        resp = client.patch(url, json={"department_id": other_dept.id}, headers=admin)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        # Те же формулировки, что пишет расчёт.
        assert detail["issues"] == ["Не задан график"]
        db_session.expire_all()
        pos = db_session.get(EmployeePosition, guard_staff["position_id"])
        assert pos.department_id != other_dept.id  # без подтверждения — ничего

        resp = client.patch(
            url, params={"confirm": True}, json={"department_id": other_dept.id},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        assert db_session.get(EmployeePosition, guard_staff["position_id"]).department_id == other_dept.id

        # Владение сменилось: вахта не видит, справочник правит.
        assert client.patch(url, json={"amount": "1"}, headers=admin).status_code == 404
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}/positions/{guard_staff['position_id']}",
            json={"shift_rate": "4000"}, headers=admin,
        )
        assert resp.status_code == 200

    def test_out_of_guard_lists_missing_base(self, client, admin, guard_dept, other_dept):
        staff = _hire(client, admin, guard_dept, amount=None).json()
        resp = client.patch(
            f"/api/vahta/staff/{staff['position_id']}",
            json={"department_id": other_dept.id}, headers=admin,
        )
        assert resp.json()["detail"]["issues"] == [
            "Не задан график", "Не задана ставка за смену",
        ]

    def test_into_guard_needs_nothing(
        self, client, admin, db_session, ordinary_emp, guard_dept
    ):
        pid = ordinary_emp.primary_position.id
        resp = client.patch(
            f"/api/employees/{ordinary_emp.id}/positions/{pid}",
            json={"department_id": guard_dept.id}, headers=admin,
        )
        assert resp.status_code == 200, resp.text
        rows = client.get("/api/vahta/staff", headers=admin).json()
        assert pid in [r["position_id"] for r in rows]
        # Теперь это охранная позиция — справочник её не правит.
        resp = client.patch(
            f"/api/employees/{ordinary_emp.id}/positions/{pid}",
            json={"rate": "1"}, headers=admin,
        )
        assert resp.status_code == 403

    def test_edit_within_guard(self, client, admin, guard_staff, db_session):
        resp = client.patch(
            f"/api/vahta/staff/{guard_staff['position_id']}",
            json={"kind": "chief", "amount": "120000", "dismissal_date": "2026-09-30"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["pay_type"] == "salary"
        pos = db_session.get(EmployeePosition, guard_staff["position_id"])
        db_session.refresh(pos)
        assert pos.rate == Decimal("120000") and pos.shift_rate is None


# ── Обычные позиции без послаблений и перемен ─────────────────────────────────

class TestOrdinaryUnchanged:
    def test_reasons_unchanged(self, db_session, other_dept):
        emp = Employee(full_name="Безграфиков", is_active=True)
        db_session.add(emp)
        db_session.commit()
        pos = emp.ensure_primary_position()
        pos.department_id = other_dept.id
        db_session.commit()
        assert position_setup_issues(pos) == ["Не задан график", "Не задан оклад"]

    def test_ordinary_directory_edit_works(self, client, admin, ordinary_emp):
        resp = client.patch(
            f"/api/employees/{ordinary_emp.id}", json={"rate": "85000"}, headers=admin,
        )
        assert resp.status_code == 200


# ── Снятие флага охраны ───────────────────────────────────────────────────────

class TestGuardFlagRemoval:
    def test_warns_then_allows(self, client, admin, guard_staff, guard_dept, db_session):
        url = f"/api/departments/{guard_dept.id}"
        resp = client.patch(url, json={"is_guard_department": False}, headers=admin)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["position_count"] == 1
        assert detail["not_calculable_count"] == 1
        assert "Не задан график" in detail["issues"]

        resp = client.patch(
            url, params={"confirm": True}, json={"is_guard_department": False},
            headers=admin,
        )
        assert resp.status_code == 200
        # Позиция перешла в справочник.
        resp = client.patch(
            f"/api/employees/{guard_staff['employee_id']}/positions/{guard_staff['position_id']}",
            json={"shift_rate": "4000"}, headers=admin,
        )
        assert resp.status_code == 200

    def test_empty_department_no_warning(self, client, admin, guard_dept):
        resp = client.patch(
            f"/api/departments/{guard_dept.id}", json={"is_guard_department": False},
            headers=admin,
        )
        assert resp.status_code == 200


# ── Ведомость ─────────────────────────────────────────────────────────────────

def test_statement_includes_staff_from_vahta(
    client, admin, db_session, guard_staff, guard_post, ordinary_emp
):
    """Одна ведомость на всех: охранник из вахты и обычный сотрудник рядом."""
    pos = db_session.get(EmployeePosition, guard_staff["position_id"])
    assignment = create_assignment(
        db_session, year=YEAR, month=MONTH, place=guard_post, position=pos,
        rate=Decimal("3500"),
    )
    set_days(db_session, assignment, set(range(1, 11)))
    db_session.commit()
    employees = [pos.employee, ordinary_emp]
    statement = build_payroll_statement(db_session, employees, [], YEAR, MONTH)
    by_emp = {r.employee_id: r for r in statement.rows}
    assert by_emp[pos.employee_id].distribution_source == "guard_post"
    assert by_emp[pos.employee_id].base_salary == Decimal("35000")
    assert ordinary_emp.id in by_emp
