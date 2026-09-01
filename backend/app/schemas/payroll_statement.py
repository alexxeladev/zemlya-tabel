from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CompanyShareInput(BaseModel):
    company_id: int
    percent: Decimal


class EmployeeSharesRead(BaseModel):
    """Проценты распределения по умолчанию из карточки сотрудника.

    Дополнительно отдаётся дефолт отдела — чтобы в карточке было видно, что
    при пустом собственном распределении наследуется отдел (ч.3 каскада).
    """
    employee_id: int
    # Рабочее место, к которому относятся проценты (task_positions ч.A).
    position_id: int | None = None
    shares: list[CompanyShareInput]
    percent_sum: Decimal
    department_id: int | None = None
    department_name: str | None = None
    department_shares: list[CompanyShareInput] = []
    # Своё распределение не задано, а у отдела есть → сотрудник наследует отдел
    inherits_department: bool = False


class EmployeeSharesUpdate(BaseModel):
    # Какому рабочему месту задаются проценты (task_positions ч.A);
    # не указано — основному.
    position_id: int | None = None
    shares: list[CompanyShareInput]


class DistributionOverrideInput(BaseModel):
    """Переопределение распределения на конкретный месяц (правка в ведомости)."""
    employee_id: int
    # Рабочее место, чьё распределение правим; не указано — основное.
    position_id: int | None = None
    year: int
    month: int
    shares: list[CompanyShareInput]


class StatementCompanyRef(BaseModel):
    id: int
    code: str
    name: str
    # Короткое имя для заголовка колонки (ч.2): код в шапке ведомости человеку
    # ничего не говорит. Полное имя остаётся в `name` — оно идёт в подсказку.
    display_name: str = ""
    sort_order: int = 0


class StatementCompanyAmount(BaseModel):
    company_id: int
    # percent — процент, ЗАДАННЫЙ каскадом (его и правят в ведомости).
    percent: Decimal
    # effective_percent — ФАКТИЧЕСКАЯ доля юрлица в базе распределения
    # («К выплате»). Она отличается от заданной и из-за целевых премий
    # (task_funding_source: каскад 50/50 плюс целевая даёт 40/60), и из-за
    # округления долей до тысячи. Показывать надо именно её, а править —
    # percent, иначе правка молча учла бы целевую сумму дважды.
    effective_percent: Decimal = Decimal("0")
    amount: Decimal


