# Задача 2.4 — Производственный календарь РФ

## Контекст

Перед запуском Этапа 3 (табель) необходимо загрузить производственный календарь — данные о праздничных и сокращённых днях в РФ. Норма часов в табеле, расчёт отклонений, отображение типов дней — всё зависит от календаря.

**Источник:** xmlcalendar.ru — открытые данные, JSON API: `https://xmlcalendar.ru/data/ru/{YEAR}/calendar.json`

**Формат данных:**
```json
{
  "year": 2026,
  "months": [
    { "month": 1, "days": "1,2,3,4,5,6,7,8,9+,10,11,17,18,24,25,31" },
    { "month": 2, "days": "1,7,8,14,15,21,22,23,28" }
  ]
}
```

Где в строке `days` каждый токен через запятую:
- `1` — выходной/праздничный день (не рабочий)
- `9+` — праздничный день (помеченный, тоже не рабочий)
- `8*` — сокращённый рабочий день (рабочий, но минус 1 час)

**Норма часов в месяце** = `(рабочих_дней × часы_смены) − количество_сокращённых_дней`.

## Стратегия загрузки

Бэкенд — сервер с интернетом, CORS его не касается. Поэтому:

1. **Primary**: загрузка с xmlcalendar.ru через httpx
2. **Fallback**: ручной импорт JSON через UI (для редкого случая когда сайт недоступен или нужны корректировки)
3. **Один раз сохранили — данные в БД** — больше внешние запросы не нужны

**Никаких встроенных данных в коде** — это анти-паттерн «hardcoded business data», устаревает с каждым годом.

## Что нужно сделать

### Часть А — бэкенд

#### 1. Модель ProductionCalendar

Создать `app/models/production_calendars.py`:

```python
class ProductionCalendar(Base):
    __tablename__ = "production_calendars"
    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)  # полный JSON от xmlcalendar
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'remote' | 'manual'
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # стандартные created_at, updated_at
```

Поле `data` хранит весь JSON месяцев. JSONB на Postgres, fallback на JSON TypeDecorator для SQLite в тестах (как уже сделано в audit_log).

#### 2. Сервис парсинга и норм (pure functions)

Создать `app/services/calendar.py` с чистыми функциями (без БД):

```python
def parse_days_string(days: str) -> tuple[set[int], set[int]]:
    """
    Парсит строку '1,2,3,8*,9+,10' →
      ({1,2,3,9,10}, {8})  # (нерабочие_дни, сокращённые_дни)
    """

def get_month_data(calendar_data: dict, month: int) -> dict | None:
    """Извлекает блок месяца из JSON календаря, или None."""

def is_workday(calendar_data: dict, year: int, month: int, day: int) -> bool:
    """True если день рабочий (включая сокращённые)."""

def is_short_day(calendar_data: dict, month: int, day: int) -> bool:
    """True если день сокращённый (минус час)."""

def is_holiday(calendar_data: dict, month: int, day: int) -> bool:
    """True если день не рабочий."""

def workdays_in_month(calendar_data: dict, year: int, month: int) -> int:
    """Количество рабочих дней (включая сокращённые)."""

def short_days_in_month(calendar_data: dict, month: int) -> int:
    """Количество сокращённых дней."""

def norm_hours_for_period(calendar_data: dict, year: int, month: int, hours_per_shift: int) -> int:
    """Норма часов = workdays × hours_per_shift − short_days."""
```

Все функции — pure, легко тестируются. Принимают `data: dict` (содержимое поля `ProductionCalendar.data`), не сам объект модели. Это нужно для:
- Тестируемости (никакой БД в юнит-тестах)
- Переиспользования из других сервисов в будущем
```

#### 3. Сервис загрузки (с БД и сетью)

В том же `app/services/calendar.py`:

```python
async def fetch_calendar_from_remote(year: int) -> dict:
    """
    Тянет JSON с xmlcalendar.ru через httpx.AsyncClient.
    Таймаут 10 секунд. Бросает CalendarFetchError с понятным сообщением.
    URL: https://xmlcalendar.ru/data/ru/{year}/calendar.json
    Валидирует что в ответе есть year и months.
    """

