"""Вычистка денежных полей из ответов API (task_timekeeper_role).

Табельщик ведёт табель своих отделов, но зарплату видеть не должен. Отказать
эндпойнтам расчёта (`/payroll`, `/statement`, премии, займ) мало: оклад, ставки,
коэффициенты и займ лежат в карточке сотрудника и его позициях, а те приходят
вместе с самим табелем. Поэтому финансы снимаются с уже собранных схем ответа —
на УРОВНЕ API, а не в UI: прямой запрос табельщика тоже вернёт `null`.

Поля перечислены явно (а не «всё, что Decimal»): новое денежное поле должно
осознанно попадать в список, иначе оно молча утечёт. Неденежное — должность,
отдел, график, тип оплаты, признак ночных смен — остаётся: без графика и типа
оплаты табель не построить, а «сколько это стоит» из них не следует.
"""
from __future__ import annotations

from app.schemas.employee import EmployeeRead
from app.schemas.position import EmployeePositionRead

# Денежные поля позиции: база оплаты, коэффициенты надбавок, ночная ставка.
FINANCIAL_POSITION_FIELDS = (
    "rate",
    "shift_rate",
    "hour_rate",
    "weekend_coefficient",
    "weekend_fixed_rate",
    "holiday_coefficient",
    "holiday_fixed_rate",
    "overtime_coefficient",
    "night_rate",
)

# То же в «плоской» карточке сотрудника (это compat-вид основной позиции) плюс займ.
# Ночная ставка сюда не попадает: в карточке её нет, она только у позиции —
# лишние имена отсеет `_blank`, но перечислять несуществующее незачем.
FINANCIAL_EMPLOYEE_FIELDS = tuple(
    f for f in FINANCIAL_POSITION_FIELDS if f != "night_rate"
) + (
    "loan_amount",
    "loan_term_months",
    "loan_start_date",
)


def _blank(model, fields: tuple[str, ...]):
    """Копия схемы с обнулёнными полями. Копия, а не правка на месте: тот же
    объект может уйти в другой ответ, а модели Pydantic здесь строятся из ORM.

    Поля, которых у схемы нет, пропускаем: `model_copy` их не валидирует и молча
    завёл бы лишний атрибут вместо того, чтобы что-то скрыть.
    """
    update = {f: None for f in fields if f in type(model).model_fields}
    return model.model_copy(update=update)


def mask_position(position: EmployeePositionRead) -> EmployeePositionRead:
    return _blank(position, FINANCIAL_POSITION_FIELDS)


def mask_employee(employee: EmployeeRead) -> EmployeeRead:
    masked = _blank(employee, FINANCIAL_EMPLOYEE_FIELDS)
    masked.positions = [mask_position(p) for p in employee.positions]
    return masked


def mask_employees(employees: list[EmployeeRead]) -> list[EmployeeRead]:
    return [mask_employee(e) for e in employees]


def mask_positions_by_employee(
    positions_by_employee: dict[int, list[EmployeePositionRead]],
) -> dict[int, list[EmployeePositionRead]]:
    return {
        emp_id: [mask_position(p) for p in positions]
        for emp_id, positions in positions_by_employee.items()
    }
