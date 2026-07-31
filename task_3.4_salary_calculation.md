# Задача 3.4 — Расчёт ЗП в табеле

## Контекст

Табель готов: часы вводятся, статусы переходят, автозаполнение работает. Теперь добавляем финансы — расчёт зарплаты на основе фактически отработанных часов. Это не выгрузка в 1С (это задача 3.6), а **расчёт для просмотра в системе**: бухгалтер видит сколько и за что начислено перед закрытием периода.

## Бизнес-правила расчёта

### Базовая формула

Для каждого сотрудника за месяц:

```
оклад_к_выплате = rate × (фактически_отработано / норма_по_графику)
```

где:
- `rate` — оклад из карточки сотрудника
- `фактически_отработано` — сумма всех часов в табеле за месяц (по всем компаниям)
- `норма_по_графику` — workdays × hours_per_shift − short_days из производственного календаря и графика сотрудника

Если сотрудник отработал ровно норму → получает полный оклад.
Если меньше → пропорционально меньше.
Если больше → полный оклад + переработка отдельной строкой (см. ниже).

### Переработка

Часы свыше нормы оплачиваются по тарифу:

```
часовая_ставка = rate / норма
переработка_оплата = (отработано - норма) × часовая_ставка × 1.5
```

То есть `×1.5` от обычной часовой ставки. Это **сверх оклада**, не вместо.

### Праздничные часы (×1.5)

Часы попавшие на дни типа `holiday` из производственного календаря оплачиваются по ставке `×1.5`:

```
часы_в_праздники = сумма часов где work_date — holiday в календаре
доплата_за_праздники = часы_в_праздники × часовая_ставка × 0.5
```

Здесь `×0.5` потому что **базовая часть этих часов уже учтена в окладе**. Праздничный коэффициент — это **доплата сверху**, превращающая обычную часовую ставку в полуторную.

Если часы попали на сокращённый день (`short`) или рабочий (`work`) — это обычные часы, никакой доплаты.

### Распределение по компаниям

Раз сотрудник работал на несколько юрлиц — выплата идёт **с каждой компании отдельно**, пропорционально отработанным часам. Логика:

```
для каждой компании:
    доля = часы_по_этой_компании / общий_часы_сотрудника
    оклад_от_компании = оклад_к_выплате × доля
    переработка_от_компании = переработка_оплата × доля_переработки
    доплата_празд_от_компании = доплата_за_праздники × доля_праздничных_по_компании
    итого_от_компании = сумма
```

Чтобы избежать дробной копейки — округление по правилу **«банковское» (round half to even)** до целых рублей. Сумма по компаниям может отличаться от общего на 1-2 рубля из-за округления — это нормально, в строке «Итого» показываем сумму по компаниям.

### Что НЕ считаем в системе

- НДФЛ (13%) — это задача 1С
- Авансы, удержания — задача 1С
- Премии, бонусы — задача 1С
- Страховые взносы, налоги работодателя — задача 1С

Мы считаем **чистый брутто к начислению** — что должно прийти в 1С как основание для начисления.

### Граничные случаи

| Кейс | Поведение |
|---|---|
| `rate = NULL` или `rate = 0` | Все суммы = 0. Колонки показываем как «—» |
| `schedule = NULL` (нет графика) | Норма не определена, переработка/недоработка не считаются. Часы × дневная ставка не работает. Показываем «—» с тултипом «Не задан график» |
| `default_company = NULL` | Распределение по компаниям всё равно работает (есть entries) — деньги распределяются между компаниями где есть часы |
| `норма = 0` (выходной месяц, типа января с 1 рабочим днём) | Считаем по факту: всё что отработано — переработка. Базовый оклад = 0. На практике вряд ли будет, но обработать как edge case |
| Сотрудник уволен в середине месяца | Часы за период до увольнения учитываются обычно. Норма — за **полный месяц** (мы не считаем пропорцию по дням работы, это сделает 1С) |
| Сокращённый день (8 час → 7 час) | В норме уже учтено (норма = workdays × 8 − short_days). Часы фактически отработанные — как есть, без коэффициента |

## Что нужно сделать

### Часть А — бэкенд

#### 1. Сервис расчёта

`app/services/payroll.py` — новый модуль:

```python
from decimal import Decimal, ROUND_HALF_EVEN

@dataclass
class CompanyBreakdown:
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    base_amount: Decimal      # часть оклада с этой компании
    overtime_amount: Decimal  # доплата за переработку
    holiday_amount: Decimal   # доплата за праздники
    total: Decimal

@dataclass
class EmployeePayroll:
    employee_id: int
    employee_name: str
    rate: Decimal | None
    schedule_name: str | None
    
    # Часы
    total_hours: Decimal      # всего отработано
    norm_hours: Decimal | None  # норма по графику и календарю
    delta_hours: Decimal | None  # отработано - норма
    overtime_hours: Decimal   # часы переработки (max(0, total - norm))
    holiday_hours: Decimal    # часы в дни holiday
    
    # Часовая ставка (только если есть rate и norm > 0)
    hourly_rate: Decimal | None
    
    # Суммы
    base_amount: Decimal      # оклад × отработано/норма (capped at rate)
    overtime_amount: Decimal  # overtime_hours × hourly × 1.5
    holiday_amount: Decimal   # holiday_hours × hourly × 0.5 (доплата сверх базы)
    total_amount: Decimal     # итого к начислению
    
    breakdown_by_company: list[CompanyBreakdown]
    is_calculable: bool       # False если нет rate или нет schedule
    reason_if_not_calculable: str | None  # «Не задан оклад», «Не задан график» и т.п.

def calculate_employee_payroll(
    employee: Employee,
    entries: list[TimesheetEntry],
    calendar_data: dict,
    year: int,
    month: int,
) -> EmployeePayroll:
    """
    Чистая функция: считает зарплату сотрудника за период.
    Не лезет в БД, принимает все данные на вход.
    Использует функции из app.services.calendar для определения типов дней.
    """
```

Округление: использовать `Decimal.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)` для всех денежных значений (точность до копейки), а потом до целых рублей `.quantize(Decimal('1'), rounding=ROUND_HALF_EVEN)`.

Все промежуточные расчёты — в Decimal, не float. Никаких `*` и `/` с числами с плавающей точкой.

#### 2. Pydantic-схемы

`app/schemas/payroll.py`:

```python
class CompanyBreakdownRead(BaseModel):
    company_id: int
    company_code: str
    company_name: str
    hours: Decimal
    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total: Decimal

class EmployeePayrollRead(BaseModel):
    employee_id: int
    employee_name: str
    rate: Decimal | None
    schedule_name: str | None
    
    total_hours: Decimal
    norm_hours: Decimal | None
    delta_hours: Decimal | None
    overtime_hours: Decimal
    holiday_hours: Decimal
    hourly_rate: Decimal | None
    
    base_amount: Decimal
    overtime_amount: Decimal
    holiday_amount: Decimal
    total_amount: Decimal
    
    breakdown_by_company: list[CompanyBreakdownRead]
    is_calculable: bool
    reason_if_not_calculable: str | None

class PayrollSummaryRead(BaseModel):
    year: int
    month: int
    employees: list[EmployeePayrollRead]
    # агрегаты для футера таблицы
    total_employees: int
    total_hours: Decimal
    total_base_amount: Decimal
    total_overtime_amount: Decimal
    total_holiday_amount: Decimal
    grand_total: Decimal
```

#### 3. Расширение существующего эндпойнта табеля

В `TimesheetMonthResponse` добавить опциональное поле `payroll: PayrollSummaryRead | None`.

В `GET /api/timesheet/{year}/{month}` — добавить query-параметр `?include_payroll=true`. Только если параметр true И роль = admin/accountant — посчитать payroll и положить в ответ. Для manager и employee — игнорировать параметр, payroll всегда null.

Это устраняет лишний запрос с фронта (один HTTP-вызов на табель + ЗП).

#### 4. Отдельный эндпойнт для сводки

`GET /api/timesheet/{year}/{month}/payroll`:
- Возвращает `PayrollSummaryRead`
- Только admin / accountant. Manager/employee — 403
- Учитывает фильтр `?department_id=X` как у GET табеля
- Кеширование не нужно, считаем на лету

#### 5. Тесты

`tests/test_payroll.py` — много кейсов, расчёт критичен:

**Юнит-тесты `calculate_employee_payroll`:**
- Полная норма: rate=80000, norm=151, total=151 → base=80000, overtime=0, holiday=0
- Недоработка: rate=80000, norm=151, total=140 → base=80000×140/151≈74172
- Переработка: rate=80000, norm=151, total=160 → base=80000, overtime=(160−151)×(80000/151)×1.5≈7152
- Работа в праздник: 8 часов на 1 мая (holiday) → holiday_amount=8×(80000/151)×0.5
- Сокращённый день: 7 часов на 8 мая (short) — никакой доплаты, считается как обычный
- Нет rate: rate=None → is_calculable=False, все суммы=0
- Нет schedule: schedule=None → is_calculable=False
- Несколько компаний 50/50: 4ч на A + 4ч на B каждый день → breakdown по 50% от оклада на каждую
- Round half to even: проверить что суммы округляются банковским способом