async def ensure_calendar(db: Session, year: int) -> ProductionCalendar:
    """
    Получить календарь из БД, или загрузить с remote и сохранить.
    Если remote не отвечает — бросает CalendarFetchError.
    Идемпотентно: повторный вызов возвращает кешированный из БД.
    """

async def reload_calendar(db: Session, year: int) -> ProductionCalendar:
    """
    Принудительная перезагрузка с remote, обновление записи в БД.
    source='remote', loaded_at=now()
    """

def save_calendar_from_dict(db: Session, year: int, data: dict, source: str = 'manual') -> ProductionCalendar:
    """
    Сохранение календаря из словаря (для ручного импорта).
    Валидирует структуру (year, months[]). Upsert по year.
    """
```

Кастомное исключение:
```python
class CalendarFetchError(Exception):
    """Не удалось получить календарь с xmlcalendar.ru"""
```

#### 4. Pydantic-схемы

`app/schemas/calendar.py`:

```python
class MonthData(BaseModel):
    month: int = Field(ge=1, le=12)
    days: str  # '1,2,3,8*,9+,10'

class CalendarRead(BaseModel):
    id: int
    year: int
    months: list[MonthData]  # извлечь из data['months']
    source: str
    loaded_at: datetime
    workdays_total: int  # подсчитать через сервис
    short_days_total: int

class CalendarImportRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    months: list[MonthData] = Field(min_length=12, max_length=12)

class DayInfo(BaseModel):
    day: int
    type: Literal['work', 'short', 'holiday']
    weekday: int  # 0=Пн, 6=Вс

class MonthSummary(BaseModel):
    year: int
    month: int
    workdays: int
    short_days: int
    norm_hours_8h: int  # норма для графика 8 ч/смена (как ориентир)
    days: list[DayInfo]
```

#### 5. Роутер

`app/routers/calendar.py` префикс `/api/calendar`:

- `GET /api/calendar/{year}` — вернуть календарь года.
  - Если в БД есть — отдать
  - Если нет — попробовать `ensure_calendar` (загрузит с remote)
  - Если remote недоступен — **404 с понятным message**: «Календарь не найден. Загрузите вручную через POST /api/calendar/import или повторите попытку позже»
  - Все авторизованные роли

- `POST /api/calendar/{year}/load` (admin only) — принудительная перезагрузка с remote.
  - 503 если remote недоступен
  - Audit log: action="calendar_loaded"

- `POST /api/calendar/import` (admin only) — ручной импорт из JSON (тело — CalendarImportRequest).
  - 201 при создании, 200 при обновлении
  - Audit log: action="calendar_imported"

- `GET /api/calendar/{year}/{month}/summary` — детали по месяцу: MonthSummary.
  - Если календарь года ещё не загружен — пытается ensure_calendar
  - Если совсем нет данных — 404
  - Все авторизованные роли

#### 6. Миграция

Сгенерировать миграцию с новой таблицей. Прогнать `alembic upgrade head`.

#### 7. Автозагрузка при старте

В `main.py` через **lifespan** (не deprecated on_event) — при запуске сервера асинхронно попробовать загрузить календари на текущий и следующий год, **только если их нет в БД**:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    current_year = datetime.now().year
    for year in [current_year, current_year + 1]:
        try:
            with SessionLocal() as db:
                exists = db.query(ProductionCalendar).filter_by(year=year).first()
                if not exists:
                    await ensure_calendar(db, year)
                    logger.info(f"Auto-loaded calendar {year}")
        except CalendarFetchError as e:
            logger.warning(f"Could not preload calendar for {year}: {e}")
    yield
    # shutdown — пока ничего

app = FastAPI(lifespan=lifespan, ...)
```

Если сети нет — просто warning в логах, сервер запустится. Юзер потом подгрузит вручную.

#### 8. Тесты

`tests/test_calendar.py`:

**Юнит-тесты парсера (без БД, без сети):**
- `parse_days_string('1,2,3,8*,9+,10')` → `({1,2,3,9,10}, {8})`
- `parse_days_string('')` → `(set(), set())`
- `parse_days_string('8*')` → `(set(), {8})`
- `parse_days_string(' 1 , 2 , 8* ')` → `({1,2}, {8})` (с пробелами)

