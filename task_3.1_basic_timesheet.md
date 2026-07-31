# Задача 3.1 — Табель: базовый ввод часов

## Контекст

Все справочники готовы (сотрудники, отделы, компании, графики), производственный календарь загружен. Сейчас добавляем сердце системы — табель: ввод часов с разбивкой по компаниям, помесячный просмотр, ролевая фильтрация.

В этой задаче — **только базовый функционал**: ввод и хранение часов. Workflow статусов (черновик→на проверке→закрыт), расчёт ЗП, автозаполнение по графику, Т-13 коды, экспорт — отдельными задачами 3.2-3.6.

## Ключевая концепция: часы делятся по компаниям

Главная особенность системы: один сотрудник в один день может работать на несколько юрлиц компании, время между ними распределяется.

Пример:
- 1 мая 2026, Иванов: ООО Альфа — 4 часа, ООО Бета — 4 часа (всего 8)
- 2 мая, Иванов: ООО Альфа — 8 часов
- 3 мая, Иванов: ООО Гамма — 8 часов

В UI табеля **одна строка = один сотрудник × одна компания**. На каждого сотрудника отрисовывается N строк (по числу активных компаний). Часы вводятся в нужную строку компании.

## Что нужно сделать

### Часть А — бэкенд

#### 1. Модель TimesheetEntry

`app/models/timesheet_entries.py`:

```python
class TimesheetEntry(Base):
    __tablename__ = "timesheet_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    work_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)  # 0.5 шаг, до 99.99
    # created_at, updated_at стандартные
    
    employee: Mapped["Employee"] = relationship(back_populates="timesheet_entries")
    company: Mapped["Company"] = relationship()
    
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", "company_id", name="uq_timesheet_employee_date_company"),
        CheckConstraint("hours >= 0 AND hours <= 24", name="ck_timesheet_hours_range"),
    )
```

В `Employee` добавить обратную связь:
```python
timesheet_entries: Mapped[list["TimesheetEntry"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
```

Композитный индекс `(employee_id, work_date)` для быстрых запросов по сотруднику за период.

Если hours=0 — запись **не должна существовать** (логически "нет часов" = нет ячейки). Сервис при сохранении ячейки удаляет её если hours=0.

#### 2. Миграция

`alembic revision --autogenerate -m "create timesheet entries table"` → `alembic upgrade head`. Проверить что unique constraint и check constraint в миграции присутствуют.

#### 3. Pydantic-схемы

`app/schemas/timesheet.py`:

```python
class TimesheetEntryRead(BaseModel):
    employee_id: int
    work_date: date
    company_id: int
    hours: Decimal
    model_config = {"from_attributes": True}

class TimesheetCellInput(BaseModel):
    employee_id: int
    work_date: date
    company_id: int
    hours: Decimal = Field(ge=0, le=24)  # 0 = удалить ячейку

class TimesheetMonthQuery(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    department_id: int | None = None  # для admin/accountant; manager игнорирует

class TimesheetMonthResponse(BaseModel):
    year: int
    month: int
    employees: list[EmployeeRead]  # активные сотрудники в периоде (по фильтру)
    companies: list[CompanyRead]   # активные компании
    entries: list[TimesheetEntryRead]  # все ячейки за период по выбранным сотрудникам
```

#### 4. Сервис

`app/services/timesheet.py`:

```python
def get_month_entries(db, employees: list[Employee], year: int, month: int) -> list[TimesheetEntry]:
    """Все entries за месяц для указанных сотрудников. Один запрос с JOIN."""

def upsert_cell(db, actor: Employee, employee_id: int, work_date: date, company_id: int, hours: Decimal) -> TimesheetEntry | None:
    """
    Сохраняет ячейку:
    - Если hours == 0 → удаляет существующую запись (если есть), возвращает None
    - Иначе создаёт или обновляет запись
    - Пишет в audit_log: entity_type='timesheet_entry', action='create'/'update'/'delete'
    - before/after — old hours / new hours
    Returns: TimesheetEntry или None
    """

def visible_employees_for_actor(db, actor: Employee, department_id: int | None = None) -> list[Employee]:
    """
    Возвращает список сотрудников видимых актору:
    - admin / accountant: все активные (с фильтром по department_id если указан)
    - manager: только своего department_id (department_id из query игнорируется или должен совпадать)
    - employee: только себя
    Returns: список Employee с has_access=True или без — все активные сотрудники компании
    """
```

