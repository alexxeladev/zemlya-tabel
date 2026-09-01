"""
Журнал изменений справочных данных (task_audit_log).

Проверка построена ПО ТРЕБОВАНИЮ, а не по реализации: сначала выписано, что
обещано пользователю — «кто, когда, какое поле, было → стало, источник», записи
только о реальных изменениях, массовая операция одним списком, история в
карточке, доступ только admin, — и на каждое обещание есть проверка.

Отдельный блок — на главную ловушку задачи: правка через compat-аксессоры
(`emp.rate`) должна попадать в журнал КАК ПРАВКА ПОЗИЦИИ, иначе у совместителя
журнал будет показывать не то рабочее место, которое реально поехало.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.companies import Company
from app.models.departments import Department
from app.models.employees import Employee
from app.models.positions import EmployeePosition
from app.models.reference_changes import (
    SOURCE_BULK,
    SOURCE_IMPORT,
    SOURCE_UI,
    ReferenceChange,
)
from app.models.schedules import Schedule
from app.services.reference_audit import audit_operation, set_audit_actor
from tests.conftest import get_token


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def company(db_session: Session) -> Company:
    c = Company(name='ООО «Земля МО»', code="ZMO", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def company2(db_session: Session) -> Company:
    c = Company(name='ООО «Комфорт»', code="KFT", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def schedule(db_session: Session) -> Schedule:
    s = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday", is_active=True)
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture
def department(db_session: Session, company: Company) -> Department:
    d = Department(name="ИТО", code="ITO", head_company_id=company.id, is_active=True)
    db_session.add(d)
    db_session.commit()
    return d


@pytest.fixture
def department2(db_session: Session, company2: Company) -> Department:
    d = Department(name="Охрана", code="SEC", head_company_id=company2.id, is_active=True)
    db_session.add(d)
    db_session.commit()
    return d


@pytest.fixture
def employee(db_session: Session, department: Department, company: Company,
             schedule: Schedule) -> Employee:
    emp = Employee(
        full_name="Иванов Иван Иванович",
        tab_number="T-0001",
        department_id=department.id,
        default_company_id=company.id,
        schedule_id=schedule.id,
        rate=Decimal("50000"),
        is_active=True,
    )
    emp.primary_position.title = "Инженер"
    db_session.add(emp)
    db_session.commit()
    return emp


def changes(db: Session, **where) -> list[ReferenceChange]:
    stmt = select(ReferenceChange)
    for key, value in where.items():
        stmt = stmt.where(getattr(ReferenceChange, key) == value)
    return list(db.execute(stmt.order_by(ReferenceChange.id)).scalars().all())


def wipe(db: Session) -> None:
    """Очистить журнал: фикстуры сами по себе пишут записи о создании."""
    db.execute(ReferenceChange.__table__.delete())
    db.commit()


# ── 1. Изменение поля: кто, когда, что, было → стало, источник ────────────────

class TestFieldChange:
    def test_position_field_change_is_recorded(self, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.primary_position.title = "Ведущий инженер"
        db_session.commit()

        rows = changes(db_session, entity_type="employee_position", field="title")
        assert len(rows) == 1
        row = rows[0]
        assert row.action == "update"
        assert row.old_value == "Инженер"
        assert row.new_value == "Ведущий инженер"
        assert row.actor_id == admin_user.id
        assert row.actor_name == admin_user.full_name
        assert row.source == SOURCE_UI
        assert row.created_at is not None
        # Имя сущности сохраняется текстом — запись должна остаться понятной
        # после удаления сотрудника.
        assert "Иванов Иван Иванович" in row.entity_label

    def test_employee_field_change_is_recorded(self, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.full_name = "Иванов Иван Петрович"
        db_session.commit()

        rows = changes(db_session, entity_type="employee", field="full_name")
        assert len(rows) == 1
        assert rows[0].old_value == "Иванов Иван Иванович"
        assert rows[0].new_value == "Иванов Иван Петрович"

    def test_each_changed_field_is_its_own_row(self, db_session, employee, admin_user):
        """Две правки в одном сохранении — две строки, а не одна с кашей."""
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.primary_position.rate = Decimal("60000")
        employee.primary_position.has_night_shifts = True
        db_session.commit()

        fields = {r.field for r in changes(db_session, entity_type="employee_position")}
        assert fields == {"rate", "has_night_shifts"}

    def test_reference_fields_store_name_not_bare_id(
        self, db_session, employee, department2, admin_user
    ):
        """У ссылки в журнале должно стоять имя: «#7» ничего не объясняет."""
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.primary_position.department_id = department2.id
        db_session.commit()

        row = changes(db_session, field="department_id")[0]
        assert "ИТО" in row.old_value
        assert "Охрана" in row.new_value

    def test_department_and_company_and_schedule_are_audited(
        self, db_session, department, company, schedule, admin_user
    ):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        department.night_shift_fund = Decimal("50000")
        company.short_name = "ЗМО"
        schedule.hours_per_shift = 12
        db_session.commit()

        got = {(r.entity_type, r.field) for r in changes(db_session)}
        assert ("department", "night_shift_fund") in got
        assert ("company", "short_name") in got
        assert ("schedule", "hours_per_shift") in got

    def test_operational_data_is_not_audited(self, db_session, employee, company, admin_user):
        """Часы и отсутствия — операционка, в журнал справочников не идут."""
        from datetime import date

        from app.models.employee_absences import EmployeeAbsence
        from app.models.timesheet_entries import TimesheetEntry

        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        db_session.add(TimesheetEntry(
            employee_id=employee.id, position_id=employee.primary_position.id,
            work_date=date(2026, 7, 1), company_id=company.id, hours=8,
        ))
        db_session.add(EmployeeAbsence(
            employee_id=employee.id, work_date=date(2026, 7, 2), kind="vacation",
        ))
        db_session.commit()

        assert changes(db_session) == []