**Юнит-тесты норм (фикстура с тестовым календарём):**
Использовать фикстуру с фиксированным календарём 2026:
```python
CALENDAR_2026 = {
    "year": 2026,
    "months": [
        {"month": 5, "days": "1,2,3,8*,9,10,11+,16,17,23,24,30,31"},
        # минимум май для теста, плюс пару других
    ]
}
```

- `workdays_in_month(CALENDAR_2026, 2026, 5)` → 19 (31 день минус 12 нерабочих)
- `short_days_in_month(CALENDAR_2026, 5)` → 1
- `norm_hours_for_period(CALENDAR_2026, 2026, 5, 8)` → 151

**Интеграционные тесты с моком сети:**
Замокать `httpx.AsyncClient.get` через `monkeypatch` или `pytest-httpx`. Никаких реальных запросов в тестах.

- `GET /api/calendar/2026` — если БД пуста, мок возвращает данные, endpoint их сохраняет и отдаёт
- `GET /api/calendar/2026` — если remote недоступен (мок бросает исключение), endpoint возвращает 404 с понятным detail
- `POST /api/calendar/2026/load` от не-admin — 403
- `POST /api/calendar/import` от admin с валидным телом — создаёт запись, source='manual'
- `POST /api/calendar/import` с битым телом (не 12 месяцев) — 422 (Pydantic validation)
- `GET /api/calendar/2026/5/summary` — возвращает 31 день правильного типа

**Все тесты должны проходить без сети.**

### Часть Б — фронтенд

#### 9. Типы

В `frontend/src/types/api.ts`:

```typescript
export interface MonthData {
  month: number;
  days: string;
}

export interface ProductionCalendar {
  id: number;
  year: number;
  months: MonthData[];
  source: 'remote' | 'manual';
  loaded_at: string;
  workdays_total: number;
  short_days_total: number;
}

export type DayType = 'work' | 'short' | 'holiday';

export interface MonthSummary {
  year: number;
  month: number;
  workdays: number;
  short_days: number;
  norm_hours_8h: number;
  days: Array<{ day: number; type: DayType; weekday: number }>;
}
```

#### 10. API-клиент

`frontend/src/api/calendar.ts`:

```typescript
getCalendar(year: number): Promise<ProductionCalendar>
loadCalendar(year: number): Promise<ProductionCalendar>  // admin only, force reload
importCalendar(payload: {year: number, months: MonthData[]}): Promise<ProductionCalendar>
getMonthSummary(year: number, month: number): Promise<MonthSummary>
```

#### 11. Страница «Производственный календарь»

Создать `frontend/src/pages/admin/CalendarPage.tsx`. Добавить пункт в sidebar (видим только admin) — после «Графики работы», перед «Сотрудники».

**Структура:**

1. **Header**: «Производственный календарь {год}» + переключатель года (стрелки ←/→, по умолчанию текущий год)

2. **Карточка статуса календаря**:
   - Источник (badge: 🌐 «xmlcalendar.ru» если source='remote', 📝 «Загружен вручную» если source='manual')
   - Дата загрузки: «обновлён 2 дня назад»
   - Кнопка «🔄 Обновить с xmlcalendar.ru» (admin only)
   - Кнопка «📥 Импорт JSON» (admin only) — модал с textarea
   - Ссылка на xmlcalendar.ru (внешняя, target=_blank)

3. **Если календаря нет в БД**: большая жёлтая плашка
   - «Календарь {год} не загружен»
   - «Возможные причины: нет связи с xmlcalendar.ru, или год ещё не опубликован»
   - Две кнопки: «Попробовать загрузить» и «Импорт JSON»

4. **Метрики (4 карточки)** при наличии календаря:
   - Рабочих дней в году (workdays_total)
   - Сокращённых дней (short_days_total)
   - Выходных/праздничных (365 − workdays_total)
   - Норма часов при 8ч/смена (workdays_total × 8 − short_days_total)

5. **Сетка 12 месяцев** в формате 4×3 или 3×4. Каждый месяц — карточка:
   - Название месяца (Январь, Февраль...)
   - Сетка 7 столбцов (Пн-Вс), под ней дни
   - Цветовая кодировка: рабочий = серый текст на белом, сокращённый = жёлтый фон, праздник = красный фон
   - Tooltip при наведении: «Рабочий день» / «Сокращённый день (−1 час)» / «Праздник»