#### 5. Роутер

`app/routers/timesheet.py` префикс `/api/timesheet`:

- `GET /api/timesheet/{year}/{month}` — табель за месяц.
  - Query: `department_id?` (опц., для admin/accountant фильтр; для manager игнорируется)
  - Возвращает `TimesheetMonthResponse`
  - Видимость:
    - admin/accountant: видят всех или фильтр по department_id
    - manager: только свой department_id (даже если query другой)
    - employee: видит только свои entries (но employees list содержит только его)
  - Календарь не возвращается — фронт сам зовёт `/api/calendar/{year}/{month}/summary` отдельно

- `PUT /api/timesheet/cell` — сохранение ячейки.
  - Body: `TimesheetCellInput`
  - Права: admin/accountant — любой employee; manager — только своего department; employee — только себя
  - При нарушении — 403
  - Возвращает обновлённую `TimesheetEntryRead` или `null` если удалили (hours=0)

- `POST /api/timesheet/cells/batch` — пакетное сохранение (на случай если фронту нужно отправить много изменений сразу).
  - Body: `{ entries: list[TimesheetCellInput] }`
  - Транзакционно: либо все, либо ни одной
  - Возвращает: `{ entries: list[TimesheetEntryRead | null] }` в том же порядке

Аудит каждой ячейки идёт через `upsert_cell`. Для batch — один общий transaction, но отдельные audit-записи на каждую ячейку.

#### 6. Тесты

`tests/test_timesheet.py` — 15+ кейсов:

**Доступы:**
- admin сохраняет ячейку любому сотруднику → 200
- manager сохраняет ячейку своему department → 200
- manager сохраняет ячейку чужому department → 403
- employee сохраняет ячейку себе → 200
- employee сохраняет ячейку другому → 403
- неавторизованный → 401

**Логика хранения:**
- Создание ячейки (hours=8) → запись появилась
- Обновление существующей (hours=4) → старая обновлена, дубля нет
- Удаление (hours=0) → запись удалена, в БД нет
- Несколько компаний в один день → несколько записей, сумма по дню корректна

**Валидация:**
- hours=25 → 422
- hours=-1 → 422
- несуществующий employee_id → 404 или 400 (FK violation handled)
- несуществующий company_id → 404 или 400

**Чтение:**
- GET за месяц возвращает все ячейки активных сотрудников
- Manager видит только свой department в списке employees
- Фильтр по department_id для admin работает

**Audit log:**
- При создании ячейки появляется запись в audit_log
- При обновлении — запись с before/after
- При удалении (hours=0) — запись с action='delete'

**Batch:**
- 3 ячейки в одном запросе → все 3 сохранены
- Если одна невалидна — транзакция откатывается полностью (ни одна не сохранена)

### Часть Б — фронтенд

#### 7. Типы

`frontend/src/types/api.ts`:

```typescript
export interface TimesheetEntry {
  employee_id: number;
  work_date: string;  // YYYY-MM-DD
  company_id: number;
  hours: number;  // decimal as number
}

export interface TimesheetMonthResponse {
  year: number;
  month: number;
  employees: Employee[];
  companies: Company[];
  entries: TimesheetEntry[];
}

export interface TimesheetCellInput {
  employee_id: number;
  work_date: string;
  company_id: number;
  hours: number;
}
```

#### 8. API-клиент

`frontend/src/api/timesheet.ts`:

