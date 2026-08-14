from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from app.models.employee_absences import ABSENCE_CODES, AbsenceKind
from app.models.employees import Employee
from app.models.positions import (
    PAY_TYPE_HOURLY,
    PAY_TYPE_PER_SHIFT,
    PAY_TYPE_SALARY,
    EmployeePosition,
)
from app.models.timesheet_entries import TimesheetEntry
from app.services.absences import (
    is_payable_absence_day,
    sick_limit_days,
    split_sick_dates_by_limit,
)
from app.services.calendar import workdays_in_month
from app.services.work_schedule import (
    DAY_HOLIDAY,
    DAY_OFF_SCHEDULE,
    DAY_PLANNED,
    day_category,
    is_cyclic_schedule,
    norm_days_for_schedule,
    norm_hours_for_schedule,
    schedule_issue,
    shift_hours_for_date,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_ONE_HALF = Decimal("1.5")
_HUNDRED = Decimal("100")
_PERCENT_Q = Decimal("0.1")

# Длина смены для дня отсутствия, когда графика нет: стандартные 8 ч.
# При заданном графике берётся его `hours_per_shift` — см. `absence_day_hours`.
ABSENCE_DAY_HOURS = Decimal("8")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_ONE, rounding=ROUND_HALF_EVEN)


def _distribute_whole_rubles(
    total: Decimal, weights: dict[int, Decimal]
) -> dict[int, Decimal]:
    """
    Распределяет целую сумму total по ключам пропорционально weights так,
    чтобы сумма частей была РОВНО равна total (метод наибольших остатков).
    Независимое округление долей даёт расхождение с итогом до ±N/2 руб. —
    для разнесения по юрлицам это недопустимо.
    """
    weight_sum = sum(weights.values(), _ZERO)
    if weight_sum <= _ZERO or total == _ZERO:
        return {key: _ZERO for key in weights}

    parts: dict[int, Decimal] = {}
    remainders: dict[int, Decimal] = {}
    for key, w in weights.items():
        exact = total * w / weight_sum
        floor = exact.to_integral_value(rounding=ROUND_FLOOR)
        parts[key] = floor
        remainders[key] = exact - floor

    leftover = int(total - sum(parts.values(), _ZERO))
    # Остаток (0..N-1 руб.) — по рублю ключам с наибольшими остатками;
    # при равенстве — детерминированно по company_id.
    for key in sorted(weights, key=lambda k: (-remainders[k], k))[:leftover]:
        parts[key] += _ONE
    return parts


def _extra_pay(
    hours: Decimal,
    units: Decimal,
    unit_rate: Decimal,
    pay_type: str | None,
    coefficient: Decimal | None,
    fixed_rate: Decimal | None,
    default_coefficient: Decimal,
) -> Decimal:
    """
    Оплата повышенной категории (вне графика / праздничных) по настройкам
    конкретной позиции:
      - coefficient: `units × unit_rate × коэффициент` (0 = не оплачивается).
        Единица зависит от типа оплаты: у окладной это ЧАС (units = часы,
        unit_rate = часовая ставка), у посменной — целая СМЕНА (units = смены,
        unit_rate = ставка за смену): смена в выходной стоит ставку ×
        коэффициент независимо от того, сколько часов она длилась
        (task_shiftpay_addons).
      - fixed_rate: `часы × фикс_ставка`. Фикс-ставка задаётся ЗА ЧАС и от
        оклада/ставки смены не зависит — считается по часам при любом типе.
    """
    if hours <= _ZERO:
        return _ZERO

    if (pay_type or "coefficient") == "fixed_rate":
        if fixed_rate is None:
            return _ZERO
        return hours * Decimal(str(fixed_rate))

    if units <= _ZERO:
        return _ZERO
    coeff = default_coefficient if coefficient is None else Decimal(str(coefficient))
    return units * unit_rate * coeff


def _off_schedule_pay(
    position, hours: Decimal, units: Decimal, unit_rate: Decimal
) -> Decimal:
    """
    Оплата ВНЕ ГРАФИКА — выход в свой законный выходной по графику
    (правка 3.9-3, триггер уточнён в task_schedule_based_pay). Дефолт 1.5.
    Окладной/почасовой передают часы и часовую ставку, посменной — смены и
    ставку за смену.
    """
    return _extra_pay(
        hours, units, unit_rate,
        getattr(position, "weekend_pay_type", None),
        getattr(position, "weekend_coefficient", None),
        getattr(position, "weekend_fixed_rate", None),
        _ONE_HALF,
    )


