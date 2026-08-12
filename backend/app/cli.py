"""CLI utilities for zemlya-tabel.

Usage:
    python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
    python -m app.cli reset-password --email admin@example.com --new-password newpass
    python -m app.cli reset-data [--yes]
    python -m app.cli seed-test-data
"""
import argparse
import sys


def create_admin(email: str, password: str, full_name: str) -> None:
    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.employees import Employee

    db = SessionLocal()
    try:
        existing = db.query(Employee).filter(Employee.is_system_admin.is_(True)).first()
        if existing:
            print(
                f"Error: System admin already exists (email: {existing.email}). "
                "Use reset-password instead.",
                file=sys.stderr,
            )
            sys.exit(1)

        if db.query(Employee).filter(Employee.email == email).first():
            print(f"Error: employee with email '{email}' already exists.", file=sys.stderr)
            sys.exit(1)

        emp = Employee(
            full_name=full_name,
            email=email,
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
            must_change_password=True,
            is_system_admin=True,
        )
        db.add(emp)
        db.commit()
        print(f"System admin '{email}' created. must_change_password=True")
    finally:
        db.close()


def reset_password(email: str, new_password: str) -> None:
    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.employees import Employee

    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.email == email).first()
        if not emp:
            print(f"Error: no employee with email '{email}' found.", file=sys.stderr)
            sys.exit(1)

        emp.hashed_password = hash_password(new_password)
        emp.must_change_password = True
        db.commit()
        print(f"Password reset for '{email}'. must_change_password=True")
    finally:
        db.close()


def reset_data(assume_yes: bool = False) -> None:
    """Полностью очистить данные dev-БД, кроме системного админа (is_system_admin=True).

    Удаляет в порядке с учётом foreign keys: сначала зависимые записи
    (audit log, табель, премии, удержания, проценты распределения), затем
    сотрудников (кроме системного админа) и справочники. Идемпотентна.
    """
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models.audit_log import AuditLog
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
    from app.models.loan_deductions import LoanDeduction
    from app.models.positions import EmployeePosition
    from app.models.production_calendars import ProductionCalendar
    from app.models.schedules import Schedule
    from app.models.timesheet_entries import TimesheetEntry
    from app.models.timesheet_periods import TimesheetPeriod

    if not assume_yes:
        ans = input(
            "ВНИМАНИЕ: удалит ВСЕ данные (кроме системного админа). Продолжить? [y/N] "
        ).strip().lower()
        if ans not in ("y", "yes"):
            print("Отменено.")
            return

    db = SessionLocal()
    stats: dict[str, int] = {}
    try:
        # id системных админов — их не трогаем
        keep_ids = set(
            db.execute(
                select(Employee.id).where(Employee.is_system_admin.is_(True))
            ).scalars()
        )

        # Связь «менеджер ↔ управляемые отделы» — обычная таблица без модели.
        from app.models.department_managers import department_managers
        stats["department_managers"] = db.execute(
            department_managers.delete()
        ).rowcount

        # Зависимые от employees / справочников — удаляем первыми.
        for model in (
            AuditLog,
            TimesheetEntry,
            TimesheetPeriod,
            EmployeeAbsence,
            EmployeeAdjustment,
            LoanDeduction,
            CompanyShareOverride,
            EmployeeCompanyShare,
            DepartmentCompanyShare,
        ):
            stats[model.__tablename__] = db.query(model).delete(synchronize_session=False)

        # Позиции удаляемых сотрудников — раньше самих сотрудников: bulk delete
        # идёт мимо ORM-каскада, и FK employee_positions → employees не пустил бы.
        # Ссылку на позицию с самого сотрудника (займ) тоже надо снять.
        emp_filter = Employee.id.notin_(keep_ids) if keep_ids else None
        pos_q = db.query(EmployeePosition)
        upd_q = db.query(Employee)
        if emp_filter is not None:
            pos_q = pos_q.filter(EmployeePosition.employee_id.notin_(keep_ids))
            upd_q = upd_q.filter(emp_filter)
        upd_q.update({Employee.loan_position_id: None}, synchronize_session=False)
        stats[EmployeePosition.__tablename__] = pos_q.delete(synchronize_session=False)

        # Сотрудники, кроме системных админов.
        emp_q = db.query(Employee)
        if emp_filter is not None:
            emp_q = emp_q.filter(emp_filter)
        stats[Employee.__tablename__] = emp_q.delete(synchronize_session=False)

        # Справочники (на системного админа FK не ссылается — поля null).
        for model in (Department, Company, Schedule, ProductionCalendar):
            stats[model.__tablename__] = db.query(model).delete(synchronize_session=False)

        db.commit()
    finally:
        db.close()

    print("Удалено строк по таблицам:")
    for table, n in stats.items():
        print(f"  {table:28} {n}")
    print(f"Системных админов сохранено: {len(keep_ids)}")