```typescript
getMonth(year: number, month: number, departmentId?: number): Promise<TimesheetMonthResponse>
saveCell(input: TimesheetCellInput): Promise<TimesheetEntry | null>
saveCellsBatch(entries: TimesheetCellInput[]): Promise<(TimesheetEntry | null)[]>
```

#### 9. Страница табеля

`frontend/src/pages/TimesheetPage.tsx`. В sidebar — пункт **«Табель»** в группе «Учёт» (вместе с Дашбордом).

**Доступ:** admin, accountant, manager, employee — все могут зайти, контент фильтруется бэком.

**Структура страницы:**

1. **Header** (фиксированный):
   - Заголовок «Табель»
   - Переключатель месяца: ← Май 2026 →
   - Для admin/accountant — селект «Отдел: Все / Дирекция / ...»
   - Кнопки справа (заглушки, в следующих задачах добавим функционал):
     - «Сохранить» (если есть несохранённые изменения)
     - «Сводка» (toggleable, в задаче 3.3)

2. **Сетка табеля** — главное:
   - Колонки слева (sticky): Сотрудник, Компания
   - Колонки по дням 1..31 (или 28/30 в зависимости от месяца)
   - Колонка справа (sticky): Часов (итого за месяц по сотруднику)
   - Строки: одна на сочетание (сотрудник, компания). На каждого сотрудника — N строк по числу активных компаний
   - Между сотрудниками — тонкий разделитель (border-bottom потолще или микро-полоска)

3. **Подсветка дней** из календаря:
   - Заголовок столбца дня — `1 Пт`, цвет фона:
     - Праздник — красный (`bg-red-50 text-red-700`)
     - Сокращённый — жёлтый (`bg-yellow-50 text-yellow-700`)
     - Рабочий — нейтральный
   - Ячейки в столбце наследуют фон заголовка (полупрозрачно)

4. **Цвет компании**:
   - Каждой компании — свой цвет (палитра из 8 цветов, ассайнить по индексу компании в массиве)
   - Фон ячейки компании в строке = цвет компании
   - Названия компаний и их цвета вынести в утилиту `companyColor(id, allCompanies)` → `{ bg: string, text: string }`

5. **Ввод часов**:
   - Каждая ячейка дня — input type="number" min=0 max=24 step=0.5
   - Пустая ячейка = нет часов
   - При вводе нового значения — onBlur вызывает saveCell с этим значением
   - При вводе 0 или очистке — saveCell с hours=0 (бэк удалит)
   - Optimistic update: значение в UI меняется сразу, потом подтверждается ответом сервера. При ошибке — откат + toast

6. **Итог за месяц** в правой колонке:
   - Сумма всех часов сотрудника за месяц (по всем компаниям)
   - Отображается в первой строке блока сотрудника (rowspan=число_компаний)

7. **Empty state**:
   - Если в отделе нет сотрудников — «Нет сотрудников в отделе»
   - Если нет активных компаний — «Не настроены компании, обратитесь к админу»

#### 10. UX-нюансы

- **Tab между ячейками** должен работать естественно — по строке слева направо
- **Enter** — переход на следующую строку (тот же день)
- **Esc** — отмена редактирования, вернуть прежнее значение
- При сохранении ячейки — крутящийся индикатор в правом нижнем углу страницы (не блокирующий)
- Несколько ячеек одновременно изменены и сохранены — нет проблемы (каждая saveCell независимо)
- При ошибке (например 403) — toast с понятным сообщением, значение откатывается

#### 11. Производительность

- Загрузка табеля — один запрос за месяц, не запрос на каждую ячейку
- При сохранении — отдельный запрос (не пытаться батчить автоматически, это сложно для UX). Batch endpoint оставляем для будущего «копировать из прошлого месяца» и т.п.
- Calendar grid рендерится 1 раз, не пересчитывается при каждом keystroke

#### 12. Не делаем в этой задаче

