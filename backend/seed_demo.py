"""Демо-данные для ручной проверки ведомости «Расчёт ЗП» (задача 3.11b)."""
from datetime import date
from decimal import Decimal

from app.core.security import hash_password
from app.database import SessionLocal
from app.models.companies import Company
from app.models.company_shares import EmployeeCompanyShare
from app.models.departments import Department
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry

YEAR, MONTH = 2026, 5
# Май: нерабочие 3,4,10,11,17,18,24,25,31 → 22 рабочих дня, норма 176ч (8ч/смена)
CAL = {"year": YEAR, "months": [{"month": MONTH, "days": "3,4,10,11,17,18,24,25,31"}]}
WORKDAYS = [d for d in range(1, 32) if d not in (3, 4, 10, 11, 17, 18, 24, 25, 31)]

db = SessionLocal()


def get_or_create(model, defaults=None, **kw):
    obj = db.query(model).filter_by(**kw).first()
    if obj:
        return obj
    obj = model(**kw, **(defaults or {}))
    db.add(obj)
    db.flush()
    return obj


# Админ
if not db.query(Employee).filter_by(email="admin@example.com").first():
    db.add(Employee(full_name="Администратор", email="admin@example.com",
                    hashed_password=hash_password("admin12345"), role="admin",
                    is_active=True, must_change_password=False, is_system_admin=True))

dept = get_or_create(Department, code="SKLAD", defaults={"name": "Склад", "is_active": True})
sch = get_or_create(Schedule, name="5/2", defaults={"hours_per_shift": 8,
                    "schedule_type": "standard", "is_active": True})
kmf = get_or_create(Company, code="KMF", defaults={"name": "Комфорт", "is_active": True})
zmo = get_or_create(Company, code="ZMO", defaults={"name": "ЗМО", "is_active": True})
ghs = get_or_create(Company, code="GHS", defaults={"name": "ГХС", "is_active": True})

# Календарь
if not db.query(ProductionCalendar).filter_by(year=YEAR).first():
    db.add(ProductionCalendar(year=YEAR, data=CAL, source="manual"))

db.flush()

WORKERS = [
    ("Иванов Иван (кладовщик)", "K-001", Decimal("80000"),
     [(kmf, 50), (zmo, 30), (ghs, 20)], Decimal("10000"), Decimal("5000")),
    ("Петров Пётр (грузчик)", "K-002", Decimal("60000"),
     [(kmf, 70), (zmo, 30)], Decimal("0"), Decimal("0")),
    ("Сидорова Анна (логист)", "K-003", Decimal("90000"),
     [(zmo, 60), (ghs, 40)], Decimal("15000"), Decimal("0")),
]

for name, tab, rate, shares, premium, kpi in WORKERS:
    emp = db.query(Employee).filter_by(tab_number=tab).first()
    if not emp:
        emp = Employee(full_name=name, tab_number=tab, position="Сотрудник склада",
                       is_active=True, rate=rate, schedule_id=sch.id,
                       default_company_id=shares[0][0].id, department_id=dept.id)
        db.add(emp)
        db.flush()
    # часы — полная норма
    db.query(TimesheetEntry).filter_by(employee_id=emp.id).delete()
    for d in WORKDAYS:
        db.add(TimesheetEntry(employee_id=emp.id, work_date=date(YEAR, MONTH, d),
                              company_id=shares[0][0].id, hours=8))
    # проценты распределения по умолчанию
    db.query(EmployeeCompanyShare).filter_by(employee_id=emp.id).delete()
    for comp, pct in shares:
        db.add(EmployeeCompanyShare(employee_id=emp.id, company_id=comp.id,
                                    percent=Decimal(str(pct))))
    # премии/KPI
    db.query(EmployeeAdjustment).filter_by(employee_id=emp.id, year=YEAR, month=MONTH).delete()
    if premium > 0:
        db.add(EmployeeAdjustment(employee_id=emp.id, year=YEAR, month=MONTH,
                                  kind="premium", amount=premium, reason="Премия за месяц"))
    if kpi > 0:
        db.add(EmployeeAdjustment(employee_id=emp.id, year=YEAR, month=MONTH,
                                  kind="kpi", amount=kpi, reason="KPI за месяц"))

db.commit()
print("seeded employees:", db.query(Employee).count())
db.close()