class StatementRow(BaseModel):
    employee_id: int
    # Строка ведомости = ПОЗИЦИЯ (task_positions ч.A). У совместителя строк
    # столько, сколько рабочих мест, employee_id при этом повторяется, а
    # «к выплате» между ними не суммируется — платят разные компании.
    position_id: int | None = None
    is_primary_position: bool = True
    tab_number: str | None
    employee_name: str
    main_company_id: int | None
    main_company_name: str | None
    department_name: str | None
    position: str | None
    schedule_name: str | None

    # rate — оклад окладника; у посменного УСЛОВНЫЙ оклад (ставка × норма смен);
    # у почасовика оклада нет. Что перед нами, видно по pay_type.
    rate: Decimal | None
    pay_type: str = "salary"
    shift_rate: Decimal | None = None
    hour_rate: Decimal | None = None
    worked_shifts: int = 0
    norm_shifts: int | None = None
    base_shifts: int = 0
    norm_hours: Decimal | None
    fact_hours: Decimal
    # Плановые и фактические ДНИ (смены) месяца — в деньгах не участвуют, но в
    # ведомости стоят рядом с часами: по ним видно, из чего сложился факт.
    norm_days: int | None = None
    fact_days: int = 0
    overtime_coefficient: Decimal
    overtime_hours: Decimal
    overtime_amount: Decimal

    # Начислено оклад (включает оплату часов вне графика и праздничных)
    base_salary: Decimal
    premium_amount: Decimal       # Премия базовая
    kpi_amount: Decimal           # KPI
    premium_extra_amount: Decimal  # Премия (доп.) — пока не моделируется, плейсхолдер

    # Обоснования (task_ux_improvements ч.1b). Каждая премия/KPI/аванс заводится
    # с обязательным текстом, за месяц их может быть несколько — поэтому списки
    # строк вида «5000 ₽ — за переработку в мае», а не одно поле.
    # loan_note заполняется только у РУЧНОЙ правки удержания по займу: своего
    # поля обоснования у займа в модели нет, есть лишь плановая доля.
    premium_reasons: list[str] = []
    kpi_reasons: list[str] = []
    advance_reasons: list[str] = []
    loan_note: str | None = None

    # Отсутствия: дни и оплата (ДО/Н — только дни, денег не дают)
    vacation_days: int = 0
    sick_days: int = 0
    unpaid_days: int = 0
    absent_days: int = 0
    vacation_amount: Decimal = Decimal("0")
    sick_amount: Decimal = Decimal("0")
    # Годовой лимит больничного (часть 2)
    sick_limit_days: int = 0
    sick_unpaid_days: int = 0
    sick_limit_remaining: int = 0

    # Надбавка за ночные смены (task_night_shifts_rework): число смен × ставка
    # (фонд отдела / календарные дни месяца). Входит в «Итого начислено».
    night_shifts: int = 0
    night_rate: Decimal | None = None
    night_amount: Decimal = Decimal("0")

    # Итого начислено = оклад + переработка + отпускные + больничные + премии + KPI
    #                   + надбавка за ночные
    accrued_total: Decimal
    deductions: Decimal           # Аванс/Удержано (займ + аванс)
    # К выплате — округлено до ближайшей 1000 ₽. Оно же БАЗА распределения по
    # юрлицам (task_it_arm_distribution ч.2): Σ distribution == net_payout.
    net_payout: Decimal
    net_payout_exact: Decimal = Decimal("0")  # до округления (справочно)
    rounding_tail: Decimal = Decimal("0")     # хвост = точное − округлённое

    is_overridden: bool           # проценты распределения переопределены на месяц
    is_auto_distributed: bool     # распределено авто по фактическим часам (ручной % не задан)
    # Уровень каскада, откуда взято распределение:
    # month (правка на месяц) > employee (карточка) > department (отдел) > hours (авто).
    # Отдельно от каскада — quantity: отдел с флагом «распределение по
    # количественному показателю» (заявки у HR, АРМ у ИТ) делится по нему.
    distribution_source: str
    # Подпись количественного показателя отдела («Заявки», «АРМ»), когда строка
    # относится к отделу, делящемуся по нему, и показатель за месяц задан
    # (task_hr_applications → task_it_arm_distribution). Для интерфейса это
    # признак «ручная правка процентов в ведомости заблокирована»: он стоит и у
    # строк, ушедших на распределение из карточки (task_card_priority) —
    # исключения задаются только в карточке, а не правкой на месяц.
    quantity_metric_name: str | None = None
    # Пояснение к распределению для человека. Заполняется, когда отдел помечен
    # «по количественному показателю», но показателя за месяц нет: молча
    # посчитать «как у всех» нельзя.
    distribution_note: str | None = None
    # Целевые премии/KPI (task_funding_source): {company_id: сумма}, их итог и
    # человеческая пометка «включает целевую премию 20 000 ₽ (Секьюрити)».
    # Каскад применяется к БАЗЕ РАСПРЕДЕЛЕНИЯ («К выплате») МИНУС
    # targeted_total, поэтому Σ распределения всегда равна «К выплате».
    targeted_amounts: dict[int, Decimal] = {}
    targeted_total: Decimal = Decimal("0")
    targeted_note: str | None = None
    percent_sum: Decimal          # сумма процентов (для подсветки ≠ 100)
    distribution: list[StatementCompanyAmount]
    distribution_total: Decimal

    is_calculable: bool
    note: str | None


class PayrollStatementRead(BaseModel):
    year: int
    month: int
    companies: list[StatementCompanyRef]
    rows: list[StatementRow]

    # Шапка выгрузки (task_vedomost_format ч.3): для какого юрлица и какого
    # подразделения сформирована ведомость. Считаются при сборке — в Excel
    # ходить в БД за ними нельзя, экспортёр работает только со схемой.
    organization: str = ""
    subdivision: str = ""

    total_overtime_amount: Decimal
    total_base_salary: Decimal
    total_vacation_amount: Decimal = Decimal("0")
    total_sick_amount: Decimal = Decimal("0")
    total_night_amount: Decimal = Decimal("0")
    total_premium: Decimal
    total_kpi: Decimal
    total_accrued: Decimal
    total_deductions: Decimal
    total_net_payout: Decimal  # Σ округлённых выплат (не округление суммы)
    total_net_payout_exact: Decimal = Decimal("0")
    total_rounding_tail: Decimal = Decimal("0")
    # Итог распределения по каждой компании: {company_id: amount}
    distribution_totals: dict[int, Decimal]