def seed_test_data() -> None:
    """Наполнить БД тестовыми данными для ручной проверки (граничные случаи).

    Идемпотентна: справочники и сотрудники ищутся по натуральным ключам
    (код / имя / email) и не дублируются. Табель часами НЕ заполняется.
    """
    import datetime
    from decimal import Decimal

    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.companies import Company
    from app.models.departments import Department
    from app.models.employees import Employee
    from app.models.positions import EmployeePosition
    from app.models.schedules import Schedule

    today = datetime.date.today()
    db = SessionLocal()
    created: dict[str, int] = {
        "companies": 0, "departments": 0, "schedules": 0,
        "employees": 0, "calendar": 0,
    }

    def get_or_create(model, lookup: dict, defaults: dict, counter: str):
        obj = db.query(model).filter_by(**lookup).first()
        if obj:
            return obj
        obj = model(**lookup, **defaults)
        db.add(obj)
        db.flush()
        created[counter] += 1
        return obj

    try:
        # --- Компании (3 юрлица) ---
        zmo = get_or_create(Company, {"code": "zmo"},
                            {"name": "ЗемляМО", "is_active": True}, "companies")
        kft = get_or_create(Company, {"code": "kft"},
                            {"name": "Комфорт", "is_active": True}, "companies")
        get_or_create(Company, {"code": "sec"},
                            {"name": "Секьюрити", "is_active": True}, "companies")

        # --- Отделы ---
        # head_company_id — головная компания: группировка в дереве оргструктуры,
        # на расчёт ЗП не влияет (сотрудники по-прежнему работают на любые юрлица).
        ito = get_or_create(Department, {"code": "ITO"},
                            {"name": "ИТО", "is_active": True,
                             "head_company_id": zmo.id}, "departments")
        buh = get_or_create(Department, {"code": "BUH"},
                            {"name": "Бухгалтерия", "is_active": True,
                             "head_company_id": zmo.id}, "departments")
        sec_dept = get_or_create(Department, {"code": "SEC"},
                            {"name": "Охрана", "is_active": True,
                             "head_company_id": kft.id}, "departments")

        # --- Графики ---
        sch52 = get_or_create(Schedule, {"name": "5/2"},
                            {"hours_per_shift": 8, "schedule_type": "weekday",
                             "work_weekdays": [0, 1, 2, 3, 4],
                             "description": "Пятидневка по производственному календарю",
                             "is_active": True}, "schedules")
        get_or_create(Schedule, {"name": "6/1"},
                            {"hours_per_shift": 9, "schedule_type": "weekday",
                             "work_weekdays": [0, 1, 2, 3, 4, 5],
                             "description": "Шестидневка Пн–Сб", "is_active": True}, "schedules")
        # Сменные графики: смена 1 и смена 2 — один цикл, разные анкеры (противофаза).
        # Анкеры подобраны под фазы из 1С на июнь 2026.
        sch22 = get_or_create(Schedule, {"name": "2/2 смена 1"},
                            {"hours_per_shift": 12, "schedule_type": "cyclic",
                             "cycle_start_date": datetime.date(2026, 5, 31),
                             "cycle_work_days": 2, "cycle_off_days": 2,
                             "description": "Сутки через двое, смена 1",
                             "is_active": True}, "schedules")
        get_or_create(Schedule, {"name": "2/2 смена 2"},
                            {"hours_per_shift": 12, "schedule_type": "cyclic",
                             "cycle_start_date": datetime.date(2026, 6, 2),
                             "cycle_work_days": 2, "cycle_off_days": 2,
                             "description": "Сутки через двое, смена 2",
                             "is_active": True}, "schedules")
        get_or_create(Schedule, {"name": "3/3 смена 1"},
                            {"hours_per_shift": 12, "schedule_type": "cyclic",
                             "cycle_start_date": datetime.date(2026, 6, 4),
                             "cycle_work_days": 3, "cycle_off_days": 3,
                             "description": "Три через три, смена 1",
                             "is_active": True}, "schedules")
        get_or_create(Schedule, {"name": "3/3 смена 2"},
                            {"hours_per_shift": 12, "schedule_type": "cyclic",
                             "cycle_start_date": datetime.date(2026, 6, 7),
                             "cycle_work_days": 3, "cycle_off_days": 3,
                             "description": "Три через три, смена 2",
                             "is_active": True}, "schedules")

        # --- Производственный календарь на текущий год (если доступен remote) ---
        from app.models.production_calendars import ProductionCalendar
        if not db.query(ProductionCalendar).filter_by(year=today.year).first():
            try:
                import asyncio

                from app.services.calendar import fetch_calendar_from_remote
                data = asyncio.run(fetch_calendar_from_remote(today.year))
                db.add(ProductionCalendar(year=today.year, data=data, source="remote"))
                db.flush()
                created["calendar"] = 1
            except Exception as exc:  # noqa: BLE001 — сеть опциональна
                print(f"  календарь: пропущен (загрузится позже): {exc}")

        coef = "coefficient"
        fixed = "fixed_rate"

        # (full_name, tab, dept, schedule, default_company, rate,
        #  weekend_type, weekend_coef, weekend_fixed, loan(amount,term,start),
        #  email, role)
        D = Decimal
        rows = [
            ("QA Админ", "QA-ADM", None, None, None, None,
             coef, D("1.5"), None, None,
             "qa.admin@example.com", "admin"),
            ("QA Бухгалтер", "QA-BUH", buh, sch52, zmo, D("80000"),
             coef, D("1.5"), None, None,
             "qa.accountant@example.com", "accountant"),
            ("QA Менеджер ИТО", "QA-MGR", ito, sch52, zmo, D("90000"),
             coef, D("1.5"), None, None,
             "qa.manager@example.com", "manager"),
            ("QA Менеджер Охраны", "QA-MGR2", sec_dept, sch52, kft, D("85000"),
             coef, D("1.5"), None, None,
             "qa.manager2@example.com", "manager"),
            # Табельщик ИТО (task_timekeeper_role): ведёт время отдела, финансов
            # не видит. Оклад у него свой есть — просто он его не увидит.
            ("QA Табельщик ИТО", "QA-TK", ito, sch52, zmo, D("55000"),
             coef, D("1.5"), None, None,
             "qa.timekeeper@example.com", "timekeeper"),
            ("QA Сотрудник", "QA-EMP", ito, sch52, zmo, D("60000"),
             coef, D("1.5"), None, None,
             "qa.employee@example.com", "employee"),
            ("Электрик Фиксов", "T-005", ito, sch52, kft, D("50000"),
             fixed, None, D("740"), None,
             None, None),
            ("Безкоэф Нулевой", "T-006", ito, sch52, zmo, D("55000"),
             coef, D("0"), None, None,
             None, None),
            ("Заёмщик Должников", "T-007", buh, sch52, zmo, D("70000"),
             coef, D("1.5"), None, (D("120000"), 12, today.replace(day=1)),
             None, None),
            ("Безотдела Ничейный", "T-008", None, sch52, zmo, D("50000"),
             coef, D("1.5"), None, None,
             None, None),
            ("Бесграфика Неясный", "T-009", ito, None, zmo, D("50000"),
             coef, D("1.5"), None, None,
             None, None),
            ("Сменщик Первый", "T-010", ito, sch22, zmo, D("60000"),
             coef, D("1.5"), None, None,
             None, None),
            # Совместитель: основная позиция инженера, вторая заводится ниже.
            ("Совместитель Двойнов", "T-011", ito, sch52, zmo, D("60000"),
             coef, D("1.5"), None, None,
             None, None),
            # Почасовик: оклада нет, ставка за час — в поле ниже (rate=None).
            ("Почасовик Часов", "T-012", ito, sch52, kft, None,
             coef, D("1.5"), None, None,
             None, None),
        ]

        for (name, tab, dept, sch, comp, rate, wtype, wcoef, wfixed,
             loan, email, role) in rows:
            existing = db.query(Employee).filter_by(tab_number=tab).first()
            if existing:
                continue
            emp = Employee(
                full_name=name,
                tab_number=tab,
                position="Сотрудник",
                department_id=dept.id if dept else None,
                schedule_id=sch.id if sch else None,
                default_company_id=comp.id if comp else None,
                rate=rate,
                weekend_pay_type=wtype,
                weekend_coefficient=wcoef,
                weekend_fixed_rate=wfixed,
                overtime_coefficient=D("1.5"),
                is_active=True,
            )
            if tab == "T-012":
                # Почасовая оплата: оклада нет, платим за фактические часы.
                emp.pay_type = "hourly"
                emp.hour_rate = D("450")
            if loan:
                emp.loan_amount, emp.loan_term_months, emp.loan_start_date = loan
            if email:
                emp.email = email
                emp.hashed_password = hash_password("Test1234!")
                emp.role = role
                emp.must_change_password = False
            db.add(emp)
            db.flush()
            created["employees"] += 1

        # --- Совместительство (task_positions ч.A) ---
        # Вторая позиция у T-011: другая должность, другой оклад, другое юрлицо.
        # Расчёт по каждой идёт отдельно, «к выплате» не суммируется.
        moonlighter = db.query(Employee).filter_by(tab_number="T-011").first()
        if moonlighter and len(moonlighter.positions) == 1:
            moonlighter.primary_position.title = "Инженер"
            moonlighter.positions.append(EmployeePosition(
                title="Электрик",
                rate=D("30000"),
                schedule_id=sch52.id,
                department_id=ito.id,
                company_id=kft.id,
            ))

        # --- Менеджеры и табельщики отделов (task_org_structure ч.2) ---
        # Управляемые отделы задаются ОТДЕЛЬНО от department_id: менеджер ИТО
        # ведёт сразу два отдела (проверка мульти-отдела), менеджер охраны — один.
        # Табельщик сидит в той же связи и на том же ИТО, что менеджер: проверка,
        # что руководитель и табельщик отдела — разные люди с разными правами.
        managed = {
            "qa.manager@example.com": [ito, buh],
            "qa.manager2@example.com": [sec_dept],
            "qa.timekeeper@example.com": [ito],
        }
        for email, depts in managed.items():
            mgr = db.query(Employee).filter_by(email=email).first()
            if mgr and not mgr.managed_departments:
                mgr.managed_departments = depts

        db.commit()
    finally:
        db.close()

    print("Создано:")
    for k, v in created.items():
        print(f"  {k:14} {v}")
    print("Пароль всех QA-учёток: Test1234!")


def main() -> None:
    parser = argparse.ArgumentParser(description="zemlya-tabel CLI")
    subparsers = parser.add_subparsers(dest="command")

    cmd = subparsers.add_parser("create-admin", help="Create initial system admin employee")
    cmd.add_argument("--email", required=True)
    cmd.add_argument("--password", required=True)
    cmd.add_argument("--full-name", required=True, dest="full_name")

    cmd2 = subparsers.add_parser("reset-password", help="Reset password for an employee")
    cmd2.add_argument("--email", required=True)
    cmd2.add_argument("--new-password", required=True, dest="new_password")

    cmd3 = subparsers.add_parser(
        "reset-data", help="Wipe all data except system admin (dev only)"
    )
    cmd3.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    subparsers.add_parser("seed-test-data", help="Populate DB with test data")

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.email, args.password, args.full_name)
    elif args.command == "reset-password":
        reset_password(args.email, args.new_password)
    elif args.command == "reset-data":
        reset_data(assume_yes=args.yes)
    elif args.command == "seed-test-data":
        seed_test_data()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
