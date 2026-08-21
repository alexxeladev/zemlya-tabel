"""Генератор демо-данных «как в жизни» для дев-окружения (task_demo_data).

Отличие от `seed-test-data`: тот заводит 14 сотрудников с ГРАНИЧНЫМИ случаями и
пустой табель — он для функциональных проверок. Здесь наоборот: реалистичный
объём (200 сотрудников, 6 юрлиц, ~20 отделов) и заполненный табель за несколько
месяцев — чтобы крутить систему живьём и мерить производительность.

Принципы:
  * рабочие дни и длительность смены берутся ТОЛЬКО из `services.work_schedule`
    (`planned_work_dates` / `shift_hours_for_date`) — формулы графиков здесь не
    дублируются, иначе демо-табель разойдётся с расчётом ЗП;
  * данные детерминированы (`random.Random(SEED)`) — повторный прогон на чистой
    базе даёт те же цифры, их можно сверять между запусками;
  * часы и код отсутствия в одном дне взаимоисключающи (как в `upsert_cell`):
    отсутствия расставляются ПЕРВЫМИ, дни с ними пропускаются при заливке часов;
  * пишем bulk-ом: на 200 сотрудниках это ~30 тыс. ячеек, построчный ORM-insert
    занимал бы минуты.

Только dev. Перед запуском база очищается командой `reset-data`.
"""
from __future__ import annotations

import calendar as _cal
import datetime
import random
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.company_shares import (
    CompanyShareOverride,
    DepartmentCompanyShare,
    EmployeeCompanyShare,
)
from app.models.departments import Department
from app.models.employee_absences import EmployeeAbsence
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.work_schedule import planned_work_dates, shift_hours_for_date

SEED = 20260813
QA_PASSWORD = "Test1234!"

# ── Справочники ───────────────────────────────────────────────────────────────

COMPANIES = [
    ("zmo", 'ООО "Земля МО"'),
    ("kft", 'ООО "Комфорт"'),
    ("sec", 'ООО "Секьюрити"'),
    ("stroy", 'ООО "СтройАктив"'),
    ("park", 'ООО "Парковый"'),
    ("serv", 'ООО "СервисПлюс"'),
]

# Отделы по компаниям: 3–4 на каждую, всего 20. Головная компания — ярлык для
# дерева оргструктуры, на расчёт не влияет (сотрудник может работать на любые).
DEPARTMENTS = {
    "zmo": ["Управление", "ИТО", "Бухгалтерия", "Юридический"],
    "kft": ["Эксплуатация", "Клининг", "Диспетчерская"],
    "sec": ["Охрана объектов", "Пультовая охрана", "Видеонаблюдение", "КПП"],
    "stroy": ["Строительный контроль", "Проектный", "Снабжение"],
    "park": ["Благоустройство", "Озеленение", "Парковки"],
    "serv": ["Сервисная служба", "Склад", "Транспорт"],
}

# (name, hours_per_shift, type, work_weekdays, cycle_start, work, off)
SCHEDULES = [
    ("5/2", 8, "weekday", [0, 1, 2, 3, 4], None, None, None),
    ("6/1", 9, "weekday", [0, 1, 2, 3, 4, 5], None, None, None),
    ("Сб–Ср", 8, "weekday", [5, 6, 0, 1, 2], None, None, None),
    ("2/2 смена 1", 12, "cyclic", None, datetime.date(2025, 12, 31), 2, 2),
    ("2/2 смена 2", 12, "cyclic", None, datetime.date(2026, 1, 2), 2, 2),
    ("3/3 смена 1", 12, "cyclic", None, datetime.date(2026, 1, 1), 3, 3),
    ("3/3 смена 2", 12, "cyclic", None, datetime.date(2026, 1, 4), 3, 3),
]

FIRST_NAMES_M = ["Александр", "Дмитрий", "Сергей", "Андрей", "Алексей", "Иван",
                 "Михаил", "Николай", "Павел", "Роман", "Виктор", "Юрий"]