- Сводки/итогов по компаниям (это 3.3)
- Колонок «Норма», «Δ», «Оклад», «Переработка» (это 3.3)
- Статусов периода и кнопок «Отправить на проверку» (это 3.2)
- Автозаполнения по графику (это 3.4)
- Выбора кода Т-13 в ячейке (это 3.5)
- Экспорта (это 3.6)
- Копирования из прошлого месяца

### Часть В — общее

#### 13. Дашборд

На дашборд admin/manager/accountant добавить плитку **«Табель»** (первой в списке).

#### 14. CLAUDE.md

Добавить раздел «Timesheet»:
- Структура: одна запись = (employee, work_date, company, hours)
- hours=0 не существует, удаляется из БД при сохранении
- Все мутации через `upsert_cell` сервиса (audit log там)
- Фильтрация по department_id на бэке принудительная для manager
- Один сотрудник может иметь несколько ячеек в один день на разные компании

#### 15. Коммиты

- `feat(db): timesheet_entries table`
- `feat(backend): timesheet service with upsert and role visibility`
- `feat(backend): timesheet API endpoints`
- `feat(frontend): timesheet page with day×employee×company grid`

Запушить на main.

## Acceptance criteria

```bash
# Тесты
pytest -v
# 80+ зелёных (было 65 + ~15 новых)

# Миграция
alembic upgrade head
# Появилась таблица timesheet_entries
```

В UI (admin):

1. В sidebar появился пункт «Табель» (в группе «Учёт»)
2. Открываем — видим текущий месяц (например май 2026)
3. Праздники подсвечены красным в заголовках дней (1, 2, 3, 9, 10, 11, 16, 17, 23, 24, 30, 31), 8 мая жёлтым (сокращённый)
4. Видим всех активных сотрудников, каждого с N строками компаний
5. Каждая строка компании — со своим цветом фона
6. Можно вписать в ячейку «8» → onBlur → сохраняется (видна в БД при перезагрузке)
7. В правой колонке появляется сумма часов
8. Можно вписать на тот же день другую компанию — обе ячейки сохраняются, обе видны
9. Очистить ячейку (вписать 0 или удалить значение) → запись удаляется
10. Перевести стрелкой ← на апрель → видим апрельские данные (или пустоту если ничего не вводили)
11. Переключатель отдела работает (admin видит фильтрацию)

Для manager:
- Заходит, видит **только свой отдел** (даже если в URL `?department_id=X` другого отдела)
- Селект отделов не отображается (видит только свой)

Для employee:
- Видит **только свои строки**
- Можно вписать часы себе, сохраняется

## Подводные камни

- **Decimal vs float на фронте**: Pydantic Decimal сериализуется как строка `"8.00"` или число — проверить что фронт корректно парсит. Безопасно — `parseFloat(entry.hours as any)` или сразу `number` в схеме.
- **Часовые пояса**: work_date — это просто дата (date, без времени). Никаких timezone преобразований.
- **Параллельные сохранения**: если два пользователя редактируют одну ячейку — последний выиграет. Это OK для MVP, конфликты решаем audit log'ом.
- **Empty cell vs zero**: 0 в БД не хранится. Пустая ячейка в UI = нет записи. При сохранении 0 — отправляем запрос с hours=0, бэк удаляет.
- **При компании с is_active=False** — не отображать её строки в новых периодах. Но если в прошлых периодах часы были — отображать строку с грейед-фоном (employee менял компании). Для MVP — просто не показывать неактивные. Detail можно дополнить позже.
- **Composite unique constraint** — добавить в миграцию обязательно, иначе race condition.

## В конце

1. Покажи скрин табеля с введёнными часами на 2-3 сотрудников и 2 компании (мультикомпания на одного сотрудника)
2. Покажи `SELECT * FROM timesheet_entries LIMIT 10;` через psql
3. Покажи результат `pytest -v` (80+ green)
