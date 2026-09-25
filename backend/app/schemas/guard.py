"""Схемы модуля «Вахта» (task_vahta).

Денежные поля строки табеля вычищаются табельщику на уровне API
(`app.services.finance_masking`), поэтому все суммы здесь необязательные: у
роли без доступа к финансам они приходят `null`, а не нулём — ноль читался бы
как «начислено ничего».
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Справочник: посты ─────────────────────────────────────────────────────────

class GuardShareInput(BaseModel):
    company_id: int
    percent: Decimal = Field(ge=0)


class GuardShareRead(GuardShareInput):
    company_name: str | None = None
    company_display_name: str | None = None


class GuardPostCreate(BaseModel):
    """Пост — точка внутри объекта.

    Ни процентов, ни должности: проценты у объекта, должность у человека.
    """

    site_id: int
    name: str = Field(min_length=1, max_length=255)
    #: None — ставка берётся у объекта; заданная переопределяет её.
    shift_rate: Decimal | None = Field(default=None, ge=0)
    sort_order: int = 0


class GuardPostUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    shift_rate: Decimal | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None


class GuardPostRead(BaseModel):
    id: int
    site_id: int
    site_name: str | None = None
    name: str
    department_id: int
    #: Своя ставка поста (может быть не задана).
    shift_rate: Decimal | None = None
    #: Ставка, которая реально применится: своя либо объекта.
    effective_rate: Decimal | None = None
    sort_order: int
    is_active: bool

    model_config = {"from_attributes": True}


# ── Справочник: зоны обслуживания ─────────────────────────────────────────────

class GuardZoneCreate(BaseModel):
    """Зона обслуживания — верхний уровень: группа географически близких объектов."""

    name: str = Field(min_length=1, max_length=255)
    department_id: int
    sort_order: int = 0


class GuardZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    sort_order: int | None = None
    is_active: bool | None = None


class GuardZoneRead(BaseModel):
    id: int
    name: str
    department_id: int
    sort_order: int
    is_active: bool
    site_count: int = 0
    crew_count: int = 0

    model_config = {"from_attributes": True}


# ── Справочник: объекты ───────────────────────────────────────────────────────

class GuardSiteCreate(BaseModel):
    """Охраняемый объект внутри зоны: ставка по умолчанию и распределение."""

    name: str = Field(min_length=1, max_length=255)
    #: Зона обслуживания. Отдел охраны берётся у неё — своего у объекта нет.
    zone_id: int
    shift_rate: Decimal = Field(default=Decimal("0"), ge=0)
    sort_order: int = 0
    #: Распределение затрат объекта по юрлицам — источник % для всех его постов.
    shares: list[GuardShareInput] | None = None


class GuardSiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    #: Перенос объекта в другую зону — законная операция.
    zone_id: int | None = None
    shift_rate: Decimal | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None
    shares: list[GuardShareInput] | None = None


class GuardSiteRead(BaseModel):
    id: int
    name: str
    zone_id: int
    zone_name: str | None = None
    department_id: int
    shift_rate: Decimal | None = None
    sort_order: int
    is_active: bool
    shares: list[GuardShareRead] = []
    posts: list[GuardPostRead] = []

    model_config = {"from_attributes": True}


# ── Справочник: экипажи ───────────────────────────────────────────────────────

class GuardCrewCreate(BaseModel):
    """Выездной экипаж ГБР зоны: своя ставка и своё распределение."""

    name: str = Field(min_length=1, max_length=255)
    #: Зона экипажа. За её пределы он не выезжает; в зоне экипажей может быть
    #: несколько.
    zone_id: int
    shift_rate: Decimal = Field(default=Decimal("0"), ge=0)
    sort_order: int = 0
    #: Своё распределение по юрлицам — у экипажей оно мультикомпанийное.
    shares: list[GuardShareInput] | None = None


class GuardCrewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    #: Перенос экипажа в другую зону — законная операция: экипаж по-прежнему
    #: принадлежит ОДНОЙ зоне, меняется только какой.
    zone_id: int | None = None
    shift_rate: Decimal | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None
    shares: list[GuardShareInput] | None = None


class GuardCrewRead(BaseModel):
    id: int
    name: str
    zone_id: int
    zone_name: str | None = None
    department_id: int
    shift_rate: Decimal | None = None
    sort_order: int
    is_active: bool
    shares: list[GuardShareRead] = []

    model_config = {"from_attributes": True}


# ── Табель ────────────────────────────────────────────────────────────────────

class GuardHalfRead(BaseModel):
    """Итог расчётной половины месяца (1–15 или 16–конец)."""

    half: int
    first_day: int
    last_day: int
    shifts: int
    salary: Decimal | None = None
    premium: Decimal | None = None
    penalty: Decimal | None = None
    official_payout: Decimal | None = None
    accrued: Decimal | None = None
    net_payout: Decimal | None = None
    #: Точная «к выплате» до округления вверх до 500 ₽.
    net_payout_exact: Decimal | None = None
    #: Налог на официальную выплату половины (task_vahta_taxes).
    tax: Decimal | None = None
    #: База разнесения половины: начислено + налог.
    distribution_base: Decimal | None = None


class GuardRowRead(BaseModel):
    """Строка табеля: человек (или пустой слот) на месте работы в месяце.

    Место одно из двух: пост объекта (`post_id`) либо выездной экипаж ГБР
    (`crew_id`). `post_name` — как это место называется, для колонки «КП».
    """

    id: int
    post_id: int | None = None
    crew_id: int | None = None
    post_name: str
    site_id: int | None = None
    site_name: str | None = None
    zone_id: int | None = None
    zone_name: str | None = None
    department_id: int
    #: Должность строки — из справочника вахты; от неё способ оплаты.
    job_title_id: int
    job_title_name: str
    pay_type: str

    # Пустой слот: пост есть, человека нет. Все поля сотрудника — None.
    employee_id: int | None = None
    position_id: int | None = None
    employee_name: str | None = None
    tab_number: str | None = None

    rate: Decimal | None = None
    is_official: bool = False
    note: str | None = None

    days: list[int] = []
    shifts: int = 0
    fact_hours: int = 0

    halves: list[GuardHalfRead] = []
    salary: Decimal | None = None
    premium: Decimal | None = None
    penalty: Decimal | None = None
    official_payout: Decimal | None = None
    accrued: Decimal | None = None
    net_payout: Decimal | None = None
    #: Точная «к выплате» до округления (сумма точных по половинам).
    net_payout_exact: Decimal | None = None
    #: Налог на официальную часть выплаты (официальная выплата × ставка).
    tax: Decimal | None = None
    #: База разнесения — затраты компании: «итого начислено» + налог.
    distribution_base: Decimal | None = None
    #: Разбивка БАЗЫ РАЗНЕСЕНИЯ (начислено + налог) по юрлицам согласно
    #: процентам места работы. Сумма больше «итого начислено» ровно на `tax`.
    distribution: dict[int, Decimal] | None = None
    #: Переплата по официальной выплате (task_official_payout_debt): пришло из
    #: прошлых половин, погашено здесь, ушло дальше. Деньги — табельщику None.
    official_debt_before: Decimal | None = None
    official_debt_after: Decimal | None = None
    official_debt_repaid: Decimal | None = None


class GuardCardRead(BaseModel):
    """Карточка главного экрана — ОБЪЕКТ или ЭКИПАЖ ГБР.

    Это разные вещи, поэтому у карточки есть `kind`: у объекта в `objects`
    перечислены его ПОСТЫ (что тут закрывают), у экипажа — ОБСЛУЖИВАЕМЫЕ
    ОБЪЕКТЫ (куда он выезжает).
    """

    #: "site" — охраняемый объект, "crew" — выездной экипаж ГБР.
    kind: str
    id: int
    name: str
    department_id: int
    objects: list[str] = []
    rows: list[GuardRowRead] = []
    total_accrued: Decimal | None = None


class GuardZoneCardRead(BaseModel):
    """Зона обслуживания на главном экране — верхний уровень группировки.

    Внутри сначала карточки её экипажей ГБР, потом карточки объектов: сперва
    «кто на выезде», потом сами объекты.
    """

    zone_id: int
    zone_name: str
    department_id: int
    cards: list[GuardCardRead] = []
    total_accrued: Decimal | None = None
    total_shifts: int = 0


class GuardCompanyTotal(BaseModel):
    company_id: int
    amount: Decimal


class GuardHalfTotal(BaseModel):
    half: int
    shifts: int
    accrued: Decimal | None = None
    net_payout: Decimal | None = None
    tax: Decimal | None = None


class GuardMonthRead(BaseModel):
    """Табель вахты за месяц — целиком или за одну расчётную половину."""

    year: int
    month: int
    #: Режим отображения: None — месяц целиком, 1 или 2 — расчётная половина.
    #: В режиме половины все суммы и смены посчитаны только за неё.
    view_half: int | None = None
    #: Первый и последний день показанного периода (1–31, 1–15 или 16–31).
    first_day: int = 1
    last_day: int = 31
    days_in_month: int
    #: Последний день первой расчётной половины — по нему рисуется черта в сетке.
    first_half_last_day: int
    departments: list[int] = []
    #: Табель сгруппирован ПО ЗОНАМ обслуживания.
    zones: list[GuardZoneCardRead] = []
    total_shifts: int = 0
    total_accrued: Decimal | None = None
    total_net_payout: Decimal | None = None
    #: Налоги на официальную часть за показанный период — ровно на столько
    #: сумма разнесения по юрлицам больше «итого начислено».
    total_tax: Decimal | None = None
    #: База разнесения за период: total_accrued + total_tax.
    total_distribution_base: Decimal | None = None
    #: Сумма разнесения по юрлицам (= total_accrued + total_tax, если у всех
    #: строк задано распределение места работы).
    total_distribution: Decimal | None = None
    #: Действующая ставка налога, в процентах — для подписи на экране.
    employer_tax_percent: Decimal | None = None
    halves: list[GuardHalfTotal] = []
    company_totals: list[GuardCompanyTotal] = []
    can_edit: bool = False
    #: Статус, которым период табеля охранного подразделения закрыт для правок:
    #: `pending_review` или `closed`; None — черновик. Назначения при нём не
    #: меняются (task_stage1 п.1.4), `can_edit` всегда False.
    period_lock: str | None = None
    can_see_money: bool = False


# ── Настройки вахты ───────────────────────────────────────────────────────────

class GuardTaxRateRead(BaseModel):
    """Версия ставки налога: с какого месяца какая ставка (task_stage3_historicity)."""

    #: None — с начала (перенесена миграцией).
    effective_from: datetime.date | None = None
    employer_tax_percent: Decimal
    created_by_name: str | None = None


class GuardSettingsRead(BaseModel):
    """Настройки вахты. Ставка — в ПРОЦЕНТАХ (40 = 40 %)."""

    #: Ставка, действующая в текущем месяце.
    employer_tax_percent: Decimal
    #: История ставок по месяцам.
    history: list[GuardTaxRateRead] = []
    #: С какого месяца форма предложит новую ставку (1-е число следующего).
    default_effective_from: datetime.date | None = None


class GuardSettingsUpdate(BaseModel):
    employer_tax_percent: Decimal = Field(ge=0, le=100)
    #: С какого месяца действует ставка — только 1-е число; не задано —
    #: следующий месяц. Ставка версионируется с месяца (решение заказчика).
    effective_from: datetime.date | None = None

    @field_validator("effective_from")
    @classmethod
    def _first_of_month(cls, v: datetime.date | None) -> datetime.date | None:
        if v is not None and v.day != 1:
            raise ValueError("Ставка налога меняется только с 1-го числа месяца")
        return v


# ── Справочник должностей охраны ──────────────────────────────────────────────

class GuardJobTitleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    #: per_shift — ставка за смену, salary — оклад за месяц.
    pay_type: str
    pay_type_label: str
    default_for_post: bool
    default_for_crew: bool
    sort_order: int
    is_active: bool
    #: Сколько строк табеля (за все месяцы) стоят на этой должности — чтобы
    #: экран предупредил, что смена способа оплаты пересчитает открытые месяцы.
    usage_count: int = 0
    #: Из них в ЗАКРЫТЫХ месяцах: при них способ оплаты менять нельзя (409).
    closed_usage_count: int = 0
    #: Рабочих мест штата на этой должности.
    staff_count: int = 0


class GuardJobTitleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    pay_type: str = "per_shift"
    default_for_post: bool = False
    default_for_crew: bool = False
    sort_order: int | None = None


class GuardJobTitleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    pay_type: str | None = None
    default_for_post: bool | None = None
    default_for_crew: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


# ── Мутации ───────────────────────────────────────────────────────────────────

class GuardAssignmentCreate(BaseModel):
    year: int
    month: int
    #: Место работы: пост объекта ЛИБО выездной экипаж ГБР — ровно одно из двух.
    post_id: int | None = None
    crew_id: int | None = None
    #: Должность строки (id из справочника). Не задана — та, что в справочнике
    #: помечена «по умолчанию» для этого вида места (пост / экипаж ГБР).
    job_title_id: int | None = None
    #: Кого ставим. None — пустой слот (незанятый пост).
    position_id: int | None = None
    #: Если у человека ещё нет рабочего места под этот пост — завести новое.
    employee_id: int | None = None
    #: Несколько человек на ОДИН пост сразу: на посту ГБР их и правда несколько
    #: (в образце у 3-го экипажа четверо). Задан — `employee_id` игнорируется,
    #: строки создаются одной транзакцией: половина поставленных людей хуже,
    #: чем понятная ошибка.
    employee_ids: list[int] | None = None
    rate: Decimal | None = Field(default=None, ge=0)


class GuardAssignmentUpdate(BaseModel):
    #: Должность правится прямо в строке табеля.
    job_title_id: int | None = None
    rate: Decimal | None = Field(default=None, ge=0)
    note: str | None = None
    premium_h1: Decimal | None = Field(default=None, ge=0)
    premium_h2: Decimal | None = Field(default=None, ge=0)
    penalty_h1: Decimal | None = Field(default=None, ge=0)
    penalty_h2: Decimal | None = Field(default=None, ge=0)
    # Официальной выплаты и отметки «официальный» здесь нет: они — свойства
    # рабочего места, а выплата вычисляется (task_guard_form_rate_official).
    # Руками её не ввести ни с экрана, ни запросом к API.


class GuardDayInput(BaseModel):
    assignment_id: int
    day: int = Field(ge=1, le=31)
    value: bool


class GuardDaysInput(BaseModel):
    """«Отметить все» / «снять все» — набор дней строки целиком."""

    assignment_id: int
    days: list[int]


class GuardReplaceInput(BaseModel):
    assignment_id: int
    #: Кто сменяет. position_id — существующее рабочее место, employee_id —
    #: человек, которому место под этот пост надо завести.
    position_id: int | None = None
    employee_id: int | None = None
    from_day: int = Field(ge=1, le=31)
    rate: Decimal | None = Field(default=None, ge=0)


class GuardQuickHireInput(BaseModel):
    """Быстрый найм: ФИО, место работы, ставка — три поля, как в ТЗ."""

    full_name: str = Field(min_length=3, max_length=255)
    #: Ровно одно из двух: пост объекта либо выездной экипаж ГБР.
    post_id: int | None = None
    crew_id: int | None = None
    #: Должность нанимаемого (id из справочника); не задана — обычная для места.
    job_title_id: int | None = None
    rate: Decimal | None = Field(default=None, ge=0)
    year: int | None = None
    month: int | None = None
    #: Сразу поставить нанятого на пост в этом месяце.
    assign: bool = True


class GuardSimilarEmployee(BaseModel):
    """Похожий сотрудник — предложение выбрать вместо создания дубля."""

    id: int
    full_name: str
    tab_number: str | None = None
    department_name: str | None = None
    is_active: bool = True


class GuardCandidateRead(BaseModel):
    """Кандидат в окне «Кто сменит на посту»."""

    employee_id: int
    position_id: int | None = None
    full_name: str
    tab_number: str | None = None
    #: Откуда человек: «3 экипаж, ГБР» или пусто, если сейчас нигде не стоит.
    where: str | None = None


# ── Сотрудники охраны (task_guard_ownership) ──────────────────────────────────

class GuardStaffRead(BaseModel):
    """Строка экрана «Сотрудники охраны» — одно рабочее место в охране.

    Пост — не свойство карточки, а строки табеля месяца: человека ставят на
    пост помесячно, поэтому `places` приходят за выбранный месяц и могут быть
    пустыми. Официальное трудоустройство, наоборот, свойство САМОГО места
    (task_guard_form_rate_official) и от месяца не зависит.

    Ставки здесь нет: цена смены живёт на объекте, посте или экипаже и в строке
    табеля.
    """

    employee_id: int
    position_id: int
    full_name: str
    tab_number: str | None = None
    department_id: int
    department_name: str
    #: Должность из справочника вахты. От неё тип оплаты. None — название
    #: рабочего места не совпадает ни с одной должностью справочника.
    job_title_id: int | None = None
    job_title_name: str
    pay_type: str
    #: Период работы НА ЭТОМ МЕСТЕ — правится здесь.
    hire_date: datetime.date | None = None
    dismissal_date: datetime.date | None = None
    #: Работает ли человек в компании (даты человека ведёт общий справочник).
    employee_is_active: bool = True
    #: Где стоит в выбранном месяце: «Объект · Пост» или экипаж ГБР.
    places: list[str] = []
    #: Официально трудоустроен на этом рабочем месте.
    is_official: bool = False
    #: Официальная зарплата НА РУКИ, ₽/мес (после НДФЛ). Деньги: табельщику
    #: приходит None, как остальные суммы.
    official_salary: Decimal | None = None


class GuardStaffCreate(BaseModel):
    full_name: str = Field(min_length=3, max_length=255)
    #: Пусто — следующий номер общей нумерации.
    tab_number: str | None = None
    department_id: int
    job_title_id: int
    hire_date: datetime.date | None = None
    dismissal_date: datetime.date | None = None
    #: Официальное трудоустройство: признак и зарплата НА РУКИ (₽/мес).
    #: Признак включён — зарплата обязательна.
    is_official: bool = False
    official_salary: Decimal | None = Field(default=None, ge=0)


class GuardStaffUpdate(BaseModel):
    """ФИО и таб. № здесь не правятся: это поля человека, их ведёт справочник.
    Подразделение вне охраны — перевод, он требует `?confirm=true`."""

    department_id: int | None = None
    job_title_id: int | None = None
    #: Ставка или оклад — ТОЛЬКО при переводе в обычное подразделение: там она
    #: нужна расчёту, а у охранного места суммы нет вовсе. Присланная при
    #: правке охранного места — игнорируется.
    amount: Decimal | None = Field(default=None, ge=0)
    hire_date: datetime.date | None = None
    dismissal_date: datetime.date | None = None
    is_official: bool | None = None
    official_salary: Decimal | None = Field(default=None, ge=0)
    #: С какой даты действует изменение официальной зарплаты / признака
    #: (task_stage3_historicity). Не задано — 1-е число следующего месяца.
    terms_effective_from: datetime.date | None = None
