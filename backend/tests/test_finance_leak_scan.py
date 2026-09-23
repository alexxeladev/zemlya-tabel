"""Сканер утечек денег табельщику — рекурсивно, по ВСЕМ GET (task_stage1 п.1.3).

Маскирование идёт по явным спискам полей, и списки покрывали только верхний
уровень схем. Фонд ночных смен отдела утекал через вложенный `department` —
в табеле, списке сотрудников, позициях и `/auth/me`: по фонду ставка смены
вычисляется делением на дни месяца, а число смен табельщик видит.

Тест не знает, КАК устроено маскирование. Он обходит каждый GET-маршрут
приложения под табельщиком и ищет в ответе:

* ключи, похожие на деньги, с непустым значением — на любой глубине;
* сами суммы из фикстур (они подобраны уникальными) — в сыром тексте ответа,
  на случай денежного поля с «невинным» именем.

Новый GET-маршрут попадает под проверку сам; новый параметр пути уронит тест с
подсказкой, что дописать в `PATH_PARAMS`.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.company_shares import EmployeeCompanyShare
from app.models.employee_adjustments import EmployeeAdjustment
from app.models.employees import Employee
from app.models.night_shifts import NightShift
from app.models.production_calendars import ProductionCalendar
from app.models.schedules import Schedule
from app.models.timesheet_entries import TimesheetEntry
from app.models.timesheet_periods import TimesheetPeriod
from app.services.guard_duty import create_assignment
from app.services.positions import create_position
from tests.conftest import app_routes, get_token
from tests.test_vahta import (  # noqa: F401 — фикстуры модуля вахты
    FIRST_HALF,
    MONTH,
    YEAR,
    companies,
    crew,
    gbr_place,
    guard_dept,
    other_dept,
    rodionov,
    zone,
)

AUGUST = {"year": 2026, "months": [{"month": 8, "days": "1,2,8,9,15,16,22,23,29,30"}]}

# Суммы фикстур — уникальные, чтобы искать их в сыром тексте ответа.
NIGHT_FUND = Decimal("123457")
RATE = Decimal("51239")
SECOND_RATE = Decimal("31277")
FIXED_RATE = Decimal("1913")
LOAN = Decimal("77773")
PREMIUM = Decimal("4327")
GUARD_PREMIUM = Decimal("2339")
# Ставка экипажа из фикстуры `crew` — 5000 — слишком «круглая» для поиска по
# тексту; ставка строки вахты задаётся своей.
GUARD_RATE = Decimal("5683")
SENTINELS = [NIGHT_FUND, RATE, SECOND_RATE, FIXED_RATE, LOAN, PREMIUM, GUARD_PREMIUM, GUARD_RATE]

# Ключ «про деньги»: ставки, суммы, фонд, заём, выплаты, коэффициенты оплаты.
MONEY_KEY = re.compile(
    r"rate|amount|salary|fund(?!ing_company)|loan|payout|premium|kpi|deduction|"
    r"coefficient|penalty|tax|accrued|grand_total|^total$|unallocated|rounding"
)
# Похожи по имени, но не деньги.
NOT_MONEY = {
    "loan_is_manual",        # признак ручной правки, без суммы
    "loan_term_months",      # гасится вместе с займом, но сам по себе — срок
}


@pytest.fixture
def world(db_session: Session, companies, guard_dept, other_dept, gbr_place, rodionov):
    """Отдел с фондом ночных, окладник с займом/коэффициентами/премией/ночной
    сменой/совместительством/процентами, строка вахты — и табельщик обоих отделов."""
    db_session.add(ProductionCalendar(year=2026, data=AUGUST, source="manual"))
    schedule = Schedule(name="5/2", schedule_type="weekday", hours_per_shift=8)
    db_session.add(schedule)
    other_dept.night_shift_fund = NIGHT_FUND
    other_dept.head_company_id = companies["ZMO"].id
    db_session.commit()

    worker = Employee(
        full_name="Сотрудник С Деньгами", tab_number="T-0300", is_active=True,
        loan_amount=LOAN, loan_term_months=7, loan_start_date=date(2026, 1, 1),
    )
    db_session.add(worker)
    db_session.commit()
    main = worker.ensure_primary_position()
    main.department_id = other_dept.id
    main.company_id = companies["ZMO"].id
    main.schedule_id = schedule.id
    main.pay_type = "salary"
    main.rate = RATE
    main.weekend_pay_type = "fixed_rate"
    main.weekend_fixed_rate = FIXED_RATE
    main.holiday_coefficient = Decimal("2.5")
    main.overtime_coefficient = Decimal("1.5")
    main.has_night_shifts = True
    db_session.commit()
    second = create_position(worker, {
        "title": "Электрик", "pay_type": "salary", "rate": SECOND_RATE,
        "schedule_id": schedule.id, "department_id": other_dept.id,
        "company_id": companies["EKS"].id,
    })
    db_session.commit()
    for day in (3, 4, 8):  # 8-е — суббота: часы вне графика по фикс-ставке
        db_session.add(TimesheetEntry(
            employee_id=worker.id, position_id=main.id, work_date=date(YEAR, MONTH, day),
            company_id=companies["ZMO"].id, hours=10,
        ))
    db_session.add(NightShift(
        employee_id=worker.id, position_id=main.id, work_date=date(YEAR, MONTH, 5),
    ))
    db_session.add(EmployeeAdjustment(
        employee_id=worker.id, position_id=main.id, year=YEAR, month=MONTH,
        kind="premium", amount=PREMIUM, reason="за объект",
        funding_company_id=companies["SEC"].id,
    ))
    db_session.add(EmployeeCompanyShare(
        employee_id=worker.id, position_id=main.id,
        company_id=companies["ZMO"].id, percent=Decimal("100"),
    ))
    period = TimesheetPeriod(department_id=other_dept.id, year=YEAR, month=MONTH, status="draft")
    db_session.add(period)

    assignment = create_assignment(
        db_session, year=YEAR, month=MONTH, place=gbr_place,
        position=rodionov.primary_position, days=FIRST_HALF,
    )
    assignment.rate = GUARD_RATE
    assignment.premium_h1 = GUARD_PREMIUM

    # Табельщик сам числится в отделе и сам с окладом: своя карточка — тоже
    # карточка, а `/auth/me` раньше отдавал её без маскирования.
    keeper = Employee(
        full_name="Табельщик Сканера", email="keeper@example.com",
        hashed_password=hash_password("Test1234!"), role="timekeeper", is_active=True,
        loan_amount=LOAN, loan_term_months=7, loan_start_date=date(2026, 1, 1),
    )
    db_session.add(keeper)
    db_session.commit()
    keeper_position = keeper.ensure_primary_position()
    keeper_position.department_id = other_dept.id
    keeper_position.schedule_id = schedule.id
    keeper_position.pay_type = "salary"
    keeper_position.rate = RATE
    keeper.managed_departments = [other_dept, guard_dept]
    db_session.commit()
    return {
        "dept_id": other_dept.id, "emp_id": worker.id, "company_id": companies["ZMO"].id,
        "schedule_id": schedule.id, "period_id": period.id, "second_position_id": second.id,
    }


def _get_paths() -> list[str]:
    skip = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/health"}
    # Источник — схема OpenAPI (`app_routes` в conftest): в `app.routes`
    # маршруты подключённых роутеров с FastAPI 0.138 не лежат плоским списком,
    # и сканер молча смотрел бы 5 адресов вместо полусотни.
    return sorted({path for method, path in app_routes() if method == "GET"} - skip)


def _urls(world: dict) -> list[str]:
    path_params = {
        "year": YEAR, "month": MONTH,
        "dept_id": world["dept_id"], "emp_id": world["emp_id"],
        "company_id": world["company_id"], "schedule_id": world["schedule_id"],
        "period_id": world["period_id"],
        # История условий позиции (task_stage3_historicity) — там ставки.
        "position_id": world["second_position_id"],
    }
    urls = []
    for path in _get_paths():
        names = re.findall(r"{(\w+)}", path)
        unknown = [n for n in names if n not in path_params]
        assert not unknown, (
            f"Новый параметр пути {unknown} в {path}: допишите его в `path_params` "
            "сканера, иначе маршрут останется без проверки на утечку денег"
        )
        urls.append(path.format(**path_params))
    # Те же маршруты с параметрами, которые меняют состав ответа.
    base = f"/api/timesheet/{YEAR}/{MONTH}"
    urls += [
        f"{base}?include_payroll=true",
        f"{base}?include_payroll=true&department_id={world['dept_id']}",
        f"/api/dashboard/{YEAR}/{MONTH}?to_year={YEAR}&to_month={MONTH}",
        f"/api/departments/{world['dept_id']}/move-preview?target_company_id={world['company_id']}",
        "/api/vahta/similar?full_name=Караулов",
    ]
    return urls


def _is_empty(value) -> bool:
    if value is None or value is False or value == "" or value == [] or value == {}:
        return True
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


def _money_leaks(node, path="$") -> list[str]:
    """Рекурсивно: денежные ключи с непустым значением на любой глубине."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if MONEY_KEY.search(key) and key not in NOT_MONEY and not isinstance(value, (dict, list)):
                if not _is_empty(value):
                    found.append(f"{here} = {value!r}")
            found += _money_leaks(value, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += _money_leaks(item, f"{path}[{i}]")
    return found


def _sentinel_leaks(text: str) -> list[str]:
    return [f"сумма {s} в сыром ответе" for s in SENTINELS if str(s) in text]


def _scan(client: TestClient, email: str, world: dict, statuses: dict | None = None) -> tuple[dict[str, list[str]], int]:
    headers = {"Authorization": f"Bearer {get_token(client, email, 'Test1234!')}"}
    leaks: dict[str, list[str]] = {}
    scanned = 0
    for url in _urls(world):
        resp = client.get(url, headers=headers)
        if statuses is not None:
            statuses[url] = resp.status_code
        if resp.status_code != 200:
            continue
        is_json = resp.headers.get("content-type", "").startswith("application/json")
        if not is_json:
            continue  # Т-13: xlsx, рублей в нём нет — проверяется в test_timesheet_export
        scanned += 1
        found = _money_leaks(resp.json()) + _sentinel_leaks(resp.text)
        if found:
            leaks[url] = found
    return leaks, scanned


def test_scanner_sees_money_in_admin_responses(client, db_session, world):
    """Самопроверка сканера: под админом он ОБЯЗАН находить деньги — иначе пустой
    результат под табельщиком ничего не доказывает."""
    db_session.add(Employee(
        full_name="Админ Сканера", email="scan.admin@example.com",
        hashed_password=hash_password("Test1234!"), role="admin", is_active=True,
    ))
    db_session.commit()

    leaks, _ = _scan(client, "scan.admin@example.com", world)
    text = "\n".join(line for lines in leaks.values() for line in lines)

    for needle in ("night_shift_fund", "rate", "loan_amount", "premium", "net_payout"):
        assert needle in text, f"сканер не увидел «{needle}» даже под админом"
    for sentinel in SENTINELS:
        assert f"сумма {sentinel}" in text, f"сумма {sentinel} не встречается — фикстура не работает"


def test_timekeeper_gets_no_money_in_any_get(client, world):
    leaks, scanned = _scan(client, "keeper@example.com", world)

    assert scanned >= 25, f"просканировано всего {scanned} ответов — сканер ослеп"
    report = "\n".join(f"{url}\n    " + "\n    ".join(found) for url, found in leaks.items())
    assert not leaks, "Табельщик получил деньги:\n" + report


@pytest.mark.parametrize("url_key", ["timesheet", "employees", "positions", "me"])
def test_night_fund_does_not_leak_through_nested_department(client, world, url_key):
    """Три места из задачи (и четвёртое — позиции): фонда нет, отдел на месте."""
    url = {
        "timesheet": f"/api/timesheet/{YEAR}/{MONTH}",
        "employees": "/api/employees",
        "positions": f"/api/employees/{world['emp_id']}/positions",
        "me": "/api/auth/me",
    }[url_key]
    headers = {"Authorization": f"Bearer {get_token(client, 'keeper@example.com', 'Test1234!')}"}

    resp = client.get(url, headers=headers)

    assert resp.status_code == 200
    assert str(NIGHT_FUND) not in resp.text
    assert '"department":{' in resp.text.replace(" ", ""), "вложенный отдел должен остаться"


# ── Роль employee: свой оклад — да, бюджет отдела — нет ──────────────────────

def _fund_leaks(node, path="$") -> list[str]:
    """Только деньги ОТДЕЛА: собственный оклад сотруднику виден по решению заказчика."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if key in ("night_shift_fund", "fund") and not _is_empty(value):
                found.append(f"{here} = {value!r}")
            found += _fund_leaks(value, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += _fund_leaks(item, f"{path}[{i}]")
    return found


def test_employee_does_not_see_department_night_fund_anywhere(client, db_session, world):
    """Фонд ночных — бюджет отдела: по нему и числу смен восстанавливается
    надбавка коллег. Сотрудник видит СВОЙ оклад (`/auth/me`), но не фонд."""
    worker = db_session.get(Employee, world["emp_id"])
    worker.email = "plain.employee@example.com"
    worker.hashed_password = hash_password("Test1234!")
    worker.role = "employee"
    db_session.commit()
    headers = {"Authorization": f"Bearer {get_token(client, 'plain.employee@example.com', 'Test1234!')}"}

    leaks, scanned = {}, 0
    for url in _urls(world):
        resp = client.get(url, headers=headers)
        if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("application/json"):
            continue
        scanned += 1
        found = _fund_leaks(resp.json())
        if str(NIGHT_FUND) in resp.text:
            found.append(f"сумма {NIGHT_FUND} в сыром ответе")
        if found:
            leaks[url] = found

    assert scanned >= 8, f"просканировано всего {scanned} ответов"
    assert not leaks, "Сотрудник получил фонд отдела:\n" + "\n".join(
        f"{u}: {f[:3]}" for u, f in leaks.items())
    # Собственный оклад остаётся — решение заказчика, не «чиним заодно».
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["rate"] is not None and me["department"] is not None


def test_manager_still_sees_the_fund(client, db_session, world, other_dept):
    boss = Employee(full_name="Менеджер Сканера", email="scan.manager@example.com",
                    hashed_password=hash_password("Test1234!"), role="manager", is_active=True)
    db_session.add(boss)
    db_session.commit()
    boss.managed_departments = [other_dept]
    db_session.commit()
    headers = {"Authorization": f"Bearer {get_token(client, 'scan.manager@example.com', 'Test1234!')}"}
    depts = client.get("/api/departments", headers=headers).json()
    assert any(d["night_shift_fund"] is not None for d in depts)