def _holiday_pay(
    position, hours: Decimal, units: Decimal, unit_rate: Decimal
) -> Decimal:
    """
    Оплата ПРАЗДНИЧНЫХ — работа в нерабочий праздничный день календаря.
    Настройка отдельная от выходных (задаётся в позиции), дефолт — 1.5.
    """
    return _extra_pay(
        hours, units, unit_rate,
        getattr(position, "holiday_pay_type", None),
        getattr(position, "holiday_coefficient", None),
        getattr(position, "holiday_fixed_rate", None),
        _ONE_HALF,
    )


def is_per_shift(position) -> bool:
    """
    Посменная оплата: оклада нет, база = смен плановых дней графика × ставка.
    Смены в выходной/праздник и часы сверх смены оплачиваются сверх базы
    (task_shiftpay_addons).
    """
    return getattr(position, "pay_type", PAY_TYPE_SALARY) == PAY_TYPE_PER_SHIFT


def is_hourly(position) -> bool:
    """Почасовая оплата: оклада нет, база = отработанные часы × ставка за час."""
    return getattr(position, "pay_type", PAY_TYPE_SALARY) == PAY_TYPE_HOURLY


def notional_salary(position, norm_shifts: int | None) -> Decimal | None:
    """
    «Условный месячный оклад» посменного = ставка_за_смену × норма_смен_месяца.

    Нужен там, где формула завязана на оклад, а его нет: отпускные и
    больничные считаются от него по той же формуле «оклад / норма_часов ×
    дни × 8». Отдельным полем НЕ хранится — норма смен меняется от месяца
    к месяцу, поэтому считается на лету.
    """
    shift_rate = getattr(position, "shift_rate", None)
    if shift_rate is None or not norm_shifts:
        return None
    return Decimal(str(shift_rate)) * Decimal(norm_shifts)


def per_shift_hourly_rate(position, schedule) -> Decimal | None:
    """
    Часовая ставка ПОСМЕННОЙ позиции = `ставка_за_смену / часы_смены`
    (task_shiftpay_addons). Нужна для переработки: часы сверх дневной нормы
    оплачиваются по часам, а не сменами.

    Часы смены — `hours_per_shift` графика позиции (6/1 → 9, 2/2 → 12), БЕЗ
    сокращения предпраздничного дня: это ставка сотрудника, а не свойство
    конкретной даты. Не путать с `EmployeePayroll.hourly_rate` — там условный
    оклад / норма часов, от которого считаются отпускные и больничные.

    None — ставка за смену или график не заданы (такая позиция и так не
    считается: `schedule_issue` / «Не задана ставка за смену»).
    """
    shift_rate = getattr(position, "shift_rate", None)
    hours = getattr(schedule, "hours_per_shift", None) if schedule is not None else None
    if shift_rate is None or not hours:
        return None
    return Decimal(str(shift_rate)) / Decimal(str(hours))


def _overtime_coeff(position) -> Decimal:
    """Коэффициент переработки позиции (0/1/1.5), дефолт 1.5 (задача 3.11b п.0)."""
    coeff = getattr(position, "overtime_coefficient", None)
    return _ONE_HALF if coeff is None else Decimal(str(coeff))


def daily_norm_hours(
    schedule, work_date: date, calendar_data: dict | None
) -> Decimal:
    """
    Дневная норма сотрудника на конкретную дату — от ГРАФИКА
    (task_schedule_based_pay):
      - плановый рабочий день → длительность смены;
      - сокращённый (предпраздничный) рабочий день → смена − 1 (только у
        weekday-графиков: у сменщика смена 12 ч независимо от календаря);
      - выходной по графику и праздник → 0: часы таких дней целиком уходят
        в свои категории («вне графика» / «праздничные») и в переработку
        не превращаются.
    """
    if schedule is None:
        return _ZERO
    if day_category(schedule, work_date, calendar_data) != DAY_PLANNED:
        return _ZERO
    return shift_hours_for_date(schedule, work_date, calendar_data)


def daily_overtime_hours(
    schedule,
    hours_by_date: dict[date, Decimal],
    calendar_data: dict | None,
) -> Decimal:
    """
    Переработка ПО ДНЯМ: для каждого дня max(0, факт − дневная норма), сумма за месяц.
    Часы всех компаний в дне уже должны быть просуммированы в hours_by_date.
    Недоработка одного дня НЕ компенсирует переработку другого.
    Дни вне графика сюда не попадают — там дневная норма 0 и часы учитываются
    отдельной категорией «вне графика» (см. вызывающий код).
    """
    overtime = _ZERO
    for work_date, hours in hours_by_date.items():
        norm = daily_norm_hours(schedule, work_date, calendar_data)
        if norm > _ZERO and hours > norm:
            overtime += hours - norm
    return overtime


