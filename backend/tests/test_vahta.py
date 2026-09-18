"""Модуль «Вахта»: справочник, табель, замена, врезка в ведомость и права.

Проверочные примеры Караулова и Дозорова — в `test_vahta_payroll.py` (чистый
расчёт) и здесь же на уровне ведомости: цифры обязаны сойтись в обоих местах,
иначе экран и выгрузка разъедутся.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.guard_assignments import GuardAssignment
from app.models.guard_posts import (
    GuardCrew,
    GuardCrewShare,
    GuardPost,
    GuardSite,
    GuardSiteShare,
    GuardZone,
)
from app.models.positions import EmployeePosition
from app.services.guard_duty import (
    create_assignment,
    next_tab_number,
    quick_hire,
    replace_on_post,
    set_days,
)
from app.services.payroll_statement import build_payroll_statement
from tests.conftest import get_token


def _title_id(db: Session, name: str) -> int:
    """id должности охраны по названию — справочник сеет conftest."""
    from app.models.guard_job_titles import GuardJobTitle

    return db.query(GuardJobTitle).filter(GuardJobTitle.name == name).one().id

YEAR, MONTH = 2026, 8
ALL_DAYS = set(range(1, 32))
FIRST_HALF = set(range(1, 16))


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def companies(db_session: Session) -> dict[str, Company]:
    made = {}
    for i, (code, name) in enumerate(
        [("ZMO", "ЗМО"), ("EKS", "Эксплуатация"), ("SEC", "Секьюрити")], start=1
    ):
        c = Company(code=code, name=name, is_active=True, sort_order=i)
        db_session.add(c)
        made[code] = c
    db_session.commit()
    for c in made.values():
        db_session.refresh(c)
    return made


@pytest.fixture
def guard_dept(db_session: Session) -> Department:
    d = Department(
        name="СБ Охрана", code="SEC-OHR", is_active=True, is_guard_department=True
    )
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


@pytest.fixture
def other_dept(db_session: Session) -> Department:
    """Обычный отдел без флага — регрессия: модуль его не видит."""
    d = Department(name="ИТО", code="IT", is_active=True)
    db_session.add(d)
    db_session.commit()
    db_session.refresh(d)
    return d


def _make_zone(db: Session, dept: Department, name: str = "Зона 1") -> GuardZone:
    """Зона обслуживания — верхний уровень: отдел задаётся здесь."""
    zone = GuardZone(name=name, department_id=dept.id, sort_order=1)
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


def _make_crew(
    db: Session, zone: GuardZone, name: str, rate: str,
    shares: dict[int, str] | None = None,
) -> GuardCrew:
    """Выездной экипаж ГБР зоны: своя ставка и своё распределение."""
    crew = GuardCrew(
        name=name, zone_id=zone.id, shift_rate=Decimal(rate), sort_order=1
    )
    db.add(crew)
    db.commit()
    db.refresh(crew)
    for company_id, percent in (shares or {}).items():
        db.add(GuardCrewShare(
            crew_id=crew.id, company_id=company_id, percent=Decimal(percent)
        ))
    db.commit()
    db.refresh(crew)
    return crew


def _make_site(
    db: Session, zone: GuardZone, name: str, rate: str,
    shares: dict[int, str] | None = None,
) -> GuardSite:
    """Охраняемый объект внутри зоны: ставка по умолчанию и распределение."""
    site = GuardSite(name=name, zone_id=zone.id, shift_rate=Decimal(rate))
    db.add(site)
    db.commit()
    db.refresh(site)
    for company_id, percent in (shares or {}).items():
        db.add(GuardSiteShare(
            site_id=site.id, company_id=company_id, percent=Decimal(percent)
        ))
    db.commit()
    db.refresh(site)
    return site


def _make_post(
    db: Session, site: GuardSite, name: str, rate: str | None = None,
) -> GuardPost:
    """Пост внутри объекта: только название точки и, если надо, своя ставка.

    Должности у поста НЕТ — она у строки табеля.
    """
    post = GuardPost(
        site_id=site.id, name=name,
        shift_rate=Decimal(rate) if rate is not None else None,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@pytest.fixture
def zone(db_session, guard_dept) -> GuardZone:
    return _make_zone(db_session, guard_dept)


@pytest.fixture
def crew(db_session, zone, companies) -> GuardCrew:
    """Экипаж ГБР с мультикомпанийным раскладом 35/60/5 — как в образце."""
    return _make_crew(
        db_session, zone, "1 экипаж", "5000",
        {
            companies["ZMO"].id: "35",
            companies["EKS"].id: "60",
            companies["SEC"].id: "5",
        },
    )


@pytest.fixture
def gbr_place(db_session, zone, crew, companies) -> GuardCrew:
    """Место работы выездного ГБР — сам экипаж, поста у него нет."""
    return crew


@pytest.fixture
def site(db_session, zone, crew, companies) -> GuardSite:
    """Объект «Green Wood» в той же зоне: 100 % на одно юрлицо."""
    return _make_site(
        db_session, zone, "Green Wood", "4000", {companies["SEC"].id: "100"},
    )


@pytest.fixture
def guard_post(db_session, site) -> GuardPost:
    """Рядовой пост объекта."""
    return _make_post(db_session, site, "GW 1")


def _employee(db: Session, name: str, dept: Department, tab: str) -> Employee:
    emp = Employee(full_name=name, tab_number=tab, is_active=True)
    db.add(emp)
    db.commit()
    position = emp.ensure_primary_position()
    position.department_id = dept.id
    position.pay_type = "per_shift"
    position.shift_rate = Decimal("5000")
    db.commit()
    db.refresh(emp)
    return emp


@pytest.fixture
def rodionov(db_session, guard_dept) -> Employee:
    return _employee(db_session, "Караулов Олег Петрович", guard_dept, "0000-90274")


# ── Справочник ────────────────────────────────────────────────────────────────

class TestReference:
    def test_crew_carries_rate_and_shares(self, gbr_place, companies):
        """Проценты выездного экипажа — свои, мультикомпанийные."""
        assert gbr_place.shift_rate == Decimal("5000")
        assert {s.company_id: s.percent for s in gbr_place.shares} == {
            companies["ZMO"].id: Decimal("35.000"),
            companies["EKS"].id: Decimal("60.000"),
            companies["SEC"].id: Decimal("5.000"),
        }

    def test_site_carries_rate_and_shares(self, site, companies):
        """У объекта своя ставка и своё распределение — источник % для постов."""
        assert site.shift_rate == Decimal("4000")
        assert {s.company_id for s in site.shares} == {companies["SEC"].id}

    def test_crew_belongs_to_a_zone(self, db_session, crew, site, zone):
        """Экипаж принадлежит зоне; объекты зоны — те, на которые он выезжает."""
        db_session.refresh(crew)
        assert crew.zone_id == zone.id
        assert [s.name for s in crew.zone.sites] == ["Green Wood"]

    def test_zone_may_hold_several_crews(self, db_session, zone, crew, companies):
        """В зоне экипажей может быть несколько — за её пределы они не выезжают."""
        second = _make_crew(db_session, zone, "2 экипаж", "4500",
                            {companies["SEC"].id: "100"})
        db_session.refresh(zone)
        assert {c.name for c in zone.crews} == {"1 экипаж", "2 экипаж"}
        assert second.department_id == zone.department_id

    def test_site_takes_department_from_zone(self, site, zone):
        """Отдел задаётся у зоны — у объекта своего нет."""
        assert site.department_id == zone.department_id

    def test_post_inherits_site_rate(self, db_session, site):
        """Ставка поста не задана — применяется ставка объекта."""
        post = _make_post(db_session, site, "GW 2")
        assert post.shift_rate is None
        assert post.effective_rate == Decimal("4000.00")

    def test_post_rate_overrides_the_site(self, db_session, site):
        """Точка может стоить дороже остальных — своя ставка поста."""
        post = _make_post(db_session, site, "КПП", "4500")
        assert post.effective_rate == Decimal("4500.00")

    def test_post_has_no_job_title(self, db_session, zone):
        rukovo = _make_site(db_session, zone, "Рук-во", "135000")
        chief = _make_post(db_session, rukovo, "Начальник")
        # Ставка не задана на посту — берётся у объекта (135 000 за месяц).
        assert chief.effective_rate == Decimal("135000.00")
        # А должность у поста не спрашивают: её носит человек.
        assert not hasattr(chief, "kind")


# ── Табель: назначения и дни ──────────────────────────────────────────────────

class TestAssignments:
    def test_new_assignment_marks_all_days(self, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()
        assert len(assignment.shifts) == 31

    def test_rate_defaults_to_the_place(self, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()
        assert assignment.rate == Decimal("5000.00")

    def test_empty_slot_is_allowed(self, db_session, gbr_place):
        """Незанятый пост: ставка есть, человека нет — так в образце."""
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place, position=None,
        )
        db_session.commit()
        assert assignment.position_id is None
        assert assignment.employee is None

    def test_set_days_removes_unmarked(self, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        set_days(db_session, assignment, FIRST_HALF)
        db_session.commit()
        assert len(assignment.shifts) == 15


# ── Замена на посту ───────────────────────────────────────────────────────────

class TestReplacement:
    def test_replacement_splits_days_into_two_rows(
        self, db_session, gbr_place, rodionov, guard_dept
    ):
        """Было одна строка — стало две с разделёнными днями (п.8)."""
        successor_emp = _employee(db_session, "Заставин А.", guard_dept, "T-0002")
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()

        kept, new_row = replace_on_post(
            db_session, assignment,
            position=successor_emp.primary_position, from_day=16,
        )
        db_session.commit()

        assert new_row is not None
        assert {s.work_date.day for s in kept.shifts} == set(range(1, 16))
        assert {s.work_date.day for s in new_row.shifts} == set(range(16, 32))
        assert new_row.crew_id == kept.crew_id
        assert new_row.position_id == successor_emp.primary_position.id

    def test_replacement_from_first_day_only_swaps_person(
        self, db_session, gbr_place, rodionov, guard_dept
    ):
        """Прежнему не остаётся дней — второй строки нет, меняется человек."""
        successor_emp = _employee(db_session, "Постнов С.", guard_dept, "T-0003")
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()

        kept, new_row = replace_on_post(
            db_session, assignment,
            position=successor_emp.primary_position, from_day=1,
        )
        db_session.commit()

        assert new_row is None
        assert kept.position_id == successor_emp.primary_position.id
        assert db_session.query(GuardAssignment).count() == 1

    def test_replacement_keeps_the_same_place(
        self, db_session, gbr_place, rodionov, guard_dept
    ):
        successor_emp = _employee(db_session, "Обходов Е.", guard_dept, "T-0004")
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()
        _, new_row = replace_on_post(
            db_session, assignment,
            position=successor_emp.primary_position, from_day=20,
        )
        db_session.commit()
        assert new_row.crew_id == gbr_place.id
        assert new_row.post_id is None


# ── Совместительство ──────────────────────────────────────────────────────────

class TestMoonlighting:
    def test_two_posts_give_two_rows_under_one_tab_number(
        self, db_session, guard_dept, zone, crew, companies, rodionov
    ):
        """Караулов в образце занимает две позиции ГБР под одним таб.№ (п.9)."""
        gw = _make_site(
            db_session, zone, "Green Wood", "5000", {companies["SEC"].id: "100"},
        )
        first = _make_post(db_session, gw, "GW 1", "5000")
        second = _make_post(db_session, gw, "GW 2", "3500")
        extra = EmployeePosition(
            employee_id=rodionov.id, title="ГБР", department_id=guard_dept.id,
            pay_type="per_shift", shift_rate=Decimal("3500"),
        )
        db_session.add(extra)
        db_session.commit()

        create_assignment(
            db_session, year=YEAR, month=MONTH, place=first,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=second,
            position=extra, days=FIRST_HALF,
        )
        db_session.commit()

        statement = build_payroll_statement(
            db_session, [rodionov], [], YEAR, MONTH
        )
        rows = [r for r in statement.rows if r.employee_id == rodionov.id]
        assert len(rows) == 2
        assert {r.tab_number for r in rows} == {"0000-90274"}
        assert {r.base_salary for r in rows} == {
            Decimal("75000.00"), Decimal("52500.00"),
        }


# ── Врезка в общую ведомость ──────────────────────────────────────────────────

class TestStatementIntegration:
    def test_rodionov_check_example(
        self, db_session, gbr_place, rodionov, companies
    ):
        """15 смен × 5 000 = 75 000, премия 230, итого 75 230 → 35/60/5 (п.4)."""
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        assignment.premium_h1 = Decimal("230")
        db_session.commit()

        statement = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH)
        row = statement.rows[0]

        assert row.base_salary == Decimal("75000.00")
        assert row.premium_amount == Decimal("230")
        assert row.accrued_total == Decimal("75230.00")

        amounts = {d.company_id: d.amount for d in row.distribution}
        assert amounts[companies["ZMO"].id] == Decimal("26330.50")
        assert amounts[companies["EKS"].id] == Decimal("45138.00")
        assert amounts[companies["SEC"].id] == Decimal("3761.50")
        assert row.distribution_total == Decimal("75230.00")
        assert row.unallocated_remainder == Decimal("0")

    def test_semencov_check_example(
        self, db_session, zone, guard_dept, crew, companies
    ):
        """15 × 4 500 = 67 500, премия 115, итого 67 615, «Секьюрити 100 %» (п.5)."""
        site = _make_site(
            db_session, zone, "КП Олимп", "4500", {companies["SEC"].id: "100"},
        )
        post = _make_post(db_session, site, "КП Олимп")
        emp = _employee(db_session, "Дозоров Игорь Сергеевич", guard_dept, "T-0011")
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=post,
            position=emp.primary_position, days=FIRST_HALF,
        )
        assignment.premium_h1 = Decimal("115")
        db_session.commit()

        statement = build_payroll_statement(db_session, [emp], [], YEAR, MONTH)
        row = statement.rows[0]
        assert row.accrued_total == Decimal("67615.00")
        amounts = {d.company_id: d.amount for d in row.distribution}
        assert amounts == {companies["SEC"].id: Decimal("67615.00")}

    def test_distribution_base_includes_premium(
        self, db_session, gbr_place, rodionov, companies
    ):
        """База — «Итого начислено», не одна зарплата (п.6)."""
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        assignment.premium_h1 = Decimal("230")
        db_session.commit()
        statement = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH)
        amounts = {d.company_id: d.amount for d in statement.rows[0].distribution}
        # Без премии в базе вышло бы ровно 26 250 — это и была бы ошибка.
        assert amounts[companies["ZMO"].id] != Decimal("26250.00")

    def test_penalty_reduces_accrued(self, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        assignment.premium_h1 = Decimal("230")
        assignment.penalty_h1 = Decimal("1000")
        db_session.commit()
        statement = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH)
        assert statement.rows[0].accrued_total == Decimal("74230.00")

    def test_net_payout_rounds_up_to_500(self, db_session, gbr_place, rodionov):
        """«К выплате» вахты — вверх до 500 ₽, а не к ближайшей тысяче."""
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        assignment.premium_h1 = Decimal("230")
        assignment.official_payout_h1 = Decimal("25230.50")
        db_session.commit()
        statement = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH)
        row = statement.rows[0]
        assert row.net_payout_exact == Decimal("49999.50")
        assert row.net_payout == Decimal("50000")
        assert row.rounding_tail == Decimal("-0.50")
        # Разнесение от округления не зависит: база — начислено + налог.
        assert row.accrued_total == Decimal("75230")

    def test_round_the_clock_hours(self, db_session, gbr_place, rodionov):
        """Норма — все часы месяца (744), факт — смены × 24 (15 → 360)."""
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        statement = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH)
        row = statement.rows[0]
        assert row.norm_hours == Decimal("744")
        assert row.fact_hours == Decimal("360")
        assert row.norm_days == 31
        assert row.fact_days == 15

    def test_distribution_source_is_the_post(self, db_session, gbr_place, rodionov):
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        db_session.commit()
        statement = build_payroll_statement(db_session, [rodionov], [], YEAR, MONTH)
        assert statement.rows[0].distribution_source == "guard_post"

    def test_replacement_rows_sum_into_one_statement_row(
        self, db_session, gbr_place, rodionov, guard_dept
    ):
        """Замена делит дни, но у рабочего места строка в ведомости одна."""
        successor = _employee(db_session, "Сменщик С.", guard_dept, "T-0021")
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=ALL_DAYS,
        )
        db_session.commit()
        replace_on_post(
            db_session, assignment,
            position=successor.primary_position, from_day=16,
        )
        db_session.commit()

        statement = build_payroll_statement(
            db_session, [rodionov, successor], [], YEAR, MONTH
        )
        by_emp = {r.employee_id: r for r in statement.rows}
        assert by_emp[rodionov.id].fact_days == 15
        assert by_emp[successor.id].fact_days == 16


class TestOrdinaryPayrollUntouched:
    """Строки остальных подразделений вахта не меняет ни на рубль."""

    def test_non_guard_employee_is_calculated_as_before(
        self, db_session, other_dept, companies
    ):
        emp = Employee(full_name="Обычный Сотрудник", tab_number="T-0100", is_active=True)
        db_session.add(emp)
        db_session.commit()
        position = emp.ensure_primary_position()
        position.department_id = other_dept.id
        position.pay_type = "salary"
        position.rate = Decimal("50000")
        position.company_id = companies["ZMO"].id
        db_session.commit()

        statement = build_payroll_statement(db_session, [emp], [], YEAR, MONTH)
        row = statement.rows[0]
        assert row.distribution_source != "guard_post"
        # Расчёт без графика/календаря как и раньше: нечего считать.
        assert row.accrued_total == Decimal("0")

    def test_guard_penalty_is_zero_for_everyone_else(
        self, db_session, other_dept, companies
    ):
        emp = Employee(full_name="Второй Сотрудник", tab_number="T-0101", is_active=True)
        db_session.add(emp)
        db_session.commit()
        emp.ensure_primary_position().department_id = other_dept.id
        db_session.commit()
        statement = build_payroll_statement(db_session, [emp], [], YEAR, MONTH)
        assert statement.rows[0].accrued_total == Decimal("0")


# ── Быстрый найм ──────────────────────────────────────────────────────────────

class TestJobTitleOnTheRow:
    """Должность принадлежит СТРОКЕ, а не посту (task_vahta, третья правка модели).

    Точка не бывает «охранником»: на «КП Олимп» в образце на одном посту стоят
    двое ГБР по 4 500 и трое охранников по 3 000.
    """

    def test_one_post_holds_different_titles(
        self, client, users, db_session, zone, companies, guard_dept
    ):
        site = _make_site(
            db_session, zone, "КП Олимп", "3000", {companies["SEC"].id: "100"}
        )
        post = _make_post(db_session, site, "КП Олимп")
        gbr = _employee(db_session, "Смотров П.", guard_dept, "T-0501")
        guard = _employee(db_session, "Будкин А.", guard_dept, "T-0502")

        a = create_assignment(
            db_session, year=YEAR, month=MONTH, place=post,
            position=gbr.primary_position, job_title_id=_title_id(db_session, "ГБР"), rate=Decimal("4500"),
        )
        b = create_assignment(
            db_session, year=YEAR, month=MONTH, place=post,
            position=guard.primary_position, job_title_id=_title_id(db_session, "Охранник"),
        )
        db_session.commit()

        assert a.post_id == b.post_id           # пост ОДИН
        assert a.job_title_name == "ГБР"
        assert b.job_title_name == "Охранник"
        assert a.rate == Decimal("4500.00")
        assert b.rate == Decimal("3000.00")     # от объекта

    def test_default_title_comes_from_the_place(
        self, db_session, gbr_place, guard_post, guard_dept
    ):
        """Не задали — берётся обычная для места: у экипажа ГБР, у поста охранник."""
        emp = _employee(db_session, "Без Должности", guard_dept, "T-0503")
        in_crew = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=emp.primary_position,
        )
        on_post = create_assignment(
            db_session, year=YEAR, month=MONTH, place=guard_post, position=None,
        )
        db_session.commit()
        assert in_crew.job_title_name == "ГБР"
        assert on_post.job_title_name == "Охранник"

    def test_chief_pay_follows_the_title(self, db_session, zone, companies, guard_dept):
        """Способ оплаты выводится из должности строки, а не из поста."""
        site = _make_site(
            db_session, zone, "Рук-во", "135000", {companies["SEC"].id: "100"}
        )
        post = _make_post(db_session, site, "Рук-во")
        emp = _employee(db_session, "Сторожев П.", guard_dept, "T-0504")
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=post,
            position=emp.primary_position, job_title_id=_title_id(db_session, "Начальник охраны"), days=FIRST_HALF,
        )
        db_session.commit()
        statement = build_payroll_statement(db_session, [emp], [], YEAR, MONTH)
        # Полмесяца фикс-оклада: 135 000 / 2.
        assert statement.rows[0].accrued_total == Decimal("67500.00")

    def test_title_is_editable_in_the_row(self, client, users, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()
        resp = client.patch(
            f"/api/vahta/assignments/{assignment.id}",
            json={"job_title_id": _title_id(db_session, "Диспетчер")}, headers=_auth(client, "manager"),
        )
        assert resp.status_code == 200
        db_session.refresh(assignment)
        assert assignment.job_title_name == "Диспетчер"

    def test_unknown_title_is_rejected(self, client, users, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position,
        )
        db_session.commit()
        resp = client.patch(
            f"/api/vahta/assignments/{assignment.id}",
            json={"job_title_id": 999999}, headers=_auth(client, "manager"),
        )
        assert resp.status_code == 422


class TestBatchAssignment:
    """Несколько человек на один пост одним действием (окно «Поставить людей»)."""

    def test_batch_creates_a_row_per_person(
        self, client, users, db_session, gbr_place, guard_dept
    ):
        people = [
            _employee(db_session, f"Боец {i}", guard_dept, f"T-04{i:02d}")
            for i in range(3)
        ]
        resp = client.post(
            "/api/vahta/assignments",
            json={
                "year": YEAR, "month": MONTH, "crew_id": gbr_place.id,
                "employee_ids": [e.id for e in people],
            },
            headers=_auth(client, "manager"),
        )
        assert resp.status_code == 201
        assert resp.json()["created"] == 3
        rows = db_session.query(GuardAssignment).filter_by(crew_id=gbr_place.id).all()
        assert len(rows) == 3
        # Каждому — своё рабочее место и полный месяц отмеченных дней.
        assert len({r.position_id for r in rows}) == 3
        assert all(len(r.shifts) == 31 for r in rows)

    def test_duplicates_in_the_request_are_ignored(
        self, client, users, db_session, gbr_place, rodionov
    ):
        resp = client.post(
            "/api/vahta/assignments",
            json={
                "year": YEAR, "month": MONTH, "crew_id": gbr_place.id,
                "employee_ids": [rodionov.id, rodionov.id],
            },
            headers=_auth(client, "manager"),
        )
        assert resp.json()["created"] == 1

    def test_empty_list_is_rejected(self, client, users, gbr_place):
        resp = client.post(
            "/api/vahta/assignments",
            json={
                "year": YEAR, "month": MONTH, "crew_id": gbr_place.id,
                "employee_ids": [],
            },
            headers=_auth(client, "manager"),
        )
        assert resp.status_code == 422

    def test_single_person_still_works(self, client, users, gbr_place, rodionov):
        """Старая форма запроса не сломана — окно замены шлёт именно её."""
        resp = client.post(
            "/api/vahta/assignments",
            json={
                "year": YEAR, "month": MONTH, "crew_id": gbr_place.id,
                "employee_id": rodionov.id,
            },
            headers=_auth(client, "manager"),
        )
        assert resp.status_code == 201
        assert resp.json()["created"] == 1

    def test_empty_slot_still_works(self, client, users, gbr_place):
        resp = client.post(
            "/api/vahta/assignments",
            json={"year": YEAR, "month": MONTH, "crew_id": gbr_place.id},
            headers=_auth(client, "manager"),
        )
        assert resp.status_code == 201


class TestCandidates:
    """Список кандидатов — строка на ЧЕЛОВЕКА, а не на рабочее место."""

    def test_moonlighter_appears_once(
        self, client, users, db_session, guard_dept, zone, crew, companies, rodionov
    ):
        """У совместителя два рабочих места, но в списке он один — иначе в
        выпадашке два одинаковых имени и выбрать из них нельзя."""
        extra = EmployeePosition(
            employee_id=rodionov.id, title="ГБР", department_id=guard_dept.id,
            pay_type="per_shift", shift_rate=Decimal("3500"),
        )
        db_session.add(extra)
        db_session.commit()

        resp = client.get(
            f"/api/vahta/{YEAR}/{MONTH}/candidates", headers=_auth(client, "manager")
        )
        assert resp.status_code == 200
        names = [c["full_name"] for c in resp.json()]
        assert names.count("Караулов Олег Петрович") == 1

    def test_where_lists_all_current_posts(
        self, client, users, db_session, guard_dept, zone, crew, companies, rodionov
    ):
        site = _make_site(
            db_session, zone, "Объект A", "3000", {companies["SEC"].id: "100"},
        )
        first = _make_post(db_session, site, "Пост A")
        second = _make_post(db_session, site, "Пост B")
        extra = EmployeePosition(
            employee_id=rodionov.id, title="Охранник", department_id=guard_dept.id,
            pay_type="per_shift", shift_rate=Decimal("3000"),
        )
        db_session.add(extra)
        db_session.commit()
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=first,
            position=rodionov.primary_position, days={1},
        )
        create_assignment(
            db_session, year=YEAR, month=MONTH, place=second,
            position=extra, days={2},
        )
        db_session.commit()

        resp = client.get(
            f"/api/vahta/{YEAR}/{MONTH}/candidates", headers=_auth(client, "manager")
        )
        row = next(c for c in resp.json() if c["employee_id"] == rodionov.id)
        # Пометка собирает ВСЕ места человека: «Объект · Пост» через точку.
        assert "Пост A" in (row["where"] or "")
        assert "Пост B" in (row["where"] or "")


class TestQuickHire:
    def test_creates_employee_with_position_in_guard_department(
        self, db_session, gbr_place, guard_dept
    ):
        employee, position = quick_hire(
            db_session, full_name="Новиков Пётр", place=gbr_place, rate=Decimal("4200")
        )
        db_session.commit()
        assert employee.tab_number
        assert position.department_id == guard_dept.id
        assert position.shift_rate == Decimal("4200")
        # В общем справочнике он обычный сотрудник (п.1а).
        assert db_session.query(Employee).filter_by(id=employee.id).first() is not None

    def test_tab_number_continues_common_numbering(self, db_session, gbr_place):
        db_session.add(Employee(full_name="Кто-то", tab_number="T-0200", is_active=True))
        db_session.commit()
        assert next_tab_number(db_session) == "T-0201"
        employee, _ = quick_hire(db_session, full_name="Ещё Один", place=gbr_place)
        db_session.commit()
        assert employee.tab_number == "T-0201"

    def test_rate_defaults_to_the_place(self, db_session, gbr_place):
        _, position = quick_hire(db_session, full_name="Без Ставки", place=gbr_place)
        db_session.commit()
        assert position.shift_rate == Decimal("5000.00")


# ── Права ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def users(db_session, guard_dept, other_dept) -> dict[str, Employee]:
    made = {}
    for role, name in [
        ("admin", "Админ"), ("accountant", "Бухгалтер"),
        ("manager", "Менеджер охраны"), ("timekeeper", "Табельщик охраны"),
        ("employee", "Сотрудник"),
    ]:
        emp = Employee(
            full_name=name, email=f"{role}@example.com",
            hashed_password=hash_password("Test1234!"), role=role, is_active=True,
        )
        db_session.add(emp)
        made[role] = emp
    db_session.commit()
    for role in ("manager", "timekeeper"):
        made[role].managed_departments.append(guard_dept)
    db_session.commit()
    return made


def _auth(client: TestClient, role: str) -> dict[str, str]:
    token = get_token(client, f"{role}@example.com", "Test1234!")
    return {"Authorization": f"Bearer {token}"}


class TestAccess:
    def test_employee_has_no_access(self, client, users):
        resp = client.get(f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "employee"))
        assert resp.status_code == 403

    def test_anonymous_is_rejected(self, client, users):
        assert client.get(f"/api/vahta/{YEAR}/{MONTH}").status_code == 401

    def test_manager_sees_own_guard_department(self, client, users, gbr_place):
        resp = client.get(f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "manager"))
        assert resp.status_code == 200
        assert resp.json()["can_see_money"] is True

    def test_accountant_sees_but_cannot_change_settings(self, client, users, site):
        headers = _auth(client, "accountant")
        assert client.get("/api/vahta/zones", headers=headers).status_code == 200
        assert client.get("/api/vahta/sites", headers=headers).status_code == 200
        resp = client.post(
            "/api/vahta/posts",
            json={"name": "X", "site_id": site.id},
            headers=headers,
        )
        assert resp.status_code == 403

    def test_timekeeper_cannot_change_settings(self, client, users, zone):
        resp = client.post(
            "/api/vahta/crews",
            json={"name": "Экипаж", "zone_id": zone.id},
            headers=_auth(client, "timekeeper"),
        )
        assert resp.status_code == 403


class TestTimekeeperSeesNoMoney:
    """Табельщик не получает денежных полей даже прямым запросом (п.11)."""

    @pytest.fixture
    def filled(self, db_session, gbr_place, rodionov):
        assignment = create_assignment(
            db_session, year=YEAR, month=MONTH, place=gbr_place,
            position=rodionov.primary_position, days=FIRST_HALF,
        )
        assignment.premium_h1 = Decimal("230")
        db_session.commit()
        return assignment

    def test_month_response_has_no_amounts(self, client, users, filled):
        resp = client.get(
            f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "timekeeper")
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["can_see_money"] is False
        assert data["total_accrued"] is None
        row = data["zones"][0]["cards"][0]["rows"][0]
        assert row["salary"] is None
        assert row["rate"] is None
        assert row["premium"] is None
        assert row["accrued"] is None
        assert row["net_payout"] is None
        assert row["distribution"] is None
        # Смены при этом на месте: их он и ведёт.
        assert row["shifts"] == 15
        assert len(row["days"]) == 15

    def test_raw_response_contains_no_amount_strings(self, client, users, filled):
        resp = client.get(
            f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "timekeeper")
        )
        assert "75000" not in resp.text
        assert "5000" not in resp.text

    def test_manager_does_see_the_same_amounts(self, client, users, filled):
        resp = client.get(
            f"/api/vahta/{YEAR}/{MONTH}", headers=_auth(client, "manager")
        )
        row = resp.json()["zones"][0]["cards"][0]["rows"][0]
        assert Decimal(row["salary"]) == Decimal("75000.00")
        assert Decimal(row["accrued"]) == Decimal("75230.00")

    def test_sites_reference_hides_rates_and_shares(self, client, users, site, guard_post):
        """Справочник объектов табельщику — без ставок и без процентов."""
        resp = client.get("/api/vahta/sites", headers=_auth(client, "timekeeper"))
        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["shift_rate"] is None
        assert item["shares"] == []
        assert all(p["effective_rate"] is None for p in item["posts"])

    def test_crews_reference_hides_rate_and_shares(self, client, users, gbr_place):
        resp = client.get("/api/vahta/crews", headers=_auth(client, "timekeeper"))
        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["shift_rate"] is None
        assert item["shares"] == []

    def test_money_endpoints_are_forbidden(self, client, users, filled):
        headers = _auth(client, "timekeeper")
        assert client.patch(
            f"/api/vahta/assignments/{filled.id}",
            json={"premium_h1": "500"}, headers=headers,
        ).status_code == 403
        assert client.get(
            f"/api/vahta/{YEAR}/{MONTH}/export/excel", headers=headers
        ).status_code == 403

    def test_timekeeper_can_mark_shifts(self, client, users, filled):
        resp = client.put(
            "/api/vahta/day",
            json={"assignment_id": filled.id, "day": 20, "value": True},
            headers=_auth(client, "timekeeper"),
        )
        assert resp.status_code == 200