# ── 2. Только реальные изменения ──────────────────────────────────────────────

class TestNoNoise:
    def test_saving_without_changes_writes_nothing(self, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        # Ровно то, что делает «сохранить» в форме без правок.
        employee.full_name = employee.full_name
        employee.primary_position.rate = employee.primary_position.rate
        employee.primary_position.title = employee.primary_position.title
        db_session.commit()

        assert changes(db_session) == []

    def test_only_changed_field_of_many_is_recorded(self, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.primary_position.title = employee.primary_position.title
        employee.primary_position.rate = Decimal("70000")
        db_session.commit()

        rows = changes(db_session)
        assert [r.field for r in rows] == ["rate"]


# ── 3. Создание и удаление ────────────────────────────────────────────────────

class TestCreateDelete:
    def test_create_is_recorded(self, db_session, department, company, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        emp = Employee(full_name="Петров Пётр", tab_number="T-0002",
                       department_id=department.id, default_company_id=company.id)
        db_session.add(emp)
        db_session.commit()

        created = changes(db_session, action="create")
        types = {r.entity_type for r in created}
        # Сотрудник и его основная позиция — две разные сущности.
        assert types == {"employee", "employee_position"}
        emp_row = next(r for r in created if r.entity_type == "employee")
        assert emp_row.entity_id == emp.id
        assert emp_row.entity_label == "Петров Пётр"

    def test_delete_is_recorded(self, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        extra = EmployeePosition(employee_id=employee.id, title="Электрик")
        db_session.add(extra)
        db_session.commit()
        wipe(db_session)

        db_session.delete(extra)
        db_session.commit()

        rows = changes(db_session, action="delete")
        assert len(rows) == 1
        assert rows[0].entity_type == "employee_position"
        assert "Электрик" in rows[0].entity_label


# ── 4. Compat-аксессоры: пишем ПОЗИЦИЮ, а не сотрудника ───────────────────────

class TestCompatAccessors:
    """Главная ловушка задачи.

    `emp.rate = X` выглядит как правка сотрудника, а на деле меняет ОСНОВНУЮ
    позицию. Если журнал напишет «сотрудник / оклад», он соврёт: у совместителя
    непонятно, какое из рабочих мест поехало.
    """

    def test_compat_write_is_logged_as_position(self, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.rate = Decimal("77000")   # ← старый плоский API
        db_session.commit()

        rows = changes(db_session, field="rate")
        assert len(rows) == 1
        assert rows[0].entity_type == "employee_position"
        assert rows[0].entity_id == employee.primary_position.id
        assert rows[0].old_value == "50000"
        assert rows[0].new_value == "77000"
        # Связь с человеком не потеряна — история карточки её найдёт.
        assert rows[0].employee_id == employee.id

    def test_moonlighter_records_the_real_position(
        self, db_session, employee, department2, admin_user
    ):
        """У совместителя compat-правка обязана указать ОСНОВНУЮ позицию, а
        вторая работа остаться нетронутой."""
        second = EmployeePosition(
            employee_id=employee.id, title="Электрик", department_id=department2.id,
            rate=Decimal("30000"), is_primary=False,
        )
        db_session.add(second)
        db_session.commit()
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.rate = Decimal("81000")
        db_session.commit()

        rows = changes(db_session, field="rate")
        assert len(rows) == 1
        assert rows[0].entity_id == employee.primary_position.id
        assert rows[0].entity_id != second.id
        assert "Инженер" in rows[0].entity_label


# ── 5. Массовые операции ──────────────────────────────────────────────────────

class TestBulkOperations:
    def test_bulk_rows_share_one_operation_id(
        self, db_session, employee, department, department2, company2, admin_user
    ):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        with audit_operation(db_session, SOURCE_BULK) as op_id:
            department.head_company_id = company2.id
            employee.primary_position.company_id = company2.id
            db_session.flush()
        db_session.commit()

        rows = changes(db_session)
        assert len(rows) >= 2
        assert {r.operation_id for r in rows} == {op_id}
        assert {r.source for r in rows} == {SOURCE_BULK}

    def test_source_is_restored_after_bulk_block(self, db_session, employee, admin_user):
        """После массовой операции обычные правки снова «интерфейс»."""
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        with audit_operation(db_session, SOURCE_BULK):
            employee.primary_position.title = "Мастер"
            db_session.flush()
        db_session.commit()

        employee.primary_position.title = "Начальник"
        db_session.commit()

        last = changes(db_session)[-1]
        assert last.source == SOURCE_UI
        assert last.operation_id is None

    def test_department_move_marks_all_rows_with_one_operation(
        self, db_session, employee, department, company2, admin_user
    ):
        """Настоящий перенос отдела: и отдел, и позиции — одной операцией."""
        from app.services.department_move import move_department

        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        move_department(db_session, department, company2, admin_user)
        db_session.commit()

        rows = changes(db_session)
        assert rows, "перенос отдела не оставил ни одной записи"
        op_ids = {r.operation_id for r in rows}
        assert len(op_ids) == 1 and None not in op_ids
        assert {r.source for r in rows} == {SOURCE_BULK}
        # Поехали и головная компания отдела, и юрлицо рабочего места.
        got = {(r.entity_type, r.field) for r in rows}
        assert ("department", "head_company_id") in got
        assert ("employee_position", "company_id") in got


# ── 6. Назначение ответственных ───────────────────────────────────────────────

class TestManagers:
    def test_assigning_manager_is_recorded(self, db_session, department, manager_user,
                                           admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        department.managers = [manager_user]
        db_session.commit()

        rows = changes(db_session, entity_type="department_managers")
        assert len(rows) == 1
        assert rows[0].new_value is not None
        assert manager_user.full_name in rows[0].new_value
        assert rows[0].old_value is None

    def test_removing_manager_is_recorded(self, db_session, department, manager_user,
                                          admin_user):
        department.managers = [manager_user]
        db_session.commit()
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        department.managers = []
        db_session.commit()

        rows = changes(db_session, entity_type="department_managers")
        assert len(rows) == 1
        assert manager_user.full_name in rows[0].old_value
        assert rows[0].new_value is None


# ── 7. API журнала: фильтры, постраничность, доступ ───────────────────────────

class TestAuditApi:
    def _admin_headers(self, client, admin_user):
        token = get_token(client, "admin@example.com", "admin123")
        return {"Authorization": f"Bearer {token}"}

    def test_admin_sees_journal(self, client, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        employee.primary_position.title = "Ведущий инженер"
        db_session.commit()

        resp = client.get("/api/audit", headers=self._admin_headers(client, admin_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        row = next(r for r in body["items"] if r["field"] == "title")
        assert row["old_value"] == "Инженер"
        assert row["new_value"] == "Ведущий инженер"
        # Подписи собирает бэк — на фронте второй копии словаря быть не должно.
        assert row["field_label"] == "Должность"
        assert row["entity_type_label"] == "Рабочее место"
        assert row["source_label"] == "Интерфейс"

    def test_manager_gets_403(self, client, db_session, manager_user):
        token = get_token(client, "manager@example.com", "manager123")
        resp = client.get("/api/audit", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_accountant_gets_403(self, client, db_session):
        emp = Employee(
            full_name="QA Бухгалтер", email="acc@example.com",
            hashed_password=hash_password("pass123"), role="accountant", is_active=True,
        )
        db_session.add(emp)
        db_session.commit()
        token = get_token(client, "acc@example.com", "pass123")
        resp = client.get("/api/audit", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_anonymous_gets_401(self, client):
        assert client.get("/api/audit").status_code == 401

    def test_filter_by_employee_includes_his_positions(
        self, client, db_session, employee, department2, admin_user
    ):
        """История в карточке: записи по человеку И по его рабочим местам."""
        other = Employee(full_name="Сидоров Сидор", tab_number="T-0009")
        db_session.add(other)
        db_session.commit()
        wipe(db_session)
        set_audit_actor(db_session, admin_user)

        employee.full_name = "Иванов И. И."
        employee.primary_position.rate = Decimal("90000")
        other.full_name = "Сидоров С. С."
        db_session.commit()

        headers = self._admin_headers(client, admin_user)
        body = client.get(
            f"/api/audit?employee_id={employee.id}", headers=headers
        ).json()
        got = {(r["entity_type"], r["field"]) for r in body["items"]}
        assert got == {("employee", "full_name"), ("employee_position", "rate")}
        assert all(r["employee_id"] == employee.id for r in body["items"])

    def test_filter_by_entity_type_and_source(self, client, db_session, employee,
                                              department, company2, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        employee.primary_position.title = "Мастер"
        db_session.commit()
        with audit_operation(db_session, SOURCE_BULK):
            department.head_company_id = company2.id
            db_session.flush()
        db_session.commit()

        headers = self._admin_headers(client, admin_user)
        by_type = client.get("/api/audit?entity_type=department", headers=headers).json()
        assert {r["entity_type"] for r in by_type["items"]} == {"department"}

        by_source = client.get(f"/api/audit?source={SOURCE_BULK}", headers=headers).json()
        assert by_source["items"] and all(
            r["source"] == SOURCE_BULK for r in by_source["items"]
        )

    def test_filter_by_operation_shows_whole_bulk(self, client, db_session, employee,
                                                  department, company2, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        employee.primary_position.title = "Не из операции"
        db_session.commit()

        with audit_operation(db_session, SOURCE_BULK) as op_id:
            department.head_company_id = company2.id
            employee.primary_position.company_id = company2.id
            db_session.flush()
        db_session.commit()

        headers = self._admin_headers(client, admin_user)
        body = client.get(f"/api/audit?operation_id={op_id}", headers=headers).json()
        assert body["total"] == 2
        assert {r["field"] for r in body["items"]} == {"head_company_id", "company_id"}

    def test_filter_by_actor(self, client, db_session, employee, admin_user, manager_user):
        wipe(db_session)
        set_audit_actor(db_session, manager_user)
        employee.primary_position.title = "От менеджера"
        db_session.commit()
        set_audit_actor(db_session, admin_user)
        db_session.info["audit_actor_id"] = admin_user.id
        db_session.info["audit_actor_name"] = admin_user.full_name
        employee.primary_position.title = "От админа"
        db_session.commit()

        headers = self._admin_headers(client, admin_user)
        body = client.get(f"/api/audit?actor_id={manager_user.id}", headers=headers).json()
        assert body["total"] == 1
        assert body["items"][0]["new_value"] == "От менеджера"

    def test_pagination(self, client, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        for i in range(7):
            employee.primary_position.title = f"Должность {i}"
            db_session.commit()

        headers = self._admin_headers(client, admin_user)
        first = client.get("/api/audit?limit=3&offset=0", headers=headers).json()
        second = client.get("/api/audit?limit=3&offset=3", headers=headers).json()
        assert first["total"] == 7 and len(first["items"]) == 3
        assert len(second["items"]) == 3
        # Страницы не пересекаются — порядок устойчив.
        assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})

    def test_filters_endpoint(self, client, db_session, employee, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        employee.primary_position.title = "Мастер"
        db_session.commit()

        headers = self._admin_headers(client, admin_user)
        body = client.get("/api/audit/filters", headers=headers).json()
        assert any(o["value"] == "employee_position" for o in body["entity_types"])
        assert any(o["value"] == SOURCE_IMPORT for o in body["sources"])
        assert any(int(o["value"]) == admin_user.id for o in body["actors"])

    def test_unknown_source_is_422(self, client, admin_user):
        headers = self._admin_headers(client, admin_user)
        assert client.get("/api/audit?source=nope", headers=headers).status_code == 422


# ── 8. Импорт из Excel — тоже массовая операция ───────────────────────────────

class TestImportSource:
    """Импорт обязан быть отличим от ручного заведения карточек: полсотни
    созданных сотрудников в ленте иначе выглядят как полсотни ручных правок."""

    def test_import_rows_share_operation_and_source(self, client, db_session):
        import datetime
        from io import BytesIO

        from openpyxl import Workbook

        from app.services.employee_import import COLUMNS

        company = Company(code="ZMO", name='ООО «Земля МО»')
        dept = Department(name="ИТО", code="ITO")
        sched = Schedule(name="5/2", hours_per_shift=8, schedule_type="weekday")
        admin = Employee(
            full_name="QA Админ", email="imp-admin@example.com",
            hashed_password=hash_password("admin123"), role="admin", is_active=True,
        )
        db_session.add_all([company, dept, sched, admin])
        db_session.commit()

        wb = Workbook()
        ws = wb.active
        ws.append([c.title for c in COLUMNS])
        ws.append([c.example for c in COLUMNS])
        for i in (1, 2):
            ws.append([f"IMP-{i}", f"Импортов Имп {i}", "ZMO", "ИТО", "Слесарь", "5/2",
                       "окладная", "50 000", "", "коэффициент", "1,5", "01.03.2026"])
        buf = BytesIO()
        wb.save(buf)

        wipe(db_session)
        token = get_token(client, "imp-admin@example.com", "admin123")
        resp = client.post(
            "/api/employees/import",
            params={"confirm": True},
            files={"file": ("employees.xlsx", buf.getvalue(),
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["created_count"] == 2

        rows = changes(db_session, source=SOURCE_IMPORT)
        assert rows, "импорт не оставил записей в журнале"
        op_ids = {r.operation_id for r in rows}
        assert len(op_ids) == 1 and None not in op_ids
        # По сотруднику и его основной позиции на каждого импортированного.
        assert {r.entity_type for r in rows} == {"employee", "employee_position"}
        assert len({r.employee_id for r in rows}) == 2
        assert all(r.action == "create" for r in rows)
        assert all(r.actor_name == "QA Админ" for r in rows)


# ── 9. Имена справочников добираются пакетно ──────────────────────────────────

class TestReferenceNames:
    """Ссылка в журнале должна читаться именем, даже если объекта не было в
    сессии: «#44» заставляет лезть в справочник, а журнал читают глазами."""

    def test_name_resolved_even_when_not_loaded(self, db_session, employee,
                                                department2, admin_user):
        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        old_dept_id = employee.primary_position.department_id
        new_dept_id = department2.id

        # Выбросить справочники из сессии: так и бывает при массовом переносе —
        # компанию-источник никто не загружал.
        db_session.expunge_all()
        pos = db_session.get(EmployeePosition, employee.primary_position.id)
        pos.department_id = new_dept_id
        db_session.commit()

        row = changes(db_session, field="department_id")[0]
        assert "ИТО" in row.old_value and f"#{old_dept_id}" in row.old_value
        assert "Охрана" in row.new_value and f"#{new_dept_id}" in row.new_value

    def test_deleted_reference_keeps_bare_id(self, db_session, employee, admin_user):
        """Справочник удалён — остаётся «#42», а не выдуманное имя."""
        from app.models.departments import Department as Dept

        wipe(db_session)
        set_audit_actor(db_session, admin_user)
        ghost = Dept(name="Исчезающий", code="GHOST")
        db_session.add(ghost)
        db_session.commit()
        ghost_id = ghost.id

        pos = employee.primary_position
        pos.department_id = ghost_id
        db_session.commit()
        wipe(db_session)

        pos.department_id = None
        db_session.execute(Dept.__table__.delete().where(Dept.id == ghost_id))
        db_session.commit()

        row = changes(db_session, field="department_id")[0]
        assert row.old_value == f"#{ghost_id}"


# ── 10. Распределение по юрлицам: карточка и дефолт отдела ────────────────────

class TestShares:
    """Наборы процентов переписываются целиком Core-DELETE мимо ORM, поэтому
    события сессии их НЕ видят. Пишутся одной записью «было → стало»."""

    def _admin_headers(self, client):
        return {"Authorization": f"Bearer {get_token(client, 'admin@example.com', 'admin123')}"}

    def test_card_shares_change_is_recorded(self, client, db_session, employee,
                                            company, company2, admin_user):
        wipe(db_session)
        headers = self._admin_headers(client)

        first = client.put(
            f"/api/employees/{employee.id}/company-shares",
            json={"shares": [{"company_id": company.id, "percent": "100"}]},
            headers=headers,
        )
        assert first.status_code == 200, first.text

        second = client.put(
            f"/api/employees/{employee.id}/company-shares",
            json={"shares": [
                {"company_id": company.id, "percent": "60"},
                {"company_id": company2.id, "percent": "40"},
            ]},
            headers=headers,
        )
        assert second.status_code == 200, second.text

        rows = changes(db_session, entity_type="employee_shares")
        assert len(rows) == 2
        # Первая правка: распределения не было вовсе.
        assert rows[0].old_value == "не задано"
        assert "100" in rows[0].new_value
        # Вторая: видно и что было, и что стало — названиями юрлиц, не id.
        assert "Земля МО" in rows[1].old_value and "100" in rows[1].old_value
        assert "Земля МО" in rows[1].new_value and "60" in rows[1].new_value
        assert "Комфорт" in rows[1].new_value and "40" in rows[1].new_value
        assert rows[1].employee_id == employee.id
        assert rows[1].field == "shares"

    def test_same_shares_saved_again_write_nothing(self, client, db_session, employee,
                                                   company, admin_user):
        headers = self._admin_headers(client)
        body = {"shares": [{"company_id": company.id, "percent": "100"}]}
        client.put(f"/api/employees/{employee.id}/company-shares", json=body, headers=headers)
        wipe(db_session)
        client.put(f"/api/employees/{employee.id}/company-shares", json=body, headers=headers)

        assert changes(db_session, entity_type="employee_shares") == []

    def test_clearing_shares_is_recorded_as_not_set(self, client, db_session, employee,
                                                    company, admin_user):
        headers = self._admin_headers(client)
        client.put(
            f"/api/employees/{employee.id}/company-shares",
            json={"shares": [{"company_id": company.id, "percent": "100"}]},
            headers=headers,
        )
        wipe(db_session)
        client.put(f"/api/employees/{employee.id}/company-shares",
                   json={"shares": []}, headers=headers)

        rows = changes(db_session, entity_type="employee_shares")
        assert len(rows) == 1
        # Снятие показываем словами: пустая ячейка читалась бы как «нет данных».
        assert rows[0].new_value == "не задано"

    def test_department_default_shares_are_recorded(self, client, db_session, department,
                                                    company, company2, admin_user):
        wipe(db_session)
        headers = self._admin_headers(client)
        resp = client.put(
            f"/api/departments/{department.id}/company-shares",
            json={"shares": [
                {"company_id": company.id, "percent": "70"},
                {"company_id": company2.id, "percent": "30"},
            ]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

        rows = changes(db_session, entity_type="department_shares")
        assert len(rows) == 1
        assert rows[0].old_value == "не задано"
        assert "70" in rows[0].new_value and "30" in rows[0].new_value
        assert rows[0].entity_label == department.name

    def test_shares_of_moonlighter_name_the_position(self, client, db_session, employee,
                                                     department2, company, admin_user):
        """У совместителя в записи должно быть видно, какому рабочему месту
        задано распределение."""
        second = EmployeePosition(
            employee_id=employee.id, title="Электрик", department_id=department2.id,
            is_primary=False,
        )
        db_session.add(second)
        db_session.commit()
        second_id = second.id
        wipe(db_session)

        # position_id задаётся В ТЕЛЕ запроса — распределение принадлежит
        # конкретному рабочему месту (task_positions ч.A).
        resp = client.put(
            f"/api/employees/{employee.id}/company-shares",
            json={"position_id": second_id,
                  "shares": [{"company_id": company.id, "percent": "100"}]},
            headers=self._admin_headers(client),
        )
        assert resp.status_code == 200, resp.text

        rows = changes(db_session, entity_type="employee_shares")
        assert len(rows) == 1
        assert rows[0].entity_id == second_id
        assert "Электрик" in rows[0].entity_label
