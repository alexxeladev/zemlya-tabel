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


# ── Отпуск/больничный совместителя: платит только основная позиция ────────────

class TestAbsencesOnlyFromPrimary:
    """task_positions_fixes п.1: отсутствие отмечено на человеке (он отсутствует
    на всех работах), но оплачивается ТОЛЬКО по основной позиции. Иначе
    совместитель получал бы отпускные столько раз, сколько у него мест."""

    @pytest.fixture
    def moonlighter(
        self, db_session: Session, schedule_5_2, companies, department, calendar_2026
    ):
        """Инженер (оклад 60000, основная) + электрик (оклад 30000)."""
        emp = Employee(
            full_name="Петров Пётр", tab_number="T-2",
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

    def _absent(self, db_session, emp, days: list[int], kind: str) -> None:
        from app.models.employee_absences import EmployeeAbsence

        for day in days:
            db_session.add(EmployeeAbsence(
                employee_id=emp.id, work_date=date(2026, 5, day), kind=kind,
            ))
        db_session.commit()

    def _payrolls(self, db_session, emp):
        from app.models.employee_absences import EmployeeAbsence

        absences = db_session.query(EmployeeAbsence).all()
        entries = db_session.query(TimesheetEntry).all()
        return {
            pos.id: calculate_position_payroll(
                emp, pos, [e for e in entries if e.position_id == pos.id],
                MAY_REAL, 2026, 5, absences=absences,
            )
            for pos in emp.active_positions
        }

    def test_vacation_paid_only_from_primary_position(
        self, db_session: Session, moonlighter, companies
    ):
        """5 дней отпуска: отпускные с оклада 60000, подработка — 0."""
        engineer, electrician = moonlighter.active_positions
        worked, vacation = MAY_WORKDAYS[:15], MAY_WORKDAYS[15:]
        add_hours(db_session, moonlighter, engineer, companies[0], worked)
        add_hours(db_session, moonlighter, electrician, companies[1], worked)
        self._absent(db_session, moonlighter, vacation, "vacation")

        by_position = self._payrolls(db_session, moonlighter)
        p_eng, p_ele = by_position[engineer.id], by_position[electrician.id]

        # Основная: оклад 60000 × 120/160 = 45000 + отпускные 60000/160 × 5 × 8
        assert p_eng.base_amount == Decimal("45000")
        assert p_eng.vacation_amount == Decimal("15000")
        assert p_eng.total_amount == Decimal("60000")

        # Совместительство: только оклад за отработанное, отпускных нет
        assert p_ele.base_amount == Decimal("22500")
        assert p_ele.vacation_amount == Decimal("0")
        assert p_ele.vacation_paid_days == 0
        assert p_ele.total_amount == Decimal("22500")

        # Дни отсутствия в строке видны у обеих позиций — человек отсутствовал
        assert p_eng.vacation_days == len(vacation) == p_ele.vacation_days

    def test_sick_paid_only_from_primary_and_limit_is_per_person(
        self, db_session: Session, moonlighter, companies
    ):
        """Больничный — тоже только с основной; годовой лимит на человека."""
        engineer, electrician = moonlighter.active_positions
        worked, sick = MAY_WORKDAYS[:15], MAY_WORKDAYS[15:]
        add_hours(db_session, moonlighter, engineer, companies[0], worked)
        add_hours(db_session, moonlighter, electrician, companies[1], worked)
        self._absent(db_session, moonlighter, sick, "sick")

        by_position = self._payrolls(db_session, moonlighter)
        p_eng, p_ele = by_position[engineer.id], by_position[electrician.id]

        assert p_eng.sick_paid_days == 5
        assert p_eng.sick_amount == Decimal("15000")
        assert p_ele.sick_amount == Decimal("0")
        assert p_ele.sick_paid_days == 0
        assert p_ele.sick_unpaid_days == 0
        # Лимит (10 дней в году) израсходован один раз, а не по разу на позицию
        assert p_eng.sick_limit_remaining == 5

    def test_statement_does_not_double_vacation_across_positions(
        self, db_session: Session, moonlighter, companies
    ):
        """Ведомость: сумма отпускных по человеку = отпускные основной позиции."""
        engineer, electrician = moonlighter.active_positions
        worked, vacation = MAY_WORKDAYS[:15], MAY_WORKDAYS[15:]
        add_hours(db_session, moonlighter, engineer, companies[0], worked)
        add_hours(db_session, moonlighter, electrician, companies[1], worked)
        self._absent(db_session, moonlighter, vacation, "vacation")

        summary = build_payroll_summary(
            db_session, [moonlighter], db_session.query(TimesheetEntry).all(), 2026, 5
        )
        by_position = {r.position_id: r for r in summary.employees}

        assert summary.total_vacation_amount == Decimal("15000")
        assert by_position[engineer.id].net_payout == Decimal("60000")
        assert by_position[electrician.id].net_payout == Decimal("22500")


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
        # Все 10 смен плановые → вся сумма в базе (task_shiftpay_addons)
        assert p.base_shifts == 10
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

    def test_hourly_overtime_is_counted_per_day_not_per_month(
        self, db_session: Session, schedule_5_2, companies
    ):
        """task_positions_fixes п.2: переработка почасовика — по дням, как у
        окладника. 10 дней по 12 ч = 120 ч при норме 160: по месячной норме
        переработки не было бы вовсе, по дням — 4 ч сверх смены каждый день."""
        emp = Employee(full_name="Почасовик", pay_type=PAY_TYPE_HOURLY,
                       hour_rate=Decimal("500"), overtime_coefficient=Decimal("1.5"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        add_hours(db_session, emp, emp.primary_position, companies[0],
                  MAY_WORKDAYS[:10], hours=12)

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())

        assert p.total_hours == Decimal("120")
        assert p.overtime_hours == Decimal("40")          # 10 дней × 4 ч
        assert p.base_amount == Decimal("40000")          # 80 ч × 500
        assert p.overtime_amount == Decimal("30000")      # 40 × 500 × 1.5
        assert p.total_amount == Decimal("70000")

    def test_hourly_underwork_does_not_offset_another_days_overtime(
        self, db_session: Session, schedule_5_2, companies
    ):
        """Недоработка одного дня не гасит переработку другого (как у окладной)."""
        emp = Employee(full_name="Почасовик", pay_type=PAY_TYPE_HOURLY,
                       hour_rate=Decimal("500"), overtime_coefficient=Decimal("1.5"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        # День 1: 12 ч (4 ч сверх смены), день 2: 4 ч (недоработка) — итого 16 ч
        add_hours(db_session, emp, emp.primary_position, companies[0],
                  [MAY_WORKDAYS[0]], hours=12)
        add_hours(db_session, emp, emp.primary_position, companies[0],
                  [MAY_WORKDAYS[1]], hours=4)

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())

        assert p.overtime_hours == Decimal("4")
        assert p.base_amount == Decimal("6000")           # 12 ч по ставке
        assert p.overtime_amount == Decimal("3000")       # 4 × 500 × 1.5

    def test_hourly_day_off_hours_are_not_all_overtime(
        self, db_session: Session, schedule_5_2, companies
    ):
        """Выход в свой выходной у почасовика — обычные часы по ставке: смена
        задаёт дневную норму в любой день, категории «вне графика» у него нет."""
        emp = Employee(full_name="Почасовик", pay_type=PAY_TYPE_HOURLY,
                       hour_rate=Decimal("500"), overtime_coefficient=Decimal("1.5"),
                       schedule_id=schedule_5_2.id, default_company_id=companies[0].id)
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        add_hours(db_session, emp, emp.primary_position, companies[0], [16], hours=8)

        p = self._position_payroll(db_session, emp, db_session.query(TimesheetEntry).all())

        assert p.overtime_hours == Decimal("0")
        assert p.off_schedule_hours == Decimal("0")
        assert p.base_amount == Decimal("4000")
        assert p.total_amount == Decimal("4000")

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


# ── CRUD позиций из карточки сотрудника (task_positions ч.B) ──────────────────

class TestPositionCrudApi:
    """Карточка: список рабочих мест, добавление совместительства,
    переназначение основной, удаление (основную нельзя)."""

    @pytest.fixture
    def employee(self, db_session: Session, schedule_5_2, companies, department) -> Employee:
        emp = Employee(
            full_name="Иванов И.И.", tab_number="T-900", position="Инженер",
            rate=Decimal("60000"), schedule_id=schedule_5_2.id,
            department_id=department.id, default_company_id=companies[0].id,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        return emp

    @pytest.fixture
    def auth(self, client, admin_user) -> dict:
        return {"Authorization": f"Bearer {get_token(client, 'admin@example.com', 'admin123')}"}

    def test_list_returns_primary_first(self, client, auth, employee):
        resp = client.get(f"/api/employees/{employee.id}/positions", headers=auth)
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["is_primary"] is True
        assert rows[0]["display_title"] == "Инженер"
        assert rows[0]["pay_type"] == PAY_TYPE_SALARY
        assert Decimal(rows[0]["rate"]) == Decimal("60000")

    def test_add_moonlighting_position(
        self, client, auth, employee, schedule_5_2, companies, department
    ):
        resp = client.post(
            f"/api/employees/{employee.id}/positions",
            headers=auth,
            json={
                "title": "Электрик", "pay_type": PAY_TYPE_PER_SHIFT,
                "shift_rate": "2500", "schedule_id": schedule_5_2.id,
                "department_id": department.id, "company_id": companies[1].id,
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["is_primary"] is False
        assert Decimal(created["shift_rate"]) == Decimal("2500")
        # Оклад чужого типа не сохраняется — иначе расчёт возьмёт не ту базу
        assert created["rate"] is None
        # Дефолты коэффициентов проставлены, как у обычного создания сотрудника
        assert Decimal(created["weekend_coefficient"]) == Decimal("1.5")
        assert Decimal(created["overtime_coefficient"]) == Decimal("1.5")

        rows = client.get(f"/api/employees/{employee.id}/positions", headers=auth).json()
        assert [r["display_title"] for r in rows] == ["Инженер", "Электрик"]

    def test_changing_pay_type_clears_other_base(self, client, auth, employee):
        pos_id = employee.primary_position.id
        resp = client.patch(
            f"/api/employees/{employee.id}/positions/{pos_id}",
            headers=auth,
            json={"pay_type": PAY_TYPE_HOURLY, "hour_rate": "450"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rate"] is None
        assert Decimal(resp.json()["hour_rate"]) == Decimal("450")

    def test_make_primary_moves_the_flag(self, client, auth, employee, db_session):
        side = client.post(
            f"/api/employees/{employee.id}/positions",
            headers=auth, json={"title": "Электрик", "rate": "30000"},
        ).json()
        resp = client.post(
            f"/api/employees/{employee.id}/positions/{side['id']}/make-primary",
            headers=auth,
        )
        assert resp.status_code == 200, resp.text
        flags = {r["display_title"]: r["is_primary"] for r in resp.json()}
        assert flags == {"Электрик": True, "Инженер": False}
        db_session.expire_all()
        # Плоские поля карточки читают ОСНОВНУЮ позицию — они переехали вместе с ней
        assert db_session.get(Employee, employee.id).rate == Decimal("30000")

    def test_primary_position_cannot_be_deleted(self, client, auth, employee):
        pos_id = employee.primary_position.id
        client.post(
            f"/api/employees/{employee.id}/positions",
            headers=auth, json={"title": "Электрик", "rate": "30000"},
        )
        resp = client.delete(f"/api/employees/{employee.id}/positions/{pos_id}", headers=auth)
        assert resp.status_code == 422
        assert "основную" in resp.json()["detail"]

    def test_last_position_cannot_be_deleted(self, client, auth, employee):
        """Даже неосновную: у сотрудника всегда есть хотя бы одно рабочее место."""
        pos_id = employee.primary_position.id
        resp = client.delete(f"/api/employees/{employee.id}/positions/{pos_id}", headers=auth)
        assert resp.status_code == 422

    def test_unused_position_is_deleted_but_used_one_is_deactivated(
        self, client, auth, employee, db_session, companies, schedule_5_2
    ):
        side = client.post(
            f"/api/employees/{employee.id}/positions",
            headers=auth,
            json={"title": "Электрик", "rate": "30000", "schedule_id": schedule_5_2.id,
                  "company_id": companies[1].id},
        ).json()
        # Без часов — удаляется физически
        resp = client.delete(f"/api/employees/{employee.id}/positions/{side['id']}", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["result"] == "deleted"

        # С часами — только деактивация, иначе история табеля осталась бы без места
        side2 = client.post(
            f"/api/employees/{employee.id}/positions",
            headers=auth,
            json={"title": "Сторож", "rate": "20000", "schedule_id": schedule_5_2.id,
                  "company_id": companies[1].id},
        ).json()
        db_session.add(TimesheetEntry(
            employee_id=employee.id, position_id=side2["id"],
            work_date=date(2026, 5, 12), company_id=companies[1].id, hours=8,
        ))
        db_session.commit()
        resp = client.delete(f"/api/employees/{employee.id}/positions/{side2['id']}", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["result"] == "deactivated"
        rows = client.get(f"/api/employees/{employee.id}/positions", headers=auth).json()
        assert [r["is_active"] for r in rows if r["display_title"] == "Сторож"] == [False]

    def test_non_admin_cannot_edit_positions(self, client, employee, manager_user, department):
        manager_user.managed_departments = [department]
        token = get_token(client, "manager@example.com", "manager123")
        headers = {"Authorization": f"Bearer {token}"}
        # Читать может — карточку он видит
        assert client.get(f"/api/employees/{employee.id}/positions", headers=headers).status_code == 200
        resp = client.post(
            f"/api/employees/{employee.id}/positions", headers=headers,
            json={"title": "Электрик", "rate": "30000"},
        )
        assert resp.status_code == 403

    def test_employee_read_includes_positions(self, client, auth, employee):
        client.post(
            f"/api/employees/{employee.id}/positions",
            headers=auth, json={"title": "Электрик", "rate": "30000"},
        )
        resp = client.get(f"/api/employees/{employee.id}", headers=auth)
        assert resp.status_code == 200
        titles = [p["display_title"] for p in resp.json()["positions"]]
        assert set(titles) == {"Инженер", "Электрик"}


# ── Табель: строки по позициям (task_positions ч.B) ──────────────────────────

class TestTimesheetRowsPerPosition:
    """Месячная выдача отдаёт ВИДИМЫЕ рабочие места, часы пишутся на конкретное."""

    @pytest.fixture
    def other_department(self, db_session: Session) -> Department:
        d = Department(name="Охрана", code="SEC")
        db_session.add(d)
        db_session.commit()
        db_session.refresh(d)
        return d

    @pytest.fixture
    def moonlighter(
        self, db_session: Session, schedule_5_2, companies, department, other_department
    ) -> Employee:
        emp = Employee(
            full_name="Совместитель", tab_number="T-011", position="Инженер",
            rate=Decimal("60000"), schedule_id=schedule_5_2.id,
            department_id=department.id, default_company_id=companies[0].id,
        )
        db_session.add(emp)
        db_session.commit()
        emp.positions.append(EmployeePosition(
            title="Электрик", rate=Decimal("30000"), schedule_id=schedule_5_2.id,
            department_id=other_department.id, company_id=companies[1].id,
        ))
        db_session.commit()
        db_session.refresh(emp)
        return emp

    def test_month_returns_visible_positions(
        self, client, admin_user, moonlighter, calendar_2026
    ):
        token = get_token(client, "admin@example.com", "admin123")
        resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        positions = resp.json()["positions_by_employee"][str(moonlighter.id)]
        assert [p["display_title"] for p in positions] == ["Инженер", "Электрик"]

    def test_department_filter_shows_only_its_position(
        self, client, admin_user, moonlighter, department, calendar_2026
    ):
        """В табеле отдела видна только позиция ЭТОГО отдела."""
        token = get_token(client, "admin@example.com", "admin123")
        resp = client.get(
            f"/api/timesheet/2026/5?department_id={department.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        positions = resp.json()["positions_by_employee"][str(moonlighter.id)]
        assert [p["display_title"] for p in positions] == ["Инженер"]

    def test_manager_sees_only_his_department_position(
        self, client, db_session: Session, moonlighter, other_department, calendar_2026
    ):
        manager = Employee(
            full_name="Начальник охраны", email="sec.mgr@example.com",
            hashed_password=hash_password("manager123"), role="manager",
            department_id=other_department.id,
        )
        manager.managed_departments = [other_department]
        db_session.add(manager)
        db_session.commit()

        token = get_token(client, "sec.mgr@example.com", "manager123")
        resp = client.get("/api/timesheet/2026/5", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        positions = resp.json()["positions_by_employee"][str(moonlighter.id)]
        assert [p["display_title"] for p in positions] == ["Электрик"]

    def test_hours_land_on_the_requested_position(
        self, client, admin_user, db_session: Session, moonlighter, companies, calendar_2026
    ):
        token = get_token(client, "admin@example.com", "admin123")
        engineer, electrician = moonlighter.active_positions
        resp = client.put(
            "/api/timesheet/cell",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "employee_id": moonlighter.id, "position_id": electrician.id,
                "work_date": "2026-05-12", "company_id": companies[1].id, "hours": 8,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["position_id"] == electrician.id

        # Часы одного рабочего места не попадают в другое
        entries = db_session.query(TimesheetEntry).all()
        assert [e.position_id for e in entries] == [electrician.id]
        assert engineer.id != electrician.id

    def test_autofill_uses_each_position_schedule(
        self, client, admin_user, db_session: Session, moonlighter, calendar_2026
    ):
        token = get_token(client, "admin@example.com", "admin123")
        resp = client.post(
            "/api/timesheet/autofill/preview",
            headers={"Authorization": f"Bearer {token}"},
            json={"year": 2026, "month": 5},
        )
        assert resp.status_code == 200, resp.text
        by_position: dict[int, int] = {}
        for cell in resp.json()["entries_to_create"]:
            if cell["employee_id"] != moonlighter.id:
                continue
            by_position[cell["position_id"]] = by_position.get(cell["position_id"], 0) + 1
        # Обе позиции заполняются по СВОЕМУ графику, каждая — 20 рабочих дней мая
        assert sorted(by_position.keys()) == sorted(p.id for p in moonlighter.active_positions)
        assert set(by_position.values()) == {len(MAY_WORKDAYS)}
