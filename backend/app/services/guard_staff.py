"""
Штат охраны ведётся из модуля вахты (task_guard_ownership).

**Владение данными без отделения.** Таблица сотрудников одна, отдельной для
охраны нет: ведомость собирается одним запросом, табельная нумерация сквозная.
Разделено только ВЛАДЕНИЕ, и оно на уровне РАБОЧЕГО МЕСТА, а не человека:

* человек (ФИО, таб. №, доступ в систему, даты работы в компании) — общий
  справочник;
* позиция в охранном подразделении — только вахта;
* позиция в обычном подразделении — общий справочник.

Совместитель поэтому правится в двух местах: ФИО и обычная позиция — в
справочнике, охранная — в вахте.

**Единственное место, решающее «охранная позиция или нет», —
`is_guard_position` / `is_guard_department_id`.** Роутер сотрудников, импорт,
отдел и экран вахты спрашивают только их; своей проверки флага отдела ни у кого
быть не должно, иначе одна из точек входа однажды разойдётся с остальными.

Запрет стоит на бэке: общий справочник получает `GuardOwnedError` (→ 403), а не
спрятанную кнопку.

Перевод меняет владение. Кто владеет позицией ДО правки, тот её и переводит:
обычную позицию в охрану переводят в справочнике (ничего не требуется — лишние
поля просто перестают учитываться), охранную в обычный отдел — в вахте. Перевод
из охраны НЕ требует дозаполнения, а предупреждает: позиция без графика и базы
оплаты не войдёт в расчёт, и причины показываются теми же словами, что пишет
расчёт (`payroll.position_setup_issues`).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session, selectinload

from app.models.departments import Department
from app.models.employees import Employee
from app.models.guard_posts import (
    GUARD_KIND_CHIEF,
    GUARD_KIND_GUARD,
    GUARD_KIND_LABELS,
    GUARD_KINDS,
    pay_type_for_kind,
)
from app.models.positions import (
    PAY_TYPE_BASE_FIELD,
    PAY_TYPE_SALARY,
    EmployeePosition,
)
from app.services.employees import normalize_tab_number, tab_number_conflict
from app.services.guard_duty import GuardError, next_tab_number
from app.services.org_access import can_access_department
from app.services.payroll import position_setup_issues

#: Текст отказа общего справочника. Фронт показывает его как есть и ведёт в вахту.
GUARD_OWNED_MESSAGE = (
    "Рабочее место охранного подразделения ведётся в модуле «Вахта» — "
    "изменить его можно только там"
)


class GuardOwnedError(Exception):
    """Попытка изменить охранную позицию не через вахту."""

    def __init__(self, message: str = GUARD_OWNED_MESSAGE):
        super().__init__(message)


class GuardStaffError(GuardError):
    """Ошибка формы сотрудника охраны; роутер переводит в 422, как любую
    ошибку вахты (быстрый найм ловит её как `GuardError`)."""


# ── Единственный предикат «охранное или нет» ──────────────────────────────────

def is_guard_department(dept: Department | None) -> bool:
    """Подразделение охранное — его штат ведёт вахта. Нет отдела — не охранное.

    Неактивный отдел с флагом тоже охранный: владение не должно молча переехать
    в справочник оттого, что отдел закрыли.
    """
    return bool(dept is not None and dept.is_guard_department)


def is_guard_department_id(db: Session, department_id: int | None) -> bool:
    """То же по id отдела."""
    if department_id is None:
        return False
    return is_guard_department(db.get(Department, department_id))


def is_guard_position(db: Session, position: EmployeePosition | None) -> bool:
    """Рабочее место принадлежит охранному подразделению — его ведёт вахта.

    Смотрим на `department_id`, а не на загруженный `position.department`:
    отношение может быть ещё не обновлено после присвоения id.
    """
    return position is not None and is_guard_department_id(db, position.department_id)


# ── Запрет правки из общего справочника ───────────────────────────────────────

def _differs(old, new) -> bool:
    """Реальное расхождение значений: форма присылает поля целиком, и одно
    лишь присутствие поля в запросе правкой не является."""
    if isinstance(old, Decimal) or isinstance(new, Decimal):
        if old is None or new is None:
            return old is not new
        return Decimal(str(old)) != Decimal(str(new))
    return old != new


def ensure_position_edit_allowed(
    db: Session, position: EmployeePosition | None, changes: dict
) -> None:
    """Правка позиции из общего справочника: охранную менять нельзя.

    `changes` — имя поля ПОЗИЦИИ → новое значение. Отказ только при реальном
    расхождении: сохранение карточки совместителя присылает и поля охранной
    основной позиции без изменений, и это не правка.
    """
    if not is_guard_position(db, position):
        return
    for field_name, value in changes.items():
        if _differs(getattr(position, field_name), value):
            raise GuardOwnedError()


def ensure_position_create_allowed(db: Session, department_id: int | None) -> None:
    """Новое рабочее место в охранном подразделении заводится только в вахте."""
    if is_guard_department_id(db, department_id):
        raise GuardOwnedError(
            "Сотрудников охранного подразделения оформляют в модуле «Вахта» — "
            "там же заводится их рабочее место"
        )


def ensure_position_owned_outside_vahta(
    db: Session, position: EmployeePosition | None
) -> None:
    """Действие над охранной позицией целиком (удаление, распределение) — отказ."""
    if is_guard_position(db, position):
        raise GuardOwnedError()


# ── Начисления основной системы на охранной позиции (аудит 2-Г) ───────────────

class GuardAccrualError(GuardOwnedError):
    """Часы, премия/KPI/аванс или заём адресованы охранной позиции.

    Позицию охранного подразделения считает модуль вахты: часов в табеле у
    охранника нет, премия, штраф и официальная выплата у вахты свои. Общий ввод
    для неё раньше принимался и молча игнорировался расчётом — а в месяц без
    назначения на пост вдруг учитывался. Теперь он отклоняется на входе.

    Наследник `GuardOwnedError`: это то же правило «охранное ведёт вахта», и
    роутеры отвечают тем же 403.
    """


_GUARD_ACCRUAL_MESSAGE = (
    "{what} для рабочего места охранного подразделения не {verb}: его смены, "
    "премии, штрафы и выплаты ведутся в модуле «Вахта»"
)


def ensure_no_guard_accrual(
    db: Session, position: EmployeePosition | None, what: str, verb: str = "вводятся"
) -> None:
    """ЕДИНСТВЕННОЕ место запрета общего ввода на охранную позицию.

    Зовут все точки входа: часы (ячейка, батч, перенос юрлица), премии/KPI/аванс,
    заём (карточка и ручная правка удержания). Автозаполнение такие позиции
    пропускает с причиной. СНЯТИЕ (часы в 0, удаление премии, очистка займа)
    сюда не приходит — оно разрешено всегда, иначе введённое до запрета было бы
    нечем убрать.
    """
    if is_guard_position(db, position):
        raise GuardAccrualError(_GUARD_ACCRUAL_MESSAGE.format(what=what, verb=verb))


GUARD_AUTOFILL_SKIP_REASON = (
    "Охранное рабочее место — смены ведутся в модуле «Вахта», табель не заполняется"
)

LOAN_FIELDS = ("loan_amount", "loan_term_months", "loan_start_date")


def loan_position(employee: Employee) -> EmployeePosition | None:
    """Рабочее место, с которого удерживается заём: `loan_position_id`, а у займов
    без него — основная (так же читает расчёт, `_loan_belongs_to`)."""
    if employee.loan_position_id is not None:
        return employee.position_by_id(employee.loan_position_id)
    return employee.primary_position


def ensure_loan_change_allowed(db: Session, employee: Employee, changes: dict) -> None:
    """Правка займа в карточке: на охранной позиции заём не заводится и не меняется.

    Отказ только при РЕАЛЬНОМ расхождении (форма шлёт поля целиком) и только когда
    заём остаётся заданным: очистка полей разрешена — это и есть способ убрать
    заём, заведённый до запрета.
    """
    touched = {f: v for f, v in changes.items() if f in LOAN_FIELDS}
    if not any(_differs(getattr(employee, f), v) for f, v in touched.items()):
        return
    if all(v is None for v in touched.values()) and set(touched) == set(LOAN_FIELDS):
        return
    ensure_no_guard_accrual(db, loan_position(employee), "Заём", "заводится")


# ── Должность ↔ тип оплаты ────────────────────────────────────────────────────

def guard_kind_of_position(position: EmployeePosition) -> str:
    """Должность охранного рабочего места.

    Отдельного поля у позиции нет (новых колонок задача не заводит): должность
    читается из названия, как её пишет вахта, а неизвестное название — по типу
    оплаты (оклад → начальник, иначе охранник).
    """
    for kind, label in GUARD_KIND_LABELS.items():
        if (position.title or "").strip().lower() == label.lower():
            return kind
    return GUARD_KIND_CHIEF if position.pay_type == PAY_TYPE_SALARY else GUARD_KIND_GUARD


def apply_guard_kind(position: EmployeePosition, kind: str, amount: Decimal | None) -> None:
    """Должность, тип оплаты от неё и сумма в поле базы этого типа.

    Базы чужих типов гасятся — как везде в системе, иначе расчёт возьмёт не то.
    """
    if kind not in GUARD_KINDS:
        raise GuardStaffError(f"Неизвестная должность «{kind}»")
    position.title = GUARD_KIND_LABELS[kind]
    position.pay_type = pay_type_for_kind(kind)
    for pay_type, base_field in PAY_TYPE_BASE_FIELD.items():
        setattr(position, base_field, amount if pay_type == position.pay_type else None)


def amount_of(position: EmployeePosition) -> Decimal | None:
    """Сумма рабочего места: оклад у окладного, ставка за смену у посменного."""
    return getattr(position, PAY_TYPE_BASE_FIELD[position.pay_type])


# ── Экран «Сотрудники охраны» ─────────────────────────────────────────────────

def list_staff_positions(db: Session, department_ids: list[int]) -> list[EmployeePosition]:
    """Активные рабочие места доступных охранных подразделений.

    Строка на РАБОЧЕЕ МЕСТО: у совместителя по двум постам их две, и сумма у
    каждой своя. Уволенные люди остаются в списке — у них видны даты.
    """
    if not department_ids:
        return []
    return (
        db.query(EmployeePosition)
        .options(selectinload(EmployeePosition.employee))
        .join(Employee, Employee.id == EmployeePosition.employee_id)
        .filter(
            EmployeePosition.department_id.in_(department_ids),
            EmployeePosition.is_active == True,  # noqa: E712
            Employee.is_system_admin == False,  # noqa: E712
        )
        .order_by(Employee.is_active.desc(), Employee.full_name, EmployeePosition.id)
        .all()
    )


def _check_dates(hire: datetime.date | None, dismissal: datetime.date | None) -> None:
    if hire is not None and dismissal is not None and dismissal < hire:
        raise GuardStaffError("Дата увольнения раньше даты приёма")


def _check_amount(amount: Decimal | None) -> None:
    if amount is not None and amount < 0:
        raise GuardStaffError("Сумма не может быть отрицательной")


def create_staff(
    db: Session,
    *,
    full_name: str,
    tab_number: str | None,
    department_id: int,
    kind: str,
    amount: Decimal | None,
    hire_date: datetime.date | None,
    dismissal_date: datetime.date | None,
) -> tuple[Employee, EmployeePosition]:
    """Новый человек с рабочим местом в охране. Коммит снаружи.

    Табельный номер — из ОБЩЕЙ нумерации (`next_tab_number` вахты), вручную —
    с той же проверкой занятости, что в справочнике. Даты — у ПОЗИЦИИ: даты
    человека ведёт общий справочник.
    """
    full_name = (full_name or "").strip()
    if len(full_name) < 3:
        raise GuardStaffError("Укажите ФИО")
    if not is_guard_department_id(db, department_id):
        raise GuardStaffError("Выберите подразделение охраны")
    _check_dates(hire_date, dismissal_date)
    _check_amount(amount)

    number = normalize_tab_number(tab_number)
    if number is None:
        number = next_tab_number(db)
    conflict = tab_number_conflict(db, number)
    if conflict is not None:
        raise GuardStaffError(conflict)

    employee = Employee(
        full_name=full_name,
        tab_number=number,
        position=GUARD_KIND_LABELS.get(kind),
        is_active=True,
    )
    db.add(employee)
    db.flush()
    position = employee.ensure_primary_position()
    position.department_id = department_id
    apply_guard_kind(position, kind, amount)
    position.hire_date = hire_date
    position.dismissal_date = dismissal_date
    db.flush()
    return employee, position


@dataclass
class TransferOutWarning:
    """Перевод охранной позиции в обычный отдел ждёт подтверждения."""

    department_name: str
    issues: list[str] = field(default_factory=list)


def update_staff(
    db: Session,
    actor: Employee,
    position: EmployeePosition,
    data: dict,
    confirm: bool,
) -> TransferOutWarning | None:
    """Правка охранного рабочего места из вахты. Коммит снаружи.

    `data` — только присланные поля: department_id, kind, amount, hire_date,
    dismissal_date. ФИО и таб. № здесь не правятся — это поля ЧЕЛОВЕКА, их ведёт
    общий справочник.

    Отдел вне охраны — перевод. Без `confirm` ничего не пишется, возвращается
    предупреждение с причинами, по которым место не войдёт в расчёт; с
    `confirm` перевод сохраняется, и дальше позицию ведёт справочник.
    """
    if not is_guard_position(db, position):
        raise GuardStaffError(
            "Рабочее место не в охранном подразделении — оно ведётся в общем справочнике"
        )

    kind = data.get("kind", guard_kind_of_position(position))
    amount = data["amount"] if "amount" in data else amount_of(position)
    hire = data["hire_date"] if "hire_date" in data else position.hire_date
    dismissal = data["dismissal_date"] if "dismissal_date" in data else position.dismissal_date
    _check_dates(hire, dismissal)
    _check_amount(amount)

    warning: TransferOutWarning | None = None
    target = data.get("department_id", position.department_id)
    if target != position.department_id:
        dept = db.get(Department, target) if target is not None else None
        if dept is None or not dept.is_active:
            raise GuardStaffError("Подразделение не найдено")
        if not can_access_department(actor, dept.id):
            raise GuardStaffError("Нет доступа к этому подразделению")
        if not is_guard_department(dept):
            warning = TransferOutWarning(department_name=dept.name)

    if "kind" in data or "amount" in data:
        apply_guard_kind(position, kind, amount)
    position.hire_date = hire
    position.dismissal_date = dismissal
    position.department_id = target

    if warning is not None:
        # Причины — по карточке ПОСЛЕ правки: ровно то, что увидит расчёт.
        db.flush()
        db.refresh(position)
        warning.issues = position_setup_issues(position)
        if confirm:
            return None
    return warning


# ── Снятие флага охраны у отдела ──────────────────────────────────────────────

@dataclass
class GuardFlagRemovalReport:
    """Что случится со штатом, если снять флаг охраны у отдела."""

    position_count: int
    not_calculable_count: int
    issues: list[str]


def guard_flag_removal_report(db: Session, department_id: int) -> GuardFlagRemovalReport:
    """Сколько рабочих мест перестанет вестись в вахте и сколько из них не
    войдёт в обычный расчёт. Снятие флага не запрещается — отдел могли завести
    охранным по ошибке, — но делается осознанно."""
    positions = (
        db.query(EmployeePosition)
        .join(Employee, Employee.id == EmployeePosition.employee_id)
        .filter(
            EmployeePosition.department_id == department_id,
            EmployeePosition.is_active == True,  # noqa: E712
            Employee.is_system_admin == False,  # noqa: E712
        )
        .all()
    )
    issues: list[str] = []
    not_calculable = 0
    for position in positions:
        found = position_setup_issues(position)
        if found:
            not_calculable += 1
        for issue in found:
            if issue not in issues:
                issues.append(issue)
    return GuardFlagRemovalReport(
        position_count=len(positions),
        not_calculable_count=not_calculable,
        issues=issues,
    )
