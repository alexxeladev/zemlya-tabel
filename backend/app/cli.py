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
    from app.models.company_shares import CompanyShareOverride, EmployeeCompanyShare
    from app.models.departments import Department
    from app.models.employee_adjustments import EmployeeAdjustment
    from app.models.employees import Employee
    from app.models.loan_deductions import LoanDeduction
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

        # Зависимые от employees / справочников — удаляем первыми.
        for model in (
            AuditLog,
            TimesheetEntry,
            TimesheetPeriod,
            EmployeeAdjustment,
            LoanDeduction,
            CompanyShareOverride,
            EmployeeCompanyShare,
        ):
            stats[model.__tablename__] = db.query(model).delete(synchronize_session=False)

        # Сотрудники, кроме системных админов.
        emp_q = db.query(Employee)
        if keep_ids:
            emp_q = emp_q.filter(Employee.id.notin_(keep_ids))
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
        ito = get_or_create(Department, {"code": "ITO"},
                            {"name": "ИТО", "is_active": True}, "departments")
        buh = get_or_create(Department, {"code": "BUH"},
                            {"name": "Бухгалтерия", "is_active": True}, "departments")

        # --- Графики ---
        sch52 = get_or_create(Schedule, {"name": "5/2"},
                            {"hours_per_shift": 8, "schedule_type": "standard",
                             "description": "Пятидневка по производственному календарю",
                             "is_active": True}, "schedules")
        get_or_create(Schedule, {"name": "6/1"},
                            {"hours_per_shift": 8, "schedule_type": "standard",
                             "description": "Шестидневка", "is_active": True}, "schedules")

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
