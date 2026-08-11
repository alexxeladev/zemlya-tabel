"""
Совместительство: позиции сотрудника (task_positions ч.A).

Проверяем три вещи, ради которых задача и делалась:
  * сотрудник с ОДНОЙ позицией считается ровно как раньше (регрессия);
  * у совместителя каждое рабочее место считается по своим окладу/графику/норме,
    и «к выплате» между позициями не суммируется;
  * три типа оплаты — окладная, посменная, почасовая.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.positions import (
    PAY_TYPE_HOURLY,
    PAY_TYPE_PER_SHIFT,
    PAY_TYPE_SALARY,
    EmployeePosition,
)
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.services.payroll import calculate_employee_payroll, calculate_position_payroll
from app.services.payroll_statement import build_payroll_statement, build_payroll_summary
from tests.conftest import get_token

# Май 2026: нерабочие 1,2,3,9,10,16,17,23,24,30,31 → 20 рабочих дней,
# норма для 8-часовой смены = 160 ч.
MAY_REAL = {
    "year": 2026,
    "months": [{"month": 5, "days": "1,2,3,9,10,16,17,23,24,30,31"}],
}
MAY_NORM = Decimal("160")
# Рабочие дни мая 2026 по этому календарю (Пн–Пт, без 1 мая).
MAY_WORKDAYS = [d for d in range(1, 32) if d not in (1, 2, 3, 9, 10, 16, 17, 23, 24, 30, 31)]


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def schedule_5_2(db_session: Session) -> Schedule:
    s = Schedule(
        name="5/2", hours_per_shift=8, schedule_type="weekday",
        work_weekdays=[0, 1, 2, 3, 4],
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def companies(db_session: Session) -> list[Company]:
    rows = [
        Company(code="ZMO", name="Земля МО"),
        Company(code="KFT", name="Комфорт"),
    ]
    db_session.add_all(rows)
    db_session.commit()
    for c in rows:
        db_session.refresh(c)
    return rows


@pytest.fixture
def department(db_session: Session) -> Department:
    d = Department(name="ИТО", code="ITO")
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def calendar_2026(db_session: Session) -> ProductionCalendar:
    cal = ProductionCalendar(year=2026, data=MAY_REAL, source="manual")
    db_session.add(cal)
    db_session.commit()
    return cal


def add_hours(
    db: Session, emp: Employee, position: EmployeePosition, company: Company,
    days: list[int], hours: int = 8,
) -> None:
    for day in days:
        db.add(TimesheetEntry(
            employee_id=emp.id, position_id=position.id, company_id=company.id,
            work_date=date(2026, 5, day), hours=hours,
        ))
    db.commit()


# ── Инвариант основной позиции ────────────────────────────────────────────────

class TestPrimaryPosition:
    def test_every_employee_gets_one_primary_position(self, db_session: Session):
        emp = Employee(full_name="Без полей")
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)

        assert len(emp.positions) == 1
        assert emp.primary_position.is_primary is True

    def test_flat_fields_read_and_write_primary_position(
        self, db_session: Session, schedule_5_2, companies, department
    ):
        """Старый «плоский» API карточки — это и есть основная позиция."""
        emp = Employee(
            full_name="Совместимость",
            rate=Decimal("50000"),
            schedule_id=schedule_5_2.id,
            department_id=department.id,
            default_company_id=companies[0].id,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)

        pos = emp.primary_position
        assert pos.rate == Decimal("50000.00")
        assert pos.schedule_id == schedule_5_2.id
        assert pos.department_id == department.id
        # У сотрудника default_company_id, у позиции — company_id: одно и то же.
        assert pos.company_id == companies[0].id

        # Обратное направление: правка «сотрудника» правит основную позицию.
        emp.rate = Decimal("60000")
        db_session.commit()
        db_session.refresh(emp)
        assert emp.primary_position.rate == Decimal("60000.00")

    def test_second_position_does_not_shift_flat_fields(
        self, db_session: Session, schedule_5_2, companies, department
    ):
        """Совместительство не меняет того, что видно через плоские поля."""
        emp = Employee(full_name="Совместитель", rate=Decimal("60000"),
                       schedule_id=schedule_5_2.id, department_id=department.id,
                       default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()

        emp.positions.append(EmployeePosition(
            title="Электрик", rate=Decimal("30000"),
            schedule_id=schedule_5_2.id, company_id=companies[1].id,
        ))
        db_session.commit()
        db_session.refresh(emp)

        assert len(emp.positions) == 2
        assert emp.rate == Decimal("60000.00")
        assert emp.default_company_id == companies[0].id


# ── Регрессия: одна позиция считается как раньше ──────────────────────────────

class TestSinglePositionRegression:
    def test_single_position_matches_legacy_numbers(
        self, db_session: Session, schedule_5_2, companies
    ):
        """Оклад 50000, отработана полная норма → полный оклад, как и до позиций."""
        emp = Employee(full_name="Окладник", rate=Decimal("50000"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        add_hours(db_session, emp, emp.primary_position, companies[0], MAY_WORKDAYS)

        entries = db_session.query(TimesheetEntry).all()
        p = calculate_employee_payroll(emp, entries, MAY_REAL, 2026, 5)

        assert p.is_calculable is True
        assert p.norm_hours == MAY_NORM
        assert p.total_hours == MAY_NORM
        assert p.base_amount == Decimal("50000")
        assert p.overtime_hours == Decimal("0")
        assert p.total_amount == Decimal("50000")
        # Строка знает своё рабочее место
        assert p.position_id == emp.primary_position.id
        assert p.is_primary_position is True

    def test_entries_without_position_belong_to_primary(
        self, db_session: Session, schedule_5_2, companies, calendar_2026
    ):
        """Часы, заведённые до появления позиций (position_id IS NULL), не теряются."""
        emp = Employee(full_name="Мигрированный", rate=Decimal("50000"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        for day in MAY_WORKDAYS:
            db_session.add(TimesheetEntry(
                employee_id=emp.id, position_id=None, company_id=companies[0].id,
                work_date=date(2026, 5, day), hours=8,
            ))
        db_session.commit()

        summary = build_payroll_summary(
            db_session, [emp], db_session.query(TimesheetEntry).all(), 2026, 5
        )
        assert len(summary.employees) == 1
        assert summary.employees[0].total_hours == MAY_NORM
        assert summary.employees[0].base_amount == Decimal("50000")


# ── Совместительство: две позиции с разными окладами ──────────────────────────

class TestTwoPositions:
    @pytest.fixture
    def moonlighter(
        self, db_session: Session, schedule_5_2, companies, department, calendar_2026
    ):
        """Инженер (оклад 60000) + электрик (оклад 30000) на разных юрлицах."""
        emp = Employee(
            full_name="Иванов Иван", tab_number="T-1",
            rate=Decimal("60000"), schedule_id=schedule_5_2.id,
            department_id=department.id, default_company_id=companies[0].id,
        )
        db_session.add(emp)
        db_session.commit()
        emp.primary_position.title = "Инженер"
        emp.positions.append(EmployeePosition(
            title="Электрик", rate=Decimal("30000"), schedule_id=schedule_5_2.id,
            department_id=department.id, company_id=companies[1].id,
        ))
        db_session.commit()
        db_session.refresh(emp)
        return emp

    def test_each_position_calculated_from_own_rate(
        self, db_session: Session, moonlighter, companies
    ):
        engineer, electrician = moonlighter.active_positions
        add_hours(db_session, moonlighter, engineer, companies[0], MAY_WORKDAYS)
        add_hours(db_session, moonlighter, electrician, companies[1], MAY_WORKDAYS)
        entries = db_session.query(TimesheetEntry).all()

        p_eng = calculate_position_payroll(
            moonlighter, engineer,
            [e for e in entries if e.position_id == engineer.id], MAY_REAL, 2026, 5,
        )
        p_ele = calculate_position_payroll(
            moonlighter, electrician,
            [e for e in entries if e.position_id == electrician.id], MAY_REAL, 2026, 5,
        )

        assert p_eng.base_amount == Decimal("60000")
        assert p_ele.base_amount == Decimal("30000")
        assert p_eng.position_title == "Инженер"
        assert p_ele.position_title == "Электрик"
        assert p_ele.is_primary_position is False

    def test_summary_returns_row_per_position(
        self, db_session: Session, moonlighter, companies
    ):
        engineer, electrician = moonlighter.active_positions
        add_hours(db_session, moonlighter, engineer, companies[0], MAY_WORKDAYS)
        add_hours(db_session, moonlighter, electrician, companies[1], MAY_WORKDAYS)

        summary = build_payroll_summary(
            db_session, [moonlighter], db_session.query(TimesheetEntry).all(), 2026, 5
        )

        assert len(summary.employees) == 2
        assert {r.position_id for r in summary.employees} == {engineer.id, electrician.id}
        by_position = {r.position_id: r for r in summary.employees}
        assert by_position[engineer.id].base_amount == Decimal("60000")
        assert by_position[electrician.id].base_amount == Decimal("30000")

    def test_net_payout_is_per_position_not_summed(
        self, db_session: Session, moonlighter, companies
    ):
        """«К выплате» считается по каждому месту отдельно — разные компании платят."""
        engineer, electrician = moonlighter.active_positions
        add_hours(db_session, moonlighter, engineer, companies[0], MAY_WORKDAYS)
        add_hours(db_session, moonlighter, electrician, companies[1], MAY_WORKDAYS)

        summary = build_payroll_summary(
            db_session, [moonlighter], db_session.query(TimesheetEntry).all(), 2026, 5
        )
        by_position = {r.position_id: r for r in summary.employees}

        assert by_position[engineer.id].net_payout == Decimal("60000")
        assert by_position[electrician.id].net_payout == Decimal("30000")

    def test_hours_of_one_position_do_not_leak_into_another(
        self, db_session: Session, moonlighter, companies
    ):
        """Часы электрика не должны попасть в норму/оклад инженера."""
        engineer, electrician = moonlighter.active_positions
        add_hours(db_session, moonlighter, engineer, companies[0], MAY_WORKDAYS)

        summary = build_payroll_summary(
            db_session, [moonlighter], db_session.query(TimesheetEntry).all(), 2026, 5
        )
        by_position = {r.position_id: r for r in summary.employees}

        assert by_position[engineer.id].total_hours == MAY_NORM
        assert by_position[electrician.id].total_hours == Decimal("0")
        # Совсем без часов оклад не начисляется
        assert by_position[electrician.id].base_amount == Decimal("0")

    def test_positions_have_independent_schedules_and_norms(
        self, db_session: Session, moonlighter, companies
    ):
        """У позиций разные графики → разные нормы, каждая считает свою."""
        part_time = Schedule(
            name="2/2 12ч", hours_per_shift=12, schedule_type="cyclic",
            cycle_start_date=date(2026, 5, 1), cycle_work_days=2, cycle_off_days=2,
        )
        db_session.add(part_time)
        db_session.commit()
        electrician = moonlighter.active_positions[1]
        electrician.schedule_id = part_time.id
        db_session.commit()
        db_session.refresh(moonlighter)

        summary = build_payroll_summary(db_session, [moonlighter], [], 2026, 5)
        by_position = {r.position_id: r for r in summary.employees}

        assert by_position[moonlighter.primary_position.id].norm_hours == MAY_NORM
        # 2/2 по 12 ч в мае 2026 → 16 смен × 12 = 192 ч, календарь не участвует
        assert by_position[electrician.id].norm_hours == Decimal("192")

    def test_adjustment_lands_on_its_own_position(
        self, db_session: Session, moonlighter, companies
    ):
        """Премия, выписанная на совместительство, не попадает в основную позицию."""
        engineer, electrician = moonlighter.active_positions
        add_hours(db_session, moonlighter, engineer, companies[0], MAY_WORKDAYS)
        add_hours(db_session, moonlighter, electrician, companies[1], MAY_WORKDAYS)
        db_session.add(EmployeeAdjustment(
            employee_id=moonlighter.id, position_id=electrician.id,
            year=2026, month=5, kind="premium", amount=Decimal("5000"),
            reason="За аварийный вызов",
        ))
        db_session.commit()

        summary = build_payroll_summary(
            db_session, [moonlighter], db_session.query(TimesheetEntry).all(), 2026, 5
        )
        by_position = {r.position_id: r for r in summary.employees}

        assert by_position[electrician.id].premium_amount == Decimal("5000")
        assert by_position[engineer.id].premium_amount == Decimal("0")
        assert by_position[electrician.id].net_payout == Decimal("35000")
        assert by_position[engineer.id].net_payout == Decimal("60000")

    def test_statement_has_row_per_position_with_own_company(
        self, db_session: Session, moonlighter, companies
    ):
        engineer, electrician = moonlighter.active_positions
        add_hours(db_session, moonlighter, engineer, companies[0], MAY_WORKDAYS)
        add_hours(db_session, moonlighter, electrician, companies[1], MAY_WORKDAYS)

        statement = build_payroll_statement(
            db_session, [moonlighter], db_session.query(TimesheetEntry).all(), 2026, 5
        )

        assert len(statement.rows) == 2
        by_position = {r.position_id: r for r in statement.rows}
        assert by_position[engineer.id].main_company_id == companies[0].id
        assert by_position[electrician.id].main_company_id == companies[1].id
        assert by_position[engineer.id].position == "Инженер"
        assert by_position[electrician.id].position == "Электрик"
        # Распределение по юрлицам — от данных позиции: ни на одном уровне
        # каскада % не задан, значит авто по часам своего рабочего места.
        assert by_position[engineer.id].distribution_total == Decimal("60000")
        assert by_position[electrician.id].distribution_total == Decimal("30000")


# ── Три типа оплаты ───────────────────────────────────────────────────────────

class TestPayTypes:
    def _position_payroll(self, db_session, emp, entries):
        return calculate_position_payroll(
            emp, emp.primary_position, entries, MAY_REAL, 2026, 5,
        )

    def test_salary_full_norm_gives_full_salary(
        self, db_session: Session, schedule_5_2, companies
    ):
        emp = Employee(full_name="Окладник", pay_type=PAY_TYPE_SALARY,
                       rate=Decimal("50000"), schedule_id=schedule_5_2.id,
                       default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        add_hours(db_session, emp, emp.primary_position, companies[0], MAY_WORKDAYS)

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())
        assert p.pay_type == PAY_TYPE_SALARY
        assert p.base_amount == Decimal("50000")

    def test_salary_is_guaranteed_but_hourly_is_not(
        self, db_session: Session, schedule_5_2, companies
    ):
        """Ключевое отличие: окладник за полную норму получает оклад целиком,
        почасовик — ровно за отработанные часы."""
        half = MAY_WORKDAYS[:10]  # 10 дней × 8 ч = 80 ч из 160

        salaried = Employee(full_name="Окладник", pay_type=PAY_TYPE_SALARY,
                            rate=Decimal("50000"), schedule_id=schedule_5_2.id,
                            default_company_id=companies[0].id)
        hourly = Employee(full_name="Почасовик", pay_type=PAY_TYPE_HOURLY,
                          hour_rate=Decimal("500"), schedule_id=schedule_5_2.id,
                          default_company_id=companies[0].id)
        db_session.add_all([salaried, hourly])
        db_session.commit()
        db_session.refresh(salaried)
        db_session.refresh(hourly)
        add_hours(db_session, salaried, salaried.primary_position, companies[0], half)
        add_hours(db_session, hourly, hourly.primary_position, companies[0], half)

        entries = db_session.query(TimesheetEntry).all()
        p_sal = calculate_position_payroll(
            salaried, salaried.primary_position,
            [e for e in entries if e.employee_id == salaried.id], MAY_REAL, 2026, 5,
        )
        p_hr = calculate_position_payroll(
            hourly, hourly.primary_position,
            [e for e in entries if e.employee_id == hourly.id], MAY_REAL, 2026, 5,
        )

        # Окладник: 50000 × 80/160 = 25000
        assert p_sal.base_amount == Decimal("25000")
        # Почасовик: 80 ч × 500 = 40000, ровно за факт
        assert p_hr.base_amount == Decimal("40000")

    def test_per_shift_counts_shifts(self, db_session: Session, schedule_5_2, companies):
        emp = Employee(full_name="Сменщик", pay_type=PAY_TYPE_PER_SHIFT,
                       shift_rate=Decimal("3000"), schedule_id=schedule_5_2.id,
                       default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        add_hours(db_session, emp, emp.primary_position, companies[0], MAY_WORKDAYS[:10])

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())
        assert p.pay_type == PAY_TYPE_PER_SHIFT
        assert p.worked_shifts == 10
        assert p.base_amount == Decimal("30000")

    def test_hourly_base_and_overtime(self, db_session: Session, schedule_5_2, companies):
        """Почасовая: часы в пределах нормы × ставка + сверх нормы × ставка × коэф."""
        emp = Employee(full_name="Почасовик", pay_type=PAY_TYPE_HOURLY,
                       hour_rate=Decimal("500"), overtime_coefficient=Decimal("1.5"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        # 20 рабочих дней по 9 ч = 180 ч при норме 160 → 20 ч переработки
        add_hours(db_session, emp, emp.primary_position, companies[0], MAY_WORKDAYS, hours=9)

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())

        assert p.pay_type == PAY_TYPE_HOURLY
        assert p.hour_rate == Decimal("500")
        assert p.total_hours == Decimal("180")
        assert p.overtime_hours == Decimal("20")
        # база 160 × 500 = 80000, переработка 20 × 500 × 1.5 = 15000
        assert p.base_amount == Decimal("80000")
        assert p.overtime_amount == Decimal("15000")
        assert p.total_amount == Decimal("95000")
        # Оклада у почасовика нет
        assert p.rate is None

    def test_hourly_overtime_coefficient_zero(
        self, db_session: Session, schedule_5_2, companies
    ):
        emp = Employee(full_name="Без переработки", pay_type=PAY_TYPE_HOURLY,
                       hour_rate=Decimal("500"), overtime_coefficient=Decimal("0"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        add_hours(db_session, emp, emp.primary_position, companies[0], MAY_WORKDAYS, hours=9)

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())
        assert p.overtime_hours == Decimal("20")
        assert p.overtime_amount == Decimal("0")
        assert p.total_amount == Decimal("80000")

    def test_hourly_gets_no_vacation_or_sick_pay(
        self, db_session: Session, schedule_5_2, companies
    ):
        """Отпуск/больничный почасовику не начисляются — только отметка в табеле."""
        from app.models.employee_absences import EmployeeAbsence

        emp = Employee(full_name="Почасовик в отпуске", pay_type=PAY_TYPE_HOURLY,
                       hour_rate=Decimal("500"), schedule_id=schedule_5_2.id,
                       default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        worked, vacation = MAY_WORKDAYS[:10], MAY_WORKDAYS[10:]
        add_hours(db_session, emp, emp.primary_position, companies[0], worked)
        for day in vacation:
            db_session.add(EmployeeAbsence(
                employee_id=emp.id, work_date=date(2026, 5, day), kind="vacation",
            ))
        db_session.commit()

        p = calculate_position_payroll(
            emp, emp.primary_position, db_session.query(TimesheetEntry).all(),
            MAY_REAL, 2026, 5,
            absences=db_session.query(EmployeeAbsence).all(),
        )

        assert p.vacation_days == len(vacation)
        assert p.vacation_amount == Decimal("0")
        assert p.sick_amount == Decimal("0")
        assert p.total_amount == Decimal("40000")  # только отработанные 80 ч

    def test_hourly_without_rate_not_calculable(
        self, db_session: Session, schedule_5_2, companies
    ):
        emp = Employee(full_name="Без ставки", pay_type=PAY_TYPE_HOURLY,
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)

        p = self._position_payroll(db_session, emp, [])
        assert p.is_calculable is False
        assert p.reason_if_not_calculable == "Не задана ставка за час"

    def test_two_positions_with_different_pay_types(
        self, db_session: Session, schedule_5_2, companies, calendar_2026
    ):
        """Оклад на основной + почасовая подработка — считаются независимо."""
        emp = Employee(full_name="Смешанный", rate=Decimal("50000"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        emp.positions.append(EmployeePosition(
            title="Подработка", pay_type=PAY_TYPE_HOURLY, hour_rate=Decimal("400"),
            schedule_id=schedule_5_2.id, company_id=companies[1].id,
        ))
        db_session.commit()
        db_session.refresh(emp)

        main, side = emp.active_positions
        add_hours(db_session, emp, main, companies[0], MAY_WORKDAYS)
        add_hours(db_session, emp, side, companies[1], MAY_WORKDAYS[:5], hours=4)

        summary = build_payroll_summary(
            db_session, [emp], db_session.query(TimesheetEntry).all(), 2026, 5
        )
        by_position = {r.position_id: r for r in summary.employees}

        assert by_position[main.id].pay_type == PAY_TYPE_SALARY
        assert by_position[main.id].base_amount == Decimal("50000")
        assert by_position[side.id].pay_type == PAY_TYPE_HOURLY
        # 5 дней × 4 ч = 20 ч × 400 = 8000
        assert by_position[side.id].base_amount == Decimal("8000")


# ── Видимость позиций по ролям ────────────────────────────────────────────────

class TestPositionVisibility:
    def test_manager_does_not_see_moonlighting_in_foreign_department(
        self, db_session: Session, client, schedule_5_2, companies, department, calendar_2026
    ):
        """Подработка в чужом отделе менеджеру не показывается."""
        other = Department(name="Охрана", code="SEC")
        db_session.add(other)
        db_session.commit()

        manager = Employee(
            full_name="Менеджер ИТО", email="mgr.pos@example.com",
            hashed_password=hash_password("manager123"), role="manager",
            department_id=department.id,
        )
        manager.managed_departments = [department]
        emp = Employee(full_name="Совместитель", rate=Decimal("60000"),
                       schedule_id=schedule_5_2.id, department_id=department.id,
                       default_company_id=companies[0].id)
        db_session.add_all([manager, emp])
        db_session.commit()
        emp.positions.append(EmployeePosition(
            title="Охранник", rate=Decimal("20000"), schedule_id=schedule_5_2.id,
            department_id=other.id, company_id=companies[1].id,
        ))
        db_session.commit()

        token = get_token(client, "mgr.pos@example.com", "manager123")
        resp = client.get(
            "/api/timesheet/2026/5/payroll",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        rows = [r for r in resp.json()["employees"] if r["employee_id"] == emp.id]
        assert len(rows) == 1
        assert rows[0]["position_title"] != "Охранник"

    def test_admin_sees_all_positions(
        self, db_session: Session, client, admin_user, schedule_5_2, companies,
        department, calendar_2026,
    ):
        emp = Employee(full_name="Совместитель", rate=Decimal("60000"),
                       schedule_id=schedule_5_2.id, department_id=department.id,
                       default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        emp.positions.append(EmployeePosition(
            title="Электрик", rate=Decimal("30000"), schedule_id=schedule_5_2.id,
            department_id=department.id, company_id=companies[1].id,
        ))
        db_session.commit()

        token = get_token(client, "admin@example.com", "admin123")
        resp = client.get(
            "/api/timesheet/2026/5/payroll",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        rows = [r for r in resp.json()["employees"] if r["employee_id"] == emp.id]
        assert len(rows) == 2