def hourly_overtime_hours(
    schedule,
    hours_by_date: dict[date, Decimal],
    calendar_data: dict | None,
) -> Decimal:
    """
    Переработка ПОЧАСОВИКА — по дням, как у окладника (task_positions_fixes п.2):
    для каждого дня max(0, факт − длительность смены), сумма за месяц.

    Отличие от `daily_overtime_hours`: у почасовика нет категорий «вне графика»
    и «праздничные» (каждый час уже оплачен по ставке), поэтому дневная норма
    равна смене по графику в ЛЮБОЙ день с часами, а не только в плановый —
    иначе выход в свой выходной целиком превратился бы в переработку ×1.5.
    """
    overtime = _ZERO
    for work_date, hours in hours_by_date.items():
        norm = shift_hours_for_date(schedule, work_date, calendar_data)
        if norm > _ZERO and hours > norm:
            overtime += hours - norm
    return overtime


def absence_day_hours(schedule) -> Decimal:
    """
    Сколько часов «стоит» один оплачиваемый день отсутствия — ДЛИНА СМЕНЫ
    ГРАФИКА (task_vacation_shift_fix): 5/2 → 8 ч, 6/1 → 9 ч, 2/2 и 3/1 → 12 ч.

    Раньше здесь было жёсткое «× 8», из-за чего отпуск сменщика на 12-часовом
    графике оплачивался в полтора раза дешевле смены. Сокращение
    предпраздничного дня к отсутствию не применяем: это ставка сотрудника, а не
    свойство конкретной даты (и на 5/2 сохраняет прежний результат).
    """
    if schedule is None:
        return ABSENCE_DAY_HOURS
    hours = Decimal(str(schedule.hours_per_shift or 0))
    return hours if hours > _ZERO else ABSENCE_DAY_HOURS


def absence_pay(
    hourly_rate: Decimal | None, paid_days: int, day_hours: Decimal = ABSENCE_DAY_HOURS
) -> Decimal:
    """
    Оплата дней отпуска/больничного: `оклад / норма_часов × часы_отсутствия`,
    где `часы_отсутствия = оплачиваемые дни × длина смены графика`.

    Оплачиваемые дни — это рабочие СМЕНЫ ГРАФИКА, попавшие в период отсутствия
    (`is_payable_absence_day`), а `day_hours` — `absence_day_hours(schedule)`.
    Календарные дни отпуска сюда не приходят: у сменщика 14 календарных дней
    дают смены цикла, а не 14 дней × 8 ч.
    """
    if hourly_rate is None or paid_days <= 0:
        return _ZERO
    return hourly_rate * day_hours * Decimal(paid_days)


@dataclass
class CompanyBreakdown:
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    percent: Decimal
    overtime_hours: Decimal
    # Три непересекающиеся категории сверх оклада:
    #   off_schedule_* — выход в свой выходной по графику,
    #   holiday_*      — работа в нерабочий праздничный день календаря.
    off_schedule_hours: Decimal
    holiday_hours: Decimal
    base_amount: Decimal
    overtime_amount: Decimal
    off_schedule_amount: Decimal
    holiday_amount: Decimal
    total: Decimal


@dataclass
class EmployeePayroll:
    employee_id: int
    employee_name: str

    # Позиция (рабочее место), по которой посчитана строка (task_positions ч.A).
    # У сотрудника с совместительством строк столько же, сколько позиций, и
    # «к выплате» между ними НЕ суммируется — платят разные компании.
    position_id: int | None
    position_title: str | None
    is_primary_position: bool

    # rate — месячный оклад окладника; у посменного это УСЛОВНЫЙ оклад
    # (ставка × норма смен), от которого считаются отпускные/больничные.
    # У почасовика оклада нет вовсе — здесь None.
    rate: Decimal | None
    schedule_name: str | None

    # Тип оплаты и показатели посменной/почасовой оплаты
    pay_type: str
    shift_rate: Decimal | None
    hour_rate: Decimal | None
    worked_shifts: int
    norm_shifts: int | None
    # Смены, вошедшие в БАЗУ посменного (плановые дни графика). Смены в свой
    # выходной и в праздник сюда НЕ входят — они оплачены по коэффициенту
    # (task_shiftpay_addons), поэтому `base_amount = base_shifts × shift_rate`,
    # а не `worked_shifts × shift_rate`.
    base_shifts: int

    total_hours: Decimal
    norm_hours: Decimal | None
    delta_hours: Decimal | None
    overtime_hours: Decimal
    # off_schedule_* — выход в свой выходной ПО ГРАФИКУ (task_schedule_based_pay);
    # holiday_*      — работа в нерабочий ПРАЗДНИЧНЫЙ день производственного
    #                  календаря. Категории не пересекаются, праздник главнее.
    off_schedule_hours: Decimal
    holiday_hours: Decimal

    norm_days: int | None
    fact_days: int

    hourly_rate: Decimal | None

    base_amount: Decimal
    overtime_amount: Decimal
    off_schedule_amount: Decimal
    holiday_amount: Decimal
    total_amount: Decimal

    # Отсутствия (ОТ/ДО/Б/Н). *_days — сколько дней отмечено кодом,
    # *_paid_days — сколько из них рабочих по календарю (за них и платим).
    vacation_days: int
    unpaid_days: int
    sick_days: int
    absent_days: int
    vacation_paid_days: int
    sick_paid_days: int
    vacation_amount: Decimal
    sick_amount: Decimal

    # Годовой лимит больничного (часть 2): сколько оплачено, сколько сверх лимита
    sick_limit_days: int
    sick_days_used_before: int
    sick_unpaid_days: int
    sick_limit_remaining: int

    breakdown_by_company: list[CompanyBreakdown]
    is_calculable: bool
    reason_if_not_calculable: str | None


