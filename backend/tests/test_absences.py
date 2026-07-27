"""Тесты блока отсутствий, часть 1: коды ОТ / ДО / Б / Н.

Покрывают: оплату отпуска и больничного, неоплачиваемые ДО/Н, пропорциональный
оклад при отсутствиях, исключение дней отсутствия из переработки и
взаимоисключение «часы или код» в одном дне.
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employee_absences import EmployeeAbsence
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.payroll import calculate_employee_payroll
from tests.conftest import get_token

# Май 2026: нерабочие 1,3,4,10,11,17,18,24,25,31 + сокращённый 8*
# → 21 рабочий день, норма при смене 8ч = 21*8 − 1 = 167 ч
MAY_167 = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,3,4,8*,10,11,17,18,24,25,31"}],
}
NON_WORKING = {1, 3, 4, 10, 11, 17, 18, 24, 25, 31}
WORKDAYS = [d for d in range(1, 32) if d not in NON_WORKING]  # 21 день, 8 — сокращённый


# ── Хелперы (чистые объекты, без БД) ─────────────────────────────────────────

def make_schedule(hours_per_shift: int = 8, schedule_type: str = "standard") -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=hours_per_shift, schedule_type=schedule_type)
    s.id = 1
    return s


def make_employee(rate: Decimal | None = Decimal("50000")) -> Employee:
    emp = Employee(full_name="Absence Worker", rate=rate, is_active=True)
    emp.id = 1
    emp.schedule = make_schedule()
    return emp


def make_entry(day: int, hours: int = 8, company_id: int = 1) -> TimesheetEntry:
    return TimesheetEntry(
        employee_id=1, company_id=company_id,
        work_date=date(2026, 5, day), hours=hours,
    )


def make_absence(day: int, kind: str) -> EmployeeAbsence:
    return EmployeeAbsence(employee_id=1, work_date=date(2026, 5, day), kind=kind)


def full_month_entries(skip_days: set[int] | None = None) -> list[TimesheetEntry]:
    """Полностью отработанный месяц по норме (сокращённый день — 7 ч)."""
    skip_days = skip_days or set()
    return [
        make_entry(d, hours=7 if d == 8 else 8)
        for d in WORKDAYS
        if d not in skip_days
    ]


# ── Оплата отпуска и больничного ─────────────────────────────────────────────

class TestVacationPay:
    def test_vacation_paid_by_formula(self):
        """ОТ: оклад / норма × (дни × 8). 50000/167 × 40 = 11976,05 → 11976."""
        emp = make_employee()
        absences = [make_absence(d, "vacation") for d in (5, 6, 7, 12, 13)]

        p = calculate_employee_payroll(emp, [], MAY_167, 2026, 5, absences=absences)

        assert p.norm_hours == Decimal("167")
        assert p.vacation_days == 5
        assert p.vacation_paid_days == 5
        assert p.vacation_amount == Decimal("11976")
        assert p.sick_amount == Decimal("0")
        # без часов оклада нет — только отпускные
        assert p.base_amount == Decimal("0")
        assert p.total_amount == Decimal("11976")

    def test_vacation_on_non_working_day_not_paid(self):
        """Код на выходном отмечается, но не оплачивается: нормы за этот день нет."""
        emp = make_employee()
        absences = [make_absence(5, "vacation"), make_absence(10, "vacation")]  # 10 — выходной

        p = calculate_employee_payroll(emp, [], MAY_167, 2026, 5, absences=absences)

        assert p.vacation_days == 2
        assert p.vacation_paid_days == 1
        assert p.vacation_amount == Decimal("2395")  # 50000/167 × 8


class TestSickPay:
    def test_sick_paid_100_percent_no_limit(self):
        """Б в части 1 — 100% по той же формуле, без годового лимита."""
        emp = make_employee()
        absences = [make_absence(d, "sick") for d in (5, 6, 7)]

        p = calculate_employee_payroll(emp, [], MAY_167, 2026, 5, absences=absences)

        assert p.sick_days == 3
        assert p.sick_amount == Decimal("7186")  # 50000/167 × 24
        assert p.vacation_amount == Decimal("0")

    def test_many_sick_days_still_fully_paid(self):
        """Лимит — часть 2; сейчас оплачиваются все дни."""
        emp = make_employee()
        absences = [make_absence(d, "sick") for d in WORKDAYS[:15]]

        p = calculate_employee_payroll(emp, [], MAY_167, 2026, 5, absences=absences)

        assert p.sick_days == 15
        assert p.sick_amount == Decimal("35928")  # 50000/167 × 120


class TestUnpaidKinds:
    def test_unpaid_leave_and_absent_give_no_money(self):
        emp = make_employee()
        absences = [
            make_absence(5, "unpaid"), make_absence(6, "unpaid"),
            make_absence(7, "absent"),
        ]

        p = calculate_employee_payroll(emp, [], MAY_167, 2026, 5, absences=absences)

        assert p.unpaid_days == 2
        assert p.absent_days == 1
        assert p.vacation_amount == Decimal("0")
        assert p.sick_amount == Decimal("0")
        assert p.total_amount == Decimal("0")


# ── Пропорциональный оклад ────────────────────────────────────────────────────

class TestProratedSalary:
    def test_salary_plus_vacation_is_about_full_rate(self):
        """Пример из задачи: оклад 50000, норма 167 ч, 5 дней отпуска.

        Оклад за отработанное + отпускные ≈ полный оклад (расхождение только
        из-за сокращённого дня и счёта отпуска по 8 ч).
        """
        emp = make_employee()
        vacation_days = {5, 6, 7, 12, 13}
        entries = full_month_entries(skip_days=vacation_days)
        absences = [make_absence(d, "vacation") for d in vacation_days]

        p = calculate_employee_payroll(emp, entries, MAY_167, 2026, 5, absences=absences)

        assert p.total_hours == Decimal("127")  # 167 − 5×8
        assert p.base_amount == Decimal("38024")  # 50000 × 127/167
        assert p.vacation_amount == Decimal("11976")
        assert p.total_amount == Decimal("50000")

    def test_no_double_pay_for_absence_days(self):
        """Оклад считается только за отработанные часы — за дни ОТ его нет."""
        emp = make_employee()
        entries = full_month_entries(skip_days={5})
        absences = [make_absence(5, "vacation")]

        p = calculate_employee_payroll(emp, entries, MAY_167, 2026, 5, absences=absences)

        without_absence = calculate_employee_payroll(
            emp, full_month_entries(), MAY_167, 2026, 5,
        )
        # оклад меньше полного ровно на один день, отпускные его компенсируют
        assert p.base_amount < without_absence.base_amount
        assert p.total_amount == without_absence.total_amount

    def test_sick_month_partially_worked(self):
        emp = make_employee()
        sick_days = {5, 6}
        entries = full_month_entries(skip_days=sick_days)
        absences = [make_absence(d, "sick") for d in sick_days]

        p = calculate_employee_payroll(emp, entries, MAY_167, 2026, 5, absences=absences)

        assert p.total_hours == Decimal("151")
        assert p.sick_amount == Decimal("4790")  # 50000/167 × 16
        assert p.total_amount == p.base_amount + p.sick_amount

    def test_unpaid_leave_reduces_salary_without_compensation(self):
        """ДО: оклад пропорционально меньше, доплаты нет."""
        emp = make_employee()
        entries = full_month_entries(skip_days={5, 6})
        absences = [make_absence(d, "unpaid") for d in (5, 6)]

        p = calculate_employee_payroll(emp, entries, MAY_167, 2026, 5, absences=absences)

        assert p.base_amount == Decimal("45210")  # 50000 × 151/167
        assert p.total_amount == Decimal("45210")


# ── Переработка ───────────────────────────────────────────────────────────────

class TestOvertimeWithAbsences:
    def test_absence_days_excluded_from_overtime(self):
        """В дне отпуска нет ни работы, ни переработки."""
        emp = make_employee()
        entries = full_month_entries(skip_days={5, 6})
        absences = [make_absence(d, "vacation") for d in (5, 6)]

        p = calculate_employee_payroll(emp, entries, MAY_167, 2026, 5, absences=absences)

        assert p.overtime_hours == Decimal("0")
        assert p.overtime_amount == Decimal("0")

    def test_overtime_on_worked_days_still_counted(self):
        """Отпуск не гасит переработку других дней (счёт по дням)."""
        emp = make_employee()
        entries = full_month_entries(skip_days={5, 6})
        entries.append(make_entry(7, hours=12))  # уже есть 8 ч за 7-е → 20 ч в дне
        absences = [make_absence(d, "vacation") for d in (5, 6)]

        p = calculate_employee_payroll(emp, entries, MAY_167, 2026, 5, absences=absences)

        # 7-е: 8+12=20 ч при норме дня 8 → 12 ч переработки
        assert p.overtime_hours == Decimal("12")
        assert p.vacation_paid_days == 2
        assert p.vacation_amount == Decimal("4790")  # 50000/167 × 16


# ── Интеграция: API ───────────────────────────────────────────────────────────

@pytest.fixture
def dept(db_session: Session) -> Department:
    d = Department(name="Abs Dept", code="AD", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(code="AC", name="Absence Co", is_active=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2-abs", hours_per_shift=8, schedule_type="standard", is_active=True)
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_167, source="manual")
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


@pytest.fixture
def admin_abs(db_session: Session) -> Employee:
    emp = Employee(
        full_name="Abs Admin", email="absadmin@example.com",
        hashed_password=hash_password("admin123"), role="admin",
        is_active=True, must_change_password=False, is_system_admin=True,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def worker(db_session: Session, company: Company, schedule: Schedule, dept: Department) -> Employee:
    emp = Employee(
        full_name="Abs Worker", is_active=True, rate=Decimal("50000"),
        schedule_id=schedule.id, default_company_id=company.id, department_id=dept.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


@pytest.fixture
def employee_user(db_session: Session, dept: Department) -> Employee:
    emp = Employee(
        full_name="Abs Employee", email="absemp@example.com",
        hashed_password=hash_password("emp123"), role="employee",
        is_active=True, must_change_password=False, department_id=dept.id,
    )
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    return emp


def _auth(client: TestClient, email: str, password: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {get_token(client, email, password)}"}


class TestAbsenceEndpoint:
    def test_set_and_read_absence(self, client: TestClient, admin_abs: Employee,
                                  worker: Employee, calendar_2026: ProductionCalendar):
        headers = _auth(client, "absadmin@example.com", "admin123")
        resp = client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "vacation",
        })
        assert resp.status_code == 200
        assert resp.json()["code"] == "ОТ"

        month = client.get("/api/timesheet/2026/5", headers=headers).json()
        assert len(month["absences"]) == 1
        assert month["absences"][0]["kind"] == "vacation"
        assert month["absences"][0]["code"] == "ОТ"

    def test_clear_absence(self, client: TestClient, admin_abs: Employee,
                           worker: Employee, calendar_2026: ProductionCalendar):
        headers = _auth(client, "absadmin@example.com", "admin123")
        client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "sick",
        })
        resp = client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": None,
        })
        assert resp.status_code == 200
        month = client.get("/api/timesheet/2026/5", headers=headers).json()
        assert month["absences"] == []

    def test_unknown_kind_rejected(self, client: TestClient, admin_abs: Employee,
                                   worker: Employee):
        headers = _auth(client, "absadmin@example.com", "admin123")
        resp = client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "holiday",
        })
        assert resp.status_code == 422

    def test_employee_sees_own_codes_without_money(
        self, client: TestClient, admin_abs: Employee, employee_user: Employee,
        calendar_2026: ProductionCalendar,
    ):
        admin_headers = _auth(client, "absadmin@example.com", "admin123")
        client.put("/api/timesheet/absence", headers=admin_headers, json={
            "employee_id": employee_user.id, "work_date": "2026-05-05", "kind": "vacation",
        })
        headers = _auth(client, "absemp@example.com", "emp123")
        data = client.get("/api/timesheet/2026/5?include_payroll=true", headers=headers).json()
        assert len(data["absences"]) == 1
        assert data["payroll"] is None  # финансы отсутствий сотруднику не видны

    def test_employee_cannot_set_foreign_absence(
        self, client: TestClient, employee_user: Employee, worker: Employee,
    ):
        headers = _auth(client, "absemp@example.com", "emp123")
        resp = client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "vacation",
        })
        assert resp.status_code == 403


class TestMutualExclusion:
    def test_absence_removes_hours_of_that_day(
        self, client: TestClient, admin_abs: Employee, worker: Employee,
        company: Company, db_session: Session, calendar_2026: ProductionCalendar,
    ):
        headers = _auth(client, "absadmin@example.com", "admin123")
        client.put("/api/timesheet/cell", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05",
            "company_id": company.id, "hours": 8,
        })
        client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "vacation",
        })

        month = client.get("/api/timesheet/2026/5", headers=headers).json()
        assert month["entries"] == []
        assert len(month["absences"]) == 1

    def test_hours_remove_absence_of_that_day(
        self, client: TestClient, admin_abs: Employee, worker: Employee,
        company: Company, calendar_2026: ProductionCalendar,
    ):
        headers = _auth(client, "absadmin@example.com", "admin123")
        client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "sick",
        })
        client.put("/api/timesheet/cell", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05",
            "company_id": company.id, "hours": 8,
        })

        month = client.get("/api/timesheet/2026/5", headers=headers).json()
        assert month["absences"] == []
        assert len(month["entries"]) == 1

    def test_other_days_untouched(
        self, client: TestClient, admin_abs: Employee, worker: Employee,
        company: Company, calendar_2026: ProductionCalendar,
    ):
        headers = _auth(client, "absadmin@example.com", "admin123")
        for day in ("2026-05-05", "2026-05-06"):
            client.put("/api/timesheet/cell", headers=headers, json={
                "employee_id": worker.id, "work_date": day,
                "company_id": company.id, "hours": 8,
            })
        client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "vacation",
        })

        month = client.get("/api/timesheet/2026/5", headers=headers).json()
        assert len(month["entries"]) == 1
        assert month["entries"][0]["work_date"] == "2026-05-06"


class TestAbsenceInPayrollResponse:
    def test_payroll_exposes_absence_days_and_amounts(
        self, client: TestClient, admin_abs: Employee, worker: Employee,
        calendar_2026: ProductionCalendar,
    ):
        headers = _auth(client, "absadmin@example.com", "admin123")
        for day in ("2026-05-05", "2026-05-06"):
            client.put("/api/timesheet/absence", headers=headers, json={
                "employee_id": worker.id, "work_date": day, "kind": "vacation",
            })
        client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-07", "kind": "sick",
        })

        data = client.get("/api/timesheet/2026/5/payroll", headers=headers).json()
        row = next(e for e in data["employees"] if e["employee_id"] == worker.id)
        assert row["vacation_days"] == 2
        assert row["sick_days"] == 1
        assert Decimal(row["vacation_amount"]) == Decimal("4790")   # 50000/167 × 16
        assert Decimal(row["sick_amount"]) == Decimal("2395")       # 50000/167 × 8
        assert Decimal(data["total_vacation_amount"]) == Decimal("4790")

    def test_statement_accrued_includes_absence_pay(
        self, client: TestClient, admin_abs: Employee, worker: Employee,
        calendar_2026: ProductionCalendar,
    ):
        headers = _auth(client, "absadmin@example.com", "admin123")
        client.put("/api/timesheet/absence", headers=headers, json={
            "employee_id": worker.id, "work_date": "2026-05-05", "kind": "vacation",
        })

        data = client.get("/api/timesheet/2026/5/statement", headers=headers).json()
        row = next(r for r in data["rows"] if r["employee_id"] == worker.id)
        assert row["vacation_days"] == 1
        assert Decimal(row["vacation_amount"]) == Decimal("2395")
        assert Decimal(row["accrued_total"]) == Decimal("2395")
        # вся сумма разнесена по юрлицам без потерь
        assert Decimal(row["distribution_total"]) == Decimal("2395")