**Интеграционные тесты эндпойнтов:**
- `GET /api/timesheet/{year}/{month}?include_payroll=true` для admin — содержит payroll
- То же от manager — payroll=null
- `GET /api/timesheet/{year}/{month}/payroll` от admin — 200
- То же от manager — 403
- `GET /api/timesheet/{year}/{month}/payroll?department_id=X` — фильтр работает

**Граничные кейсы:**
- Праздничный день имеет много часов (24) — корректно считается
- Сотрудник без часов в месяце — total=0, всё =0
- Сотрудник с часами но без rate — is_calculable=False, hours видим, money не считаем

### Часть Б — фронтенд

#### 6. Типы

`frontend/src/types/api.ts`:

```typescript
export interface CompanyBreakdown {
  company_id: number;
  company_code: string;
  company_name: string;
  hours: string;  // decimal as string from backend
  base_amount: string;
  overtime_amount: string;
  holiday_amount: string;
  total: string;
}

export interface EmployeePayroll {
  employee_id: number;
  employee_name: string;
  rate: string | null;
  schedule_name: string | null;
  
  total_hours: string;
  norm_hours: string | null;
  delta_hours: string | null;
  overtime_hours: string;
  holiday_hours: string;
  hourly_rate: string | null;
  
  base_amount: string;
  overtime_amount: string;
  holiday_amount: string;
  total_amount: string;
  
  breakdown_by_company: CompanyBreakdown[];
  is_calculable: boolean;
  reason_if_not_calculable: string | null;
}

export interface PayrollSummary {
  year: number;
  month: number;
  employees: EmployeePayroll[];
  total_employees: number;
  total_hours: string;
  total_base_amount: string;
  total_overtime_amount: string;
  total_holiday_amount: string;
  grand_total: string;
}
```

Расширить `TimesheetMonthResponse`:
```typescript
export interface TimesheetMonthResponse {
  // ... существующие поля
  payroll: PayrollSummary | null;
}
```

#### 7. Утилита форматирования

`frontend/src/utils/money.ts`:

```typescript
export function formatMoney(value: string | null, options?: { showZero?: boolean }): string {
  if (value === null) return '—';
  const num = parseFloat(value);
  if (num === 0 && !options?.showZero) return '—';
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(num);
}

export function formatHours(value: string | null): string {
  if (value === null) return '—';
  const num = parseFloat(value);
  return num.toFixed(2).replace(/\.?0+$/, '');  // "8.50" → "8.5", "8.00" → "8"
}
```

#### 8. API-клиент

`frontend/src/api/timesheet.ts` добавить:

```typescript
getMonth(year: number, month: number, options?: {
  department_id?: number;
  include_payroll?: boolean;
}): Promise<TimesheetMonthResponse>

getPayroll(year: number, month: number, departmentId?: number): Promise<PayrollSummary>
```

Логика на странице табеля: если текущий пользователь — admin/accountant, добавлять `?include_payroll=true` к запросу. Для manager/employee — нет.

#### 9. Финансовые колонки в табеле

После уже существующих колонок (день 1..31, Итого часов) — добавить колонки:

| Колонка | Значение | Кому видно |
|---|---|---|
| Норма | norm_hours | admin / accountant |
| Δ | delta_hours (зелёный/красный) | admin / accountant |
| Часов | total_hours | всем |
| Оклад | base_amount | admin / accountant |
| Сверхуроч. | overtime_amount | admin / accountant |
| Праздн. | holiday_amount | admin / accountant |
| Итого ₽ | total_amount (жирно, синий) | admin / accountant |

Эти колонки — sticky справа, как «Итого» сейчас. На сотрудника одна строка значений (т.к. payroll агрегирован по сотруднику, не по компании). Рендерится в первой строке блока сотрудника с rowspan=число_строк_компаний.

Для manager/employee — финансовые колонки не отображаются вообще. Видны только: Норма (если расчёт возможен) / Δ / Часов.

Для строки с `is_calculable=false` — в финансовых колонках «—» и серым курсивом тултип с reason.

Цвета:
- Δ > 0 (переработка) — оранжевый/янтарь
- Δ < 0 (недоработка) — красный
- Δ = 0 — нейтральный

#### 10. Sticky footer строка «Итого по месяцу»