def calculate_employee_payroll(
    employee: Employee,
    entries: list[TimesheetEntry],
    calendar_data: dict | None,
    year: int,
    month: int,
    companies_by_id: dict[int, tuple[str, str]] | None = None,
    absences: list | None = None,
    sick_days_used_before: int = 0,
    sick_limit: int | None = None,
) -> EmployeePayroll:
    """Расчёт по ОСНОВНОЙ позиции сотрудника.

    Совместимость и одиночный случай: у сотрудника без совместительства позиция
    ровно одна, и результат идентичен доположенческому. Для совместителя нужен
    расчёт по каждому рабочему месту — `calculate_position_payroll`.
    """
    return calculate_position_payroll(
        employee, employee.primary_position, entries, calendar_data, year, month,
        companies_by_id, absences, sick_days_used_before, sick_limit,
    )


def calculate_position_payroll(
    employee: Employee,
    position: EmployeePosition | None,
    entries: list[TimesheetEntry],
    calendar_data: dict | None,
    year: int,
    month: int,
    companies_by_id: dict[int, tuple[str, str]] | None = None,
    absences: list | None = None,
    sick_days_used_before: int = 0,
    sick_limit: int | None = None,
) -> EmployeePayroll:
    """
    Чистая функция: считает зарплату ОДНОЙ ПОЗИЦИИ сотрудника за период.
    Не лезет в БД, принимает все данные на вход.

    Оклад/ставка, график, коэффициенты и тип оплаты берутся из позиции — у
    совместителя каждое рабочее место считается по своим условиям и независимо
    от остальных. От сотрудника нужны только имя и id для подписи строки.

    entries: часы ЭТОЙ позиции (разложить помогает services.positions).
    companies_by_id: dict[company_id → (code, name)]
    absences: список EmployeeAbsence сотрудника за месяц (ОТ/ДО/Б/Н). Отсутствие
        у человека одно на день (он отсутствует везде), а ОПЛАЧИВАЕТСЯ только по
        ОСНОВНОЙ позиции — от её оклада и нормы. На совместительстве дни
        отсутствия начислений не дают.
    sick_days_used_before: оплачиваемых дней больничного израсходовано в этом
        году ДО текущего месяца (годовой лимит, часть 2) — считает вызывающий
        код по всем месяцам года, здесь только применяется остаток.
    sick_limit: годовой лимит дней; None — из настроек.
    """
    if companies_by_id is None:
        companies_by_id = {}
    if sick_limit is None:
        sick_limit = sick_limit_days()

    # ── Отсутствия: дни по видам + сколько из них рабочих по календарю ────────
    # Норма от отсутствий НЕ меняется, факт часов — тоже (в день отсутствия
    # часов нет по инварианту взаимоисключения). Меняются только начисления.
    schedule = position.schedule if position is not None else None

    absence_days: dict[str, int] = {kind: 0 for kind in ABSENCE_CODES}
    absence_paid_days: dict[str, int] = {kind: 0 for kind in ABSENCE_CODES}
    sick_dates: list[date] = []
    for a in absences or []:
        absence_days[a.kind] = absence_days.get(a.kind, 0) + 1
        if a.kind == AbsenceKind.sick.value:
            sick_dates.append(a.work_date)
            continue  # больничный считаем ниже, через годовой лимит
        # Платим только за рабочие дни ПО ГРАФИКУ: выходной графика (у сменщика —
        # выходной цикла) и праздник в норму не входят, иначе «оклад за
        # отработанное + отпускные» вылезет за полный оклад.
        if is_payable_absence_day(a.work_date, calendar_data, schedule):
            absence_paid_days[a.kind] = absence_paid_days.get(a.kind, 0) + 1

    # Больничный: первые sick_limit рабочих дней в году — 100%, дальше 0.
    # Хронология внутри месяца и между месяцами — по дате (см. services.absences).
    sick_paid_dates, sick_over_dates = split_sick_dates_by_limit(
        sick_dates, calendar_data, sick_limit, sick_days_used_before, schedule,
    )
    absence_paid_days[AbsenceKind.sick.value] = len(sick_paid_dates)
    sick_unpaid_days = len(sick_over_dates)
    sick_limit_remaining = max(
        0, sick_limit - max(0, sick_days_used_before) - len(sick_paid_dates)
    )

    # Отпускные и больничные платит только ОСНОВНАЯ позиция (task_positions_fixes
    # п.1): отсутствие отмечено на человеке — он отсутствует на всех работах, —
    # но оплачивать его с каждого рабочего места значило бы платить отпуск N раз.
    # На совместительстве дни отсутствия просто не дают начисления.
    # Дни отсутствия (absence_days) остаются в строке справочно, обнуляется
    # только всё «оплачиваемое»: годовой лимит больничного — на человека и
    # расходуется основной позицией.
    pays_absences = position is None or bool(position.is_primary)
    if not pays_absences:
        absence_paid_days = {kind: 0 for kind in ABSENCE_CODES}
        sick_unpaid_days = 0

    # Aggregate hours by company and by date. Каждый день попадает ровно в одну
    # категорию (`day_category`): плановый рабочий → оклад/переработка,
    # праздник календаря → праздничные, свой выходной → вне графика.
    company_hours: dict[int, Decimal] = {}
    company_off_schedule_hours: dict[int, Decimal] = {}
    company_holiday_hours: dict[int, Decimal] = {}
    hours_by_date: dict[date, Decimal] = {}
    # Часы ПЛАНОВЫХ дней по датам — сумма по ВСЕМ компаниям в этот день.
    # База для по-дневного расчёта переработки.
    planned_hours_by_date: dict[date, Decimal] = {}
    total_hours = _ZERO
    total_off_schedule_hours = _ZERO
    total_holiday_hours = _ZERO
    # Категорию дня считаем один раз на дату (в дне может быть N компаний).
    category_by_date: dict[date, str] = {}

    for entry in entries:
        cid = entry.company_id
        h = entry.hours if isinstance(entry.hours, Decimal) else Decimal(str(entry.hours))
        total_hours += h
        if cid not in company_hours:
            company_hours[cid] = _ZERO
            company_off_schedule_hours[cid] = _ZERO
            company_holiday_hours[cid] = _ZERO
        company_hours[cid] += h
        hours_by_date[entry.work_date] = hours_by_date.get(entry.work_date, _ZERO) + h

        if entry.work_date not in category_by_date:
            category_by_date[entry.work_date] = day_category(
                schedule, entry.work_date, calendar_data
            )

        category = category_by_date[entry.work_date]
        if category == DAY_HOLIDAY:
            company_holiday_hours[cid] += h
            total_holiday_hours += h
        elif category == DAY_OFF_SCHEDULE:
            company_off_schedule_hours[cid] += h
            total_off_schedule_hours += h
        else:
            planned_hours_by_date[entry.work_date] = (
                planned_hours_by_date.get(entry.work_date, _ZERO) + h
            )

    # Норма/факт дней (правка 3.9-4) — справочные, в деньгах не участвуют.
    # Норма дней = плановых рабочих дней (смен) ПО ГРАФИКУ сотрудника; без
    # графика — рабочих дней по производственному календарю.
    # Факт дней = дней, в которых есть хотя бы один час работы (по всем компаниям).
    # Сменному календарь не нужен; weekday-графику без календаря норму дней
    # не показываем — она была бы завышена на праздники.
    norm_days: int | None = None
    if calendar_data is not None or is_cyclic_schedule(schedule):
        norm_days = norm_days_for_schedule(schedule, year, month, calendar_data)
    if norm_days is None and calendar_data is not None:
        norm_days = workdays_in_month(calendar_data, year, month)
    fact_days = len(hours_by_date)

    # Determine calculability and norm.
    # Норма — по ГРАФИКУ (task_shift_schedules): weekday-график считает её по
    # производственному календарю с учётом своих дней недели, cyclic — по циклу
    # от стартовой даты (календарь не влияет, поэтому сменнику он не нужен).
    schedule_name = schedule.name if schedule else None
    is_calculable = True
    reason: str | None = None
    norm_hours: Decimal | None = None

    issue = schedule_issue(schedule)
    if issue is not None:
        is_calculable = False
        reason = issue
    elif calendar_data is None and not is_cyclic_schedule(schedule):
        # Норма weekday-графика без календаря завышена (праздники не вычтены) —
        # деньги по ней считать нельзя. Сменному календарь не нужен: цикл сам
        # задаёт рабочие дни.
        is_calculable = False
        reason = "Производственный календарь не загружен"
    else:
        norm_hours = norm_hours_for_schedule(schedule, year, month, calendar_data)
        if norm_hours is None:
            is_calculable = False
            reason = "Норма не определена"
        elif norm_hours == _ZERO:
            is_calculable = False
            reason = "Норма не определена (0 рабочих дней)"

    # ── Тип оплаты позиции ────────────────────────────────────────────────────
    # Меняется ТОЛЬКО способ расчёта базовой суммы; переработка, отсутствия,
    # премии, удержания и распределение работают по общим правилам.
    #   salary    — месячный оклад, база пропорционально зачётным часам;
    #   per_shift — ставка за смену, «оклад» для отсутствий условный
    #               (ставка × норма смен месяца);
    #   hourly    — ставка за час, оклада нет вовсе.
    per_shift = is_per_shift(position)
    hourly = is_hourly(position)
    shift_rate: Decimal | None = None
    hour_rate: Decimal | None = None
    norm_shifts: int | None = norm_days if is_calculable else None
    worked_shifts = fact_days
    # Смен в базе посменного (плановых) — считается в ветке расчёта ниже.
    base_shifts = 0

    rate = position.rate if position is not None else None
    if per_shift:
        raw_shift_rate = getattr(position, "shift_rate", None)
        shift_rate = None if raw_shift_rate is None else Decimal(str(raw_shift_rate))
        if is_calculable and (shift_rate is None or shift_rate == _ZERO):
            is_calculable = False
            reason = "Не задана ставка за смену"
        # Оклад для формул (отпуск/больничный) — условный, из ставки и нормы смен.
        rate = notional_salary(position, norm_shifts) if is_calculable else None
    elif hourly:
        raw_hour_rate = getattr(position, "hour_rate", None)
        hour_rate = None if raw_hour_rate is None else Decimal(str(raw_hour_rate))
        if is_calculable and (hour_rate is None or hour_rate == _ZERO):
            is_calculable = False
            reason = "Не задана ставка за час"
        # У почасовика оклада нет ни настоящего, ни условного: отпуск и
        # больничный ему не начисляются, а значит и «оклад для формул» не нужен.
        rate = None
    elif is_calculable and (rate is None or rate == _ZERO):
        is_calculable = False
        reason = "Не задан оклад"

    # Переработка ПО ДНЯМ (task_overtime_daily, финальное решение — откат помесячного
    # варианта 3.11b п.0): для каждого дня max(0, факт_дня − дневная норма смены),
    # суммируем за месяц. Часы всех компаний в дне складываются, недоработка одного
    # дня не гасит переработку другого.
    # Часы вне графика и праздничные — отдельные категории: в переработку и в
    # базу оклада не входят, оплачиваются по своим правилам. Поэтому база
    # переработки — только часы ПЛАНОВЫХ дней.
    planned_hours = total_hours - total_off_schedule_hours - total_holiday_hours
    delta_hours: Decimal | None = None
    overtime_hours = _ZERO
    planned_credited_hours = planned_hours
    if norm_hours is not None and schedule is not None:
        delta_hours = total_hours - norm_hours
        overtime_hours = daily_overtime_hours(
            schedule, planned_hours_by_date, calendar_data
        )
        # Зачётные часы графика = Σ min(факт_дня, дневная норма) ≤ месячная норма.
        planned_credited_hours = planned_hours - overtime_hours

    # Financial amounts
    hourly_rate: Decimal | None = None
    base_amount = _ZERO
    overtime_amount = _ZERO
    off_schedule_amount = _ZERO
    holiday_amount = _ZERO

    vacation_amount = _ZERO
    sick_amount = _ZERO

    has_base = rate is not None or hour_rate is not None
    if is_calculable and has_base and norm_hours is not None and norm_hours > _ZERO:
        if hourly and hour_rate is not None:
            # ── Почасовая ─────────────────────────────────────────────────────
            # Платим ровно за отработанные часы: гарантии оклада нет, отработал
            # меньше нормы — получил пропорционально меньше (этим и отличается
            # от окладной, где норма даёт полный оклад).
            # Переработка — ПО ДНЯМ, единообразно с окладной (task_positions_fixes):
            # сверх дневной смены часы идут по коэффициенту позиции (0/1/1.5).
            # Помесячный вариант гасил переработку одного дня недоработкой другого.
            hourly_rate = hour_rate
            overtime_hours = hourly_overtime_hours(
                schedule, hours_by_date, calendar_data
            )
            base_amount = (total_hours - overtime_hours) * hour_rate
            overtime_amount = overtime_hours * hour_rate * _overtime_coeff(position)
            # Отдельных категорий «вне графика» и «праздничные» у почасовика нет:
            # каждый час уже оплачен по часовой ставке, а сверхнормативные —
            # с коэффициентом. Часы остаются в общем итоге, доплат сверх этого нет.
            total_off_schedule_hours = _ZERO
            total_holiday_hours = _ZERO
            company_off_schedule_hours = {cid: _ZERO for cid in company_hours}
            company_holiday_hours = {cid: _ZERO for cid in company_hours}
            # Отпуск и больничный почасовику НЕ начисляются: нет часов — нет
            # денег, код отсутствия остаётся справочной отметкой в табеле.
        elif per_shift and shift_rate is not None:
            # ── Посменная ─────────────────────────────────────────────────────
            # База — смены ПЛАНОВЫХ дней графика × ставка за смену.
            # Сверх базы (task_shiftpay_addons, разворот прежнего «надбавок нет»):
            #   - смена в свой выходной / праздник — та же ставка, но с
            #     коэффициентом позиции, и применяется к ЦЕЛОЙ смене, а не к часам;
            #   - часы сверх дневной нормы смены — переработка ПО ДНЯМ, как у
            #     окладной, по часовой ставке `ставка_смены / часы_смены`.
            # Категории дня — те же три (`day_category`), что у всех типов оплаты,
            # поэтому переработка считается только по плановым дням: часы выходной
            # смены уже оплачены целиком по коэффициенту.
            hourly_rate = rate / norm_hours  # условный оклад / норма — для отсутствий
            shift_hourly_rate = per_shift_hourly_rate(position, schedule) or _ZERO
            base_shifts = len(planned_hours_by_date)
            base_amount = shift_rate * Decimal(base_shifts)
            overtime_amount = (
                overtime_hours * shift_hourly_rate * _overtime_coeff(position)
            )
            off_schedule_shifts = Decimal(sum(
                1 for c in category_by_date.values() if c == DAY_OFF_SCHEDULE
            ))
            holiday_shifts = Decimal(sum(
                1 for c in category_by_date.values() if c == DAY_HOLIDAY
            ))
            off_schedule_amount = _off_schedule_pay(
                position, total_off_schedule_hours, off_schedule_shifts, shift_rate
            )
            holiday_amount = _holiday_pay(
                position, total_holiday_hours, holiday_shifts, shift_rate
            )
        else:
            # ── Окладная ──────────────────────────────────────────────────────
            # Оклад ПРОПОРЦИОНАЛЬНО отработанному: зачётные часы графика / норма.
            # Дни отсутствия часов не дают, поэтому оклад за них не начисляется —
            # они оплачиваются отдельно (отпускные/больничные) и сумма не задваивается.
            hourly_rate = rate / norm_hours
            base_amount = rate * min(_ONE, planned_credited_hours / norm_hours)
            # Переработка: (оклад/норма) × часы × коэффициент позиции (0/1/1.5).
            overtime_amount = overtime_hours * hourly_rate * _overtime_coeff(position)
            # Вне графика и праздничные — по настройкам позиции.
            # Единица повышенной категории у окладника — ЧАС.
            off_schedule_amount = _off_schedule_pay(
                position, total_off_schedule_hours, total_off_schedule_hours, hourly_rate
            )
            holiday_amount = _holiday_pay(
                position, total_holiday_hours, total_holiday_hours, hourly_rate
            )

        if not hourly:
            # Отпуск и больничный — отдельные начисления «оклад/норма × часы», где
            # часы = рабочие СМЕНЫ ГРАФИКА в периоде × длина смены. У окладной и
            # посменной формула одна (у посменного оклад условный).
            day_hours = absence_day_hours(schedule)
            vacation_amount = absence_pay(
                hourly_rate, absence_paid_days[AbsenceKind.vacation.value], day_hours
            )
            sick_amount = absence_pay(
                hourly_rate, absence_paid_days[AbsenceKind.sick.value], day_hours
            )

    base_amount = _round(base_amount)
    overtime_amount = _round(overtime_amount)
    off_schedule_amount = _round(off_schedule_amount)
    holiday_amount = _round(holiday_amount)
    vacation_amount = _round(vacation_amount)
    sick_amount = _round(sick_amount)
    # Итог включает оплату отсутствий; распределение по компаниям (breakdown)
    # ниже — только по «рабочим» категориям: отсутствие к юрлицу не привязано,
    # разнесение отпускных по юрлицам делает ведомость (проценты каскада).
    total_amount = (
        base_amount + overtime_amount + off_schedule_amount + holiday_amount
        + vacation_amount + sick_amount
    )

    # Company breakdown — суммы частей по каждой категории сходятся с итогом
    breakdown: list[CompanyBreakdown] = []
    if is_calculable and total_hours > _ZERO:
        base_parts = _distribute_whole_rubles(base_amount, company_hours)
        overtime_parts = _distribute_whole_rubles(overtime_amount, company_hours)
        # Повышенные категории разносим по ТЕМ ЖЕ часам, где они заработаны,
        # а не по всем часам компании.
        off_schedule_parts = _distribute_whole_rubles(
            off_schedule_amount, company_off_schedule_hours
        )
        holiday_parts = _distribute_whole_rubles(holiday_amount, company_holiday_hours)
        # Часы переработки по компании — пропорционально часам компании
        # (целочисленным методом наибольших остатков, сумма = overtime_hours).
        overtime_hours_parts = _distribute_whole_rubles(overtime_hours, company_hours)
        for cid in sorted(company_hours.keys()):
            comp_hours = company_hours[cid]
            proportion = comp_hours / total_hours
            percent = (proportion * _HUNDRED).quantize(_PERCENT_Q, rounding=ROUND_HALF_EVEN)
            comp_base = base_parts[cid]
            comp_overtime = overtime_parts[cid]
            comp_off_schedule = off_schedule_parts[cid]
            comp_holiday = holiday_parts[cid]
            code, name = companies_by_id.get(cid, ("", ""))
            breakdown.append(CompanyBreakdown(
                company_id=cid,
                company_code=code,
                company_name=name,
                hours=comp_hours,
                percent=percent,
                overtime_hours=overtime_hours_parts.get(cid, _ZERO),
                off_schedule_hours=company_off_schedule_hours.get(cid, _ZERO),
                holiday_hours=company_holiday_hours.get(cid, _ZERO),
                base_amount=comp_base,
                overtime_amount=comp_overtime,
                off_schedule_amount=comp_off_schedule,
                holiday_amount=comp_holiday,
                total=comp_base + comp_overtime + comp_off_schedule + comp_holiday,
            ))

    if per_shift:
        pay_type = PAY_TYPE_PER_SHIFT
    elif hourly:
        pay_type = PAY_TYPE_HOURLY
    else:
        pay_type = PAY_TYPE_SALARY

    return EmployeePayroll(
        employee_id=employee.id,
        employee_name=employee.full_name,
        position_id=position.id if position is not None else None,
        position_title=position.display_title if position is not None else None,
        is_primary_position=bool(position is not None and position.is_primary),
        rate=rate,
        schedule_name=schedule_name,
        pay_type=pay_type,
        shift_rate=shift_rate,
        hour_rate=hour_rate,
        worked_shifts=worked_shifts,
        norm_shifts=norm_shifts,
        base_shifts=base_shifts,
        total_hours=total_hours,
        norm_hours=norm_hours,
        delta_hours=delta_hours,
        overtime_hours=overtime_hours,
        off_schedule_hours=total_off_schedule_hours,
        holiday_hours=total_holiday_hours,
        norm_days=norm_days,
        fact_days=fact_days,
        hourly_rate=hourly_rate,
        base_amount=base_amount,
        overtime_amount=overtime_amount,
        off_schedule_amount=off_schedule_amount,
        holiday_amount=holiday_amount,
        total_amount=total_amount,
        vacation_days=absence_days[AbsenceKind.vacation.value],
        unpaid_days=absence_days[AbsenceKind.unpaid.value],
        sick_days=absence_days[AbsenceKind.sick.value],
        absent_days=absence_days[AbsenceKind.absent.value],
        vacation_paid_days=absence_paid_days[AbsenceKind.vacation.value],
        sick_paid_days=absence_paid_days[AbsenceKind.sick.value],
        vacation_amount=vacation_amount,
        sick_amount=sick_amount,
        sick_limit_days=sick_limit,
        sick_days_used_before=max(0, sick_days_used_before),
        sick_unpaid_days=sick_unpaid_days,
        sick_limit_remaining=sick_limit_remaining,
        breakdown_by_company=breakdown,
        is_calculable=is_calculable,
        reason_if_not_calculable=reason,
    )
