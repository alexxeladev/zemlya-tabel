"""Тесты импорта сотрудников из Excel (task_employee_import)."""
import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.schedules import Schedule
from app.services.employee_import import COLUMNS, EXAMPLE_MARKER
from tests.conftest import get_token


@pytest.fixture
def refs(db_session):
    """Справочники: компании, отдел, графики."""
    company = Company(code="ZMO", name='ООО "Комфорт"')
    other = Company(code="KFT", name="Земля МО")
    dept = Department(name="ИТО", code="ITO")
    weekday = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday")
    cyclic = Schedule(
        name="2/2 смена 1", hours_per_shift=12, schedule_type="cyclic",
        cycle_start_date=datetime.date(2026, 5, 31), cycle_work_days=2, cycle_off_days=2,
    )
    db_session.add_all([company, other, dept, weekday, cyclic])
    db_session.commit()
    for obj in (company, other, dept, weekday, cyclic):
        db_session.refresh(obj)
    return {
        "company": company, "other": other, "dept": dept,
        "weekday": weekday, "cyclic": cyclic,
    }


@pytest.fixture
def admin_token(client, admin_user):
    return get_token(client, "admin@example.com", "admin123")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_file(rows: list[list], with_example: bool = True) -> bytes:
    """Собрать .xlsx как заполненный шаблон: заголовки, пример, затем данные."""
    wb = Workbook()
    ws = wb.active
    ws.append([c.title for c in COLUMNS])
    if with_example:
        ws.append([c.example for c in COLUMNS])
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, token: str, content: bytes, confirm: bool = False):
    return client.post(
        "/api/employees/import",
        params={"confirm": confirm},
        files={"file": ("employees.xlsx", content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth(token),
    )


# ── Шаблон ────────────────────────────────────────────────────────────────────

def test_template_download(client, admin_token, refs):
    resp = client.get("/api/employees/import/template", headers=auth(admin_token))
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    assert "shablon_sotrudnikov.xlsx" in resp.headers["content-disposition"]

    wb = load_workbook(BytesIO(resp.content))
    ws = wb["Сотрудники"]
    assert [c.value for c in ws[1]] == [c.title for c in COLUMNS]
    # вторая строка — пример, помечен явно
    assert ws.cell(row=2, column=1).value == EXAMPLE_MARKER
    assert ws.cell(row=2, column=2).value == "Иванов Иван Иванович"

    # лист со справочниками — чтобы заполняющий знал допустимые значения
    ref_ws = wb["Справочники"]
    values = {c.value for row in ref_ws.iter_rows() for c in row if c.value}
    assert 'ZMO — ООО "Комфорт"' in values
    assert "ИТО" in values
    assert "5/2" in values


def test_import_is_admin_only(client, db_session, refs):
    """Импорт — только admin: бухгалтер получает 403, аноним — 401."""
    db_session.add(Employee(
        full_name="Бухгалтер", email="acc@example.com", role="accountant",
        is_active=True, hashed_password=hash_password("acc12345"),
    ))
    db_session.commit()
    token = get_token(client, "acc@example.com", "acc12345")

    assert client.get("/api/employees/import/template", headers=auth(token)).status_code == 403
    assert client.get("/api/employees/import/template").status_code == 401