FIRST_NAMES_F = ["Елена", "Ольга", "Наталья", "Татьяна", "Ирина", "Светлана",
                 "Марина", "Анна", "Юлия", "Мария", "Галина", "Людмила"]
MID_M = ["Иванович", "Петрович", "Сергеевич", "Андреевич", "Николаевич", "Викторович"]
MID_F = ["Ивановна", "Петровна", "Сергеевна", "Андреевна", "Николаевна", "Викторовна"]
LAST = ["Иванов", "Петров", "Смирнов", "Кузнецов", "Попов", "Васильев", "Соколов",
        "Михайлов", "Новиков", "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев",
        "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев", "Орлов",
        "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв", "Борисов",
        "Яковлев", "Григорьев", "Романов", "Воробьёв", "Сергеев", "Кузьмин", "Фролов"]

TITLES_BY_KIND = {
    "salary": ["Инженер", "Специалист", "Ведущий специалист", "Менеджер", "Бухгалтер",
               "Юрист", "Диспетчер", "Мастер участка", "Начальник участка"],
    "per_shift": ["Охранник", "Сторож", "Оператор пульта", "Контролёр КПП", "Дежурный"],
    "hourly": ["Уборщик", "Дворник", "Электрик", "Сантехник", "Озеленитель", "Грузчик"],
}


def _get_or_create(db: Session, model, match: dict, defaults: dict):
    row = db.query(model).filter_by(**match).first()
    if row:
        return row
    row = model(**match, **defaults)
    db.add(row)
    db.flush()
    return row