Под последней строкой табеля — sticky-row «ИТОГО»:
- Всего сотрудников
- Всего часов
- Всего к выплате (сумма по всем)
- Видна admin/accountant
- Для manager/employee — упрощённая (только сумма часов)

#### 11. Сводная страница (опционально, в этой задаче делаем)

Отдельная страница `/admin/payroll` доступная admin/accountant — простая сводка по сотрудникам в формате таблицы:

| ФИО | Отдел | Часов | Норма | Δ | Оклад | Сверх. | Празд. | Итого ₽ |
|---|---|---|---|---|---|---|---|---|

С разбиением по компаниям (раскрывающаяся строка под каждым сотрудником) — список компаний с суммами. Полезно для бухгалтера при выгрузке.

В sidebar — пункт «Расчёт ЗП» (или «Финансы») в группе «Учёт» (под Табелем).

### Часть В — общее

#### 12. CLAUDE.md

Добавить раздел «Payroll»:
- Считаем брутто к начислению, не чистыми
- Decimal везде, никаких float
- Round half to even
- Праздничные = ×0.5 доплата (база уже в окладе)
- Переработка = ×1.5 целиком (сверх оклада)
- Финансы видят только admin/accountant
- НДФЛ/удержания/премии — задача 1С, мы не считаем

#### 13. Коммиты

- `feat(backend): payroll calculation service with Decimal`
- `feat(backend): payroll endpoints and integration with timesheet response`
- `feat(frontend): financial columns in timesheet for admin/accountant`
- `feat(frontend): payroll summary page`

Запушить на main.

## Acceptance criteria

```bash
pytest -v
# 130+ зелёных
```

В UI:

**Под admin:**
1. На странице табеля справа от часов — колонки: Норма, Δ, Часов, Оклад, Сверх., Праздн., Итого ₽
2. Создан сотрудник: оклад 80000, график 5/2 (8ч/смена)
3. Май 2026: норма 151 час, заполнено 151 ч → Оклад 80000, остальное 0
4. Изменить одну ячейку с 8 на 10 (стало 153 ч): Δ +2, Сверх. ≈ 1060
5. Заполнить ячейку на 1 мая (праздник) 8ч (стало 161 ч): Δ +10, Праздн. ≈ 2120, Сверх. ≈ 5298
6. Sticky footer показывает «1 сотрудник, 161 час, ~87 тыс ₽»
7. Сотрудник без оклада — в финансовых колонках «—», тултип «Не задан оклад»

**Под manager:**
8. Финансовых колонок НЕТ. Только Часов / Норма / Δ
9. Footer показывает только часы

**Сводная страница (admin):**
10. /admin/payroll — таблица сотрудников с суммами
11. Клик на строку — раскрывается breakdown по компаниям

## Подводные камни

- **Decimal serialization**: Pydantic v2 сериализует Decimal как строку. Фронт принимает как string, парсит при отображении. Это намеренно — никаких 0.30000000004.
- **Округление**: ROUND_HALF_EVEN — это default для финансов в большинстве стран. ROUND_HALF_UP может дать другие итоги. Зафиксируй один путь и держись его.
- **Norm = 0**: если в месяце 0 рабочих дней (странно, но возможно при кривом календаре), деление на ноль. Обработать: `is_calculable=False, reason="Норма не определена"`.
- **Distribution edge case**: если total_hours = 0 у сотрудника с rate, тоже не делить. Всё в 0.
- **Holiday hours считать только если они > 0**: если в праздник часов нет — никаких доплат
- **Manager security**: даже если manager отправит ?include_payroll=true — бэк должен игнорировать. Проверка на бэке, не на фронте.
- **Frontend perf**: при рендере таблицы с 50+ сотрудниками не пересчитывать payroll на каждый keystroke. Расчёт делается на бэке, фронт только отображает. При сохранении ячейки — заново зовём API.

## Что НЕ делать

- Не считать НДФЛ / страховые / премии
- Не делать выгрузку — это 3.6
- Не делать редактирование сумм вручную (только через изменение часов)
- Не делать историю изменений расчёта (он каждый раз считается от часов)
- Не делать сравнение с прошлым месяцем (дашборд — это 4 этап)
- Не реализовывать Т-13 коды для разных типов часов (это 3.5, тогда модель расчёта расширится)

## В конце

1. Скрин табеля под admin с финансовыми колонками
2. Скрин табеля под manager — финансовых колонок нет
3. Скрин сводной страницы /admin/payroll с раскрытым breakdown
4. pytest -v