6. **Легенда** под сеткой (квадратик + подпись для каждого типа)

#### 12. Модал импорта

Внутри modal:
- Подпись: «Вставьте содержимое файла calendar.json от xmlcalendar.ru»
- Ссылка «Скачать с xmlcalendar.ru →»
- `<textarea>` минимум 200 символов высотой
- Кнопки «Отмена» / «Импортировать»
- При сабмите: парсим JSON, валидируем структуру, POST на /api/calendar/import
- При ошибке парсинга — красный текст под textarea

#### 13. Дашборд

На дашборде admin добавить плитку «Производственный календарь» (после Графиков, перед Сотрудниками).

#### 14. Не делаем сейчас

- Интеграцию с табелем (задача 3.x)
- Календари других стран
- Региональные особенности
- Редактирование отдельных дней через UI (только импорт всего месяца через JSON)

### Часть В — общее

#### 15. CLAUDE.md

Добавить раздел «Производственный календарь»:
- Источник: xmlcalendar.ru
- Norm = workdays × hours_per_shift − short_days
- Все вычисления норм только через `app.services.calendar`, не дублировать формулу в роутерах
- В тестах обязательно мокать `httpx` — никаких реальных запросов

#### 16. Коммиты

- `feat(db): production_calendars table`
- `feat(backend): calendar parsing service`
- `feat(backend): calendar loading from xmlcalendar.ru with manual import fallback`
- `feat(backend): calendar API endpoints with lifespan autoload`
- `feat(frontend): production calendar page with monthly grid`

Запушить на main.

## Acceptance criteria

```bash
# Тесты
pytest -v
# Все зелёные. Если сети нет — всё равно зелёные (моки)

# Миграция
alembic upgrade head
# В БД появляется таблица production_calendars

# Запуск сервера
uvicorn app.main:app --reload
# В логах:
# INFO: Auto-loaded calendar 2026
# INFO: Auto-loaded calendar 2027 (или warning если сеть отказала)

# Endpoint
curl http://localhost:8000/api/calendar/2026 -H "Authorization: Bearer <TOKEN>"
# вернёт JSON с year=2026, months[12]

curl http://localhost:8000/api/calendar/2026/5/summary -H "Authorization: Bearer <TOKEN>"
# вернёт workdays=19, short_days=1, norm_hours_8h=151, days[31]
```

В UI (под admin):

1. В sidebar появился пункт «Производственный календарь»
2. Открыв страницу — сетка 12 месяцев 2026 года с подсветкой майских праздников (1, 2, 3, 9, 10, 11, 16, 17, 23, 24, 30, 31 — красные), 8 мая — жёлтый
3. Метрики разумные (для 2026: workdays ≈ 247, short ≈ 5)
4. Переключение на 2027 — если remote доступен, подтянется автоматически
5. Кнопка «Обновить» работает (видна только admin)
6. Импорт JSON работает: вставляешь содержимое файла → создаётся/обновляется календарь

## Подводные камни

- **Не делать fetch при импорте модуля** — только при вызове функции / endpoint / lifespan. Иначе тесты будут зависеть от сети.
- В тестах **обязательно мокать httpx** через `monkeypatch.setattr` или `pytest-httpx`, иначе CI будет нестабильный.
- При парсинге days-строки **trim пробелы** между запятыми, на всякий случай (xmlcalendar их может вставлять при ручной правке).
- Год передаётся как **int**, не string — Pydantic-валидация.
- JSONB поле — на Postgres работает нативно, для SQLite в тестах нужен TypeDecorator (как в audit_log) или Tests могут использовать только Postgres-фикстуру.
- В `import_calendar`: если запись года уже есть — **обновлять**, не падать с unique violation. Upsert семантика.

## Что НЕ делать

- Не использовать табель — там его ещё нет
- Не делать редактирование отдельных дней через UI
- Не делать поддержку регионов / других стран
- Не делать кэширование на фронте (запрос дешёвый, бэк сам кэширует через БД)
- Не делать встроенные данные в коде (hardcoded years)

## В конце

1. Покажи `SELECT year, source, loaded_at FROM production_calendars;` через psql
2. Покажи скрин страницы «Производственный календарь» — должны быть видны 12 месяцев и подсветка
3. Прогон `pytest -v` (все зелёные)
4. Список новых файлов