def _month_range(start: datetime.date, end: datetime.date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _clamp_to_month(day_from: datetime.date, day_to: datetime.date, year: int, month: int):
    last = _cal.monthrange(year, month)[1]
    return (
        max(day_from, datetime.date(year, month, 1)),
        min(day_to, datetime.date(year, month, last)),
    )


# ── Справочники и учётки ──────────────────────────────────────────────────────

def _seed_reference_data(db: Session) -> tuple[list[Company], list[Department], dict]:
    companies = [
        _get_or_create(db, Company, {"code": code}, {"name": name, "is_active": True})
        for code, name in COMPANIES
    ]
    by_code = {c.code: c for c in companies}

    departments: list[Department] = []
    for code, names in DEPARTMENTS.items():
        for name in names:
            dept_code = f"{code.upper()}-{len(departments):02d}"
            departments.append(_get_or_create(
                db, Department, {"code": dept_code},
                {"name": f"{name} ({by_code[code].code})", "is_active": True,
                 "head_company_id": by_code[code].id},
            ))

    schedules: dict[str, Schedule] = {}
    for name, hours, kind, weekdays, cycle_start, work, off in SCHEDULES:
        schedules[name] = _get_or_create(
            db, Schedule, {"name": name},
            {"hours_per_shift": hours, "schedule_type": kind, "work_weekdays": weekdays,
             "cycle_start_date": cycle_start, "cycle_work_days": work,
             "cycle_off_days": off, "is_active": True,
             "description": f"Демо-график {name}"},
        )
    db.flush()
    return companies, departments, schedules


def _seed_calendar(db: Session, year: int) -> None:
    """Производственный календарь года: без него у weekday-графиков норма
    считалась бы завышенной и расчёт был бы «не считается»."""
    from app.models.production_calendars import ProductionCalendar

    if db.query(ProductionCalendar).filter_by(year=year).first():
        return
    try:
        import asyncio

        from app.services.calendar import fetch_calendar_from_remote

        data = asyncio.run(fetch_calendar_from_remote(year))
        db.add(ProductionCalendar(year=year, data=data, source="remote"))
        db.flush()
    except Exception as exc:  # noqa: BLE001 — сеть опциональна
        print(f"  ! календарь {year} не загружен ({exc}); "
              f"норма weekday-графиков будет неверной, залейте вручную")


def _seed_qa_accounts(
    db: Session, departments: list[Department], companies: list[Company],
    schedules: dict, hire: datetime.date,
) -> dict[str, Employee]:
    """Учётки для входа. Руководитель и табельщик сидят на ОДНИХ отделах —
    так видно разницу в правах (табельщик не видит денег и не сдаёт период)."""
    accounts = [
        ("admin", "QA Админ", "qa.admin@example.com", "admin"),
        ("accountant", "QA Бухгалтер", "qa.accountant@example.com", "accountant"),
        ("manager", "QA Руководитель", "qa.manager@example.com", "manager"),
        ("manager2", "QA Руководитель 2", "qa.manager2@example.com", "manager"),
        ("timekeeper", "QA Табельщик", "qa.timekeeper@example.com", "timekeeper"),
        ("employee", "QA Сотрудник", "qa.employee@example.com", "employee"),
    ]
    created: dict[str, Employee] = {}
    for idx, (key, name, email, role) in enumerate(accounts):
        emp = db.query(Employee).filter_by(email=email).first()
        if emp is None:
            emp = Employee(
                full_name=name,
                # Полный key, а не первые 3 буквы: manager и manager2 дали бы
                # одинаковый номер и упёрлись в unique-констрейнт
                tab_number=f"QA-{key.upper()}",
                position=name,
                email=email,
                hashed_password=hash_password(QA_PASSWORD),
                role=role,
                is_active=True,
                must_change_password=False,
                hire_date=hire,
                department_id=departments[idx % len(departments)].id,
                default_company_id=companies[idx % len(companies)].id,
                schedule_id=schedules["5/2"].id,
                pay_type="salary",
                rate=Decimal("90000"),
                weekend_pay_type="coefficient",
                weekend_coefficient=Decimal("1.5"),
                holiday_pay_type="coefficient",
                holiday_coefficient=Decimal("2"),
                overtime_coefficient=Decimal("1.5"),
            )
            db.add(emp)
            db.flush()
        created[key] = emp

    # Мульти-отдел у первого руководителя, у второго — один; табельщик ведёт те же
    # два отдела, что и первый руководитель.
    created["manager"].managed_departments = departments[0:2]
    created["manager2"].managed_departments = departments[2:3]
    created["timekeeper"].managed_departments = departments[0:2]
    db.flush()
    return created


# ── Сотрудники ────────────────────────────────────────────────────────────────

def _make_employee(
    rnd: random.Random, index: int, dept: Department, companies: list[Company],
    schedules: dict, period_start: datetime.date,
) -> Employee:
    female = rnd.random() < 0.45
    last = rnd.choice(LAST) + ("а" if female else "")
    first = rnd.choice(FIRST_NAMES_F if female else FIRST_NAMES_M)
    mid = rnd.choice(MID_F if female else MID_M)

    kind = rnd.choices(["salary", "per_shift", "hourly"], weights=[70, 15, 15])[0]
    if kind == "per_shift":
        schedule = schedules[rnd.choice(
            ["2/2 смена 1", "2/2 смена 2", "3/3 смена 1", "3/3 смена 2"])]
    elif kind == "hourly":
        schedule = schedules[rnd.choice(["5/2", "6/1", "Сб–Ср"])]
    else:
        schedule = schedules[rnd.choices(["5/2", "6/1"], weights=[85, 15])[0]]

    # Основное юрлицо: обычно головная компания отдела, иногда чужое —
    # мультикомпанийность должна быть видна в ведомости.
    head = next((c for c in companies if c.id == dept.head_company_id), companies[0])
    company = head if rnd.random() < 0.85 else rnd.choice(companies)

    # Приём: большинство до начала периода, часть — внутри (неполные месяцы)
    if rnd.random() < 0.9:
        hire = period_start - datetime.timedelta(days=rnd.randint(200, 2000))
    else:
        hire = period_start + datetime.timedelta(days=rnd.randint(20, 170))

    emp = Employee(
        full_name=f"{last} {first} {mid}",
        tab_number=f"T-{index:04d}",
        position=rnd.choice(TITLES_BY_KIND[kind]),
        hire_date=hire,
        is_active=True,
        department_id=dept.id,
        default_company_id=company.id,
        schedule_id=schedule.id,
        pay_type=kind,
        rate=Decimal(str(rnd.randrange(45, 145) * 1000)) if kind == "salary" else None,
        shift_rate=Decimal(str(rnd.randrange(3200, 5600, 100))) if kind == "per_shift" else None,
        hour_rate=Decimal(str(rnd.randrange(380, 660, 10))) if kind == "hourly" else None,
        overtime_coefficient=Decimal(rnd.choice(["1.5", "1.5", "1.5", "1", "2"])),
    )
    # Выходные/праздничные: у большинства коэффициент, у части — фикс-ставка за час
    if rnd.random() < 0.12:
        emp.weekend_pay_type = "fixed_rate"
        emp.weekend_fixed_rate = Decimal(str(rnd.randrange(600, 900, 20)))
    else:
        emp.weekend_pay_type = "coefficient"
        emp.weekend_coefficient = Decimal(rnd.choice(["1.5", "1.5", "2"]))
    emp.holiday_pay_type = "coefficient"
    emp.holiday_coefficient = Decimal(rnd.choice(["2", "2", "1.5"]))

    # Ночные смены: только флаг. Ставка вычисляется из фонда отдела
    # (task_night_shifts_rework), задавать её на позиции больше нельзя.
    primary = emp.primary_position
    primary.has_night_shifts = kind == "per_shift"

    # Займ у части сотрудников — чтобы «Удержано» и «К выплате» были не пустыми
    if rnd.random() < 0.08:
        emp.loan_amount = Decimal(str(rnd.randrange(30, 240) * 1000))
        emp.loan_term_months = rnd.choice([6, 10, 12, 18])
        emp.loan_start_date = datetime.date(period_start.year, rnd.randint(1, 4), 1)
    return emp


def _add_second_position(
    rnd: random.Random, emp: Employee, departments: list[Department],
    companies: list[Company], schedules: dict,
) -> None:
    """Совместительство: второе рабочее место в другом отделе и юрлице."""
    dept = rnd.choice(departments)
    company = rnd.choice(companies)
    kind = rnd.choice(["salary", "hourly"])
    emp.positions.append(EmployeePosition(
        title=rnd.choice(TITLES_BY_KIND[kind]),
        department_id=dept.id,
        company_id=company.id,
        schedule_id=schedules[rnd.choice(["5/2", "Сб–Ср"])].id,
        pay_type=kind,
        rate=Decimal(str(rnd.randrange(18, 40) * 1000)) if kind == "salary" else None,
        hour_rate=Decimal(str(rnd.randrange(350, 550, 10))) if kind == "hourly" else None,
        weekend_pay_type="coefficient",
        weekend_coefficient=Decimal("1.5"),
        holiday_pay_type="coefficient",
        holiday_coefficient=Decimal("2"),
        overtime_coefficient=Decimal("1.5"),
        is_primary=False,
        sort_order=1,
    ))


# ── Отсутствия ────────────────────────────────────────────────────────────────

def _seed_absences(
    rnd: random.Random, db: Session, employees: list[Employee],
    start: datetime.date, end: datetime.date, actor_id: int | None,
) -> dict[int, set[datetime.date]]:
    """ОТ/Б/ДО/Н. Возвращает занятые дни, чтобы часы в них не писались."""
    taken: dict[int, set[datetime.date]] = {}
    rows: list[EmployeeAbsence] = []
    span = (end - start).days

    def block(emp: Employee, kind: str, length: int) -> None:
        used = taken.setdefault(emp.id, set())
        begin = start + datetime.timedelta(days=rnd.randint(0, max(0, span - length)))
        for i in range(length):
            day = begin + datetime.timedelta(days=i)
            if day > end or day in used:
                continue
            if emp.hire_date and day < emp.hire_date:
                continue
            used.add(day)
            rows.append(EmployeeAbsence(
                employee_id=emp.id, work_date=day, kind=kind, created_by_id=actor_id))

    for emp in employees:
        roll = rnd.random()
        if roll < 0.55:                      # отпуск 7 или 14 дней
            block(emp, "vacation", rnd.choice([7, 14]))
        if roll > 0.75:                      # больничный 3–6 дней
            block(emp, "sick", rnd.randint(3, 6))
        if rnd.random() < 0.12:              # длинный больничный — сверх лимита 10 дн.
            block(emp, "sick", rnd.randint(12, 20))
        if rnd.random() < 0.08:
            block(emp, "unpaid", rnd.randint(1, 3))
        if rnd.random() < 0.05:
            block(emp, "absent", 1)

    db.bulk_save_objects(rows)
    return taken


# ── Часы ──────────────────────────────────────────────────────────────────────

def _seed_entries(
    rnd: random.Random, db: Session, employees: list[Employee],
    calendar_data: dict | None, start: datetime.date, end: datetime.date,
    absences: dict[int, set[datetime.date]],
) -> int:
    """Часы по графику КАЖДОЙ позиции + разнообразие: переработки, выходы вне
    графика, дни на два юрлица, пропуски.

    Плановые дни и длительность смены — только из `work_schedule`, иначе
    демо-табель разойдётся с расчётом ЗП и нормой.
    """
    rows: list[dict] = []
    for emp in employees:
        emp_absences = absences.get(emp.id, set())
        for position in emp.positions:
            schedule = position.schedule
            if schedule is None:
                continue
            company_id = position.company_id or emp.default_company_id
            if company_id is None:
                continue
            # Второе юрлицо для дней «на двоих»
            other_company_id = emp.default_company_id if position.company_id else None

            for year, month in _month_range(start, end):
                lo, hi = _clamp_to_month(start, end, year, month)
                if emp.hire_date and hi < emp.hire_date:
                    continue
                if emp.dismissal_date and lo > emp.dismissal_date:
                    continue
                planned = planned_work_dates(schedule, year, month, calendar_data)
                for day in sorted(planned):
                    if not (lo <= day <= hi):
                        continue
                    if emp.hire_date and day < emp.hire_date:
                        continue
                    if emp.dismissal_date and day > emp.dismissal_date:
                        continue
                    if day in emp_absences:
                        continue
                    if rnd.random() < 0.02:      # прогул/забыли отметить
                        continue
                    hours = int(shift_hours_for_date(schedule, day, calendar_data) or 8)
                    if rnd.random() < 0.07:      # переработка
                        hours += rnd.choice([1, 2, 2, 3, 4])
                    if other_company_id and other_company_id != company_id and rnd.random() < 0.06:
                        half = max(1, hours // 2)
                        rows.append({"employee_id": emp.id, "position_id": position.id,
                                     "work_date": day, "company_id": company_id,
                                     "hours": half})
                        rows.append({"employee_id": emp.id, "position_id": position.id,
                                     "work_date": day, "company_id": other_company_id,
                                     "hours": hours - half})
                    else:
                        rows.append({"employee_id": emp.id, "position_id": position.id,
                                     "work_date": day, "company_id": company_id,
                                     "hours": min(24, hours)})

                # Выходы ВНЕ графика (свой выходной / праздник) — категории
                # «вне графика» и «праздничные» должны быть не пустыми
                if rnd.random() < 0.25:
                    last_day = _cal.monthrange(year, month)[1]
                    for _ in range(rnd.randint(1, 2)):
                        day = datetime.date(year, month, rnd.randint(1, last_day))
                        if not (lo <= day <= hi) or day in planned or day in emp_absences:
                            continue
                        if emp.hire_date and day < emp.hire_date:
                            continue
                        rows.append({"employee_id": emp.id, "position_id": position.id,
                                     "work_date": day, "company_id": company_id,
                                     "hours": rnd.choice([4, 6, 8])})

    # Дубли (employee, position, day, company) невозможны по построению, кроме
    # случайных «вне графика» — их отсеиваем, иначе упадёт unique constraint.
    seen: set[tuple] = set()
    unique_rows = []
    for row in rows:
        key = (row["employee_id"], row["position_id"], row["work_date"], row["company_id"])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    if unique_rows:
        db.bulk_insert_mappings(TimesheetEntry, unique_rows)
    return len(unique_rows)


# ── Премии, распределение, периоды ────────────────────────────────────────────

def _seed_adjustments(
    rnd: random.Random, db: Session, employees: list[Employee],
    months: list[tuple[int, int]], actor_id: int | None,
) -> int:
    rows = []
    for emp in employees:
        for year, month in months:
            if rnd.random() < 0.18:
                rows.append(EmployeeAdjustment(
                    employee_id=emp.id, position_id=emp.primary_position.id,
                    year=year, month=month, kind="premium",
                    amount=Decimal(str(rnd.randrange(3, 40) * 1000)),
                    reason="Премия по итогам месяца", created_by_id=actor_id))
            if rnd.random() < 0.10:
                rows.append(EmployeeAdjustment(
                    employee_id=emp.id, position_id=emp.primary_position.id,
                    year=year, month=month, kind="kpi",
                    amount=Decimal(str(rnd.randrange(2, 25) * 1000)),
                    reason="KPI за выполнение плана", created_by_id=actor_id))
            if rnd.random() < 0.07:
                rows.append(EmployeeAdjustment(
                    employee_id=emp.id, position_id=emp.primary_position.id,
                    year=year, month=month, kind="advance",
                    amount=Decimal(str(rnd.randrange(5, 30) * 1000)),
                    reason="Аванс по заявлению", created_by_id=actor_id))
    db.bulk_save_objects(rows)
    return len(rows)


def _seed_shares(
    rnd: random.Random, db: Session, employees: list[Employee],
    departments: list[Department], companies: list[Company],
    months: list[tuple[int, int]], actor_id: int | None,
) -> dict[str, int]:
    """Все уровни каскада распределения: отдел → карточка → месячный override.
    Остальные сотрудники остаются на авто-распределении по часам."""
    counts = {"department": 0, "employee": 0, "override": 0}

    for dept in rnd.sample(departments, k=max(1, len(departments) // 4)):
        pair = rnd.sample(companies, k=2)
        for company, percent in zip(pair, [Decimal("70"), Decimal("30")]):
            db.add(DepartmentCompanyShare(
                department_id=dept.id, company_id=company.id, percent=percent))
            counts["department"] += 1

    for emp in rnd.sample(employees, k=max(1, len(employees) // 10)):
        pair = rnd.sample(companies, k=2)
        for company, percent in zip(pair, [Decimal("60"), Decimal("40")]):
            db.add(EmployeeCompanyShare(
                employee_id=emp.id, position_id=emp.primary_position.id,
                company_id=company.id, percent=percent))
            counts["employee"] += 1

    year, month = months[-1]
    for emp in rnd.sample(employees, k=max(1, len(employees) // 25)):
        pair = rnd.sample(companies, k=2)
        for company, percent in zip(pair, [Decimal("50"), Decimal("50")]):
            db.add(CompanyShareOverride(
                employee_id=emp.id, position_id=emp.primary_position.id,
                company_id=company.id, year=year, month=month,
                percent=percent, created_by_id=actor_id))
            counts["override"] += 1
    return counts


def _seed_periods(
    rnd: random.Random, db: Session, departments: list[Department],
    months: list[tuple[int, int]], qa: dict[str, Employee],
) -> dict[str, int]:
    """Статусы вперемешку: прошлые месяцы закрыты, предпоследний частично на
    проверке, текущий в draft. Так проверяется весь workflow и «Задачи»."""
    counts = {"draft": 0, "pending_review": 0, "closed": 0}
    last = months[-1]
    prev = months[-2] if len(months) > 1 else None
    now = datetime.datetime.utcnow()

    for dept in departments:
        for year, month in months:
            if (year, month) == last:
                status = "draft"
            elif prev and (year, month) == prev:
                status = rnd.choice(["pending_review", "draft", "pending_review"])
            else:
                status = "closed"
            period = TimesheetPeriod(
                department_id=dept.id, year=year, month=month, status=status)
            if status in ("pending_review", "closed"):
                period.submitted_at = now
                period.submitted_by_id = qa["manager"].id
            if status == "closed":
                period.closed_at = now
                period.closed_by_id = qa["accountant"].id
            db.add(period)
            counts[status] += 1
    return counts


# ── Точка входа ───────────────────────────────────────────────────────────────

def generate_demo_data(
    db: Session,
    employees_count: int = 200,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> dict[str, int]:
    """Сгенерировать демо-базу. Возвращает статистику для печати."""
    today = datetime.date.today()
    start = start or datetime.date(today.year, 1, 1)
    end = end or today
    if end < start:
        raise ValueError("Конец периода раньше начала")

    rnd = random.Random(SEED)
    stats: dict[str, int] = {}

    companies, departments, schedules = _seed_reference_data(db)
    for year in sorted({start.year, end.year}):
        _seed_calendar(db, year)
    qa = _seed_qa_accounts(db, departments, companies, schedules, start)
    db.commit()

    from app.models.production_calendars import ProductionCalendar
    cal = db.query(ProductionCalendar).filter_by(year=end.year).first()
    calendar_data = cal.data if cal else None

    # ── Сотрудники ──
    employees: list[Employee] = []
    for i in range(employees_count):
        dept = departments[i % len(departments)]
        emp = _make_employee(rnd, i + 1, dept, companies, schedules, start)
        db.add(emp)
        employees.append(emp)
    db.flush()

    for emp in rnd.sample(employees, k=max(1, employees_count // 16)):
        _add_second_position(rnd, emp, departments, companies, schedules)
    # Уволенные внутри периода — должны быть видны в своих месяцах
    for emp in rnd.sample(employees, k=max(1, employees_count // 40)):
        emp.dismissal_date = start + datetime.timedelta(days=rnd.randint(60, 190))
        emp.is_active = False

    # Незаполненные карточки — в живой базе они есть всегда, и система обязана
    # показывать их как «не считается», а не молча считать неверно. Заодно это
    # оживляет KPI «не вошли в расчёт ФОТ» на дашборде.
    for emp in rnd.sample(employees, k=max(1, employees_count // 60)):
        emp.schedule_id = None          # нет графика → ни нормы, ни автозаполнения
    for emp in rnd.sample(employees, k=max(1, employees_count // 100)):
        if emp.pay_type == "salary":
            emp.rate = None             # нет оклада → расчёт невозможен
    db.flush()
    db.commit()

    all_employees = employees + [qa[k] for k in ("manager", "manager2", "timekeeper", "employee")]
    stats["employees"] = len(employees)
    stats["positions"] = sum(len(e.positions) for e in all_employees)

    absences = _seed_absences(rnd, db, all_employees, start, end, qa["admin"].id)
    stats["absences"] = sum(len(v) for v in absences.values())
    db.commit()

    stats["entries"] = _seed_entries(
        rnd, db, all_employees, calendar_data, start, end, absences)
    db.commit()

    months = _month_range(start, end)
    stats["adjustments"] = _seed_adjustments(rnd, db, all_employees, months, qa["accountant"].id)
    shares = _seed_shares(
        rnd, db, all_employees, departments, companies, months, qa["accountant"].id)
    stats.update({f"shares_{k}": v for k, v in shares.items()})
    period_counts = _seed_periods(rnd, db, departments, months, qa)
    stats.update({f"periods_{k}": v for k, v in period_counts.items()})
    db.commit()

    stats["companies"] = len(companies)
    stats["departments"] = len(departments)
    stats["schedules"] = len(schedules)
    stats["months"] = len(months)
    return stats
