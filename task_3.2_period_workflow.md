# Задача 3.2 — Workflow закрытия периода

## Контекст

Базовый табель работает: можно вводить и редактировать часы. Сейчас добавляем workflow закрытия периода: каждый отдел проходит цикл **Черновик → На проверке → Закрыт**. Это обеспечивает контроль над данными — бухгалтер видит когда руководитель готов, утверждает, и после закрытия правки идут только через админа с обязательным комментарием.

## Бизнес-правила

### Сущность периода

Статус хранится **на каждое сочетание (отдел × год × месяц)**. То есть:
- Май 2026 — ИТО: Closed
- Май 2026 — Стройка: Pending Review
- Май 2026 — Бухгалтерия: Draft
- Май 2026 — «Без отдела» (NULL): Closed

Это отдельные независимые периоды.

### Состояния

| Статус | Кто видит | Кто правит часы | Кто меняет статус |
|---|---|---|---|
| `draft` | Все по своему доступу | Manager (свой отдел), Admin, Employee (себя в своём отделе) | Manager → отправить на проверку |
| `pending_review` | Все по своему доступу | НИКТО | Accountant → закрыть; Accountant → вернуть в Draft |
| `closed` | Все по своему доступу | НИКТО (только через Admin reopen) | Admin → переоткрыть |

### Возвраты

- `pending_review → draft` — только Accountant, **обязателен комментарий «причина возврата»**, audit log
- `closed → draft` — только Admin (любой не только системный), **обязателен комментарий**, audit log

### Особенность: «Без отдела»

У сотрудников с `department_id IS NULL` (топ-менеджмент, системный admin) **нет руководителя**. Период для них:
- Группируется виртуально под `department_id = NULL`
- Логически у них **нет состояния `draft` от manager-а**, потому что менеджера нет
- **Бухгалтер сразу закрывает** этот период (одной кнопкой — пропуская pending_review)
- Может вернуть в draft (но тогда правит только admin, не manager — менеджера-то нет)

В UI для этой группы:
- Отображается как отдельный «отдел» с названием «Без отдела»
- Бухгалтер видит кнопку «Закрыть» (вместо «Утвердить»)
- В draft часы правит admin или сам сотрудник

## Что нужно сделать

### Часть А — бэкенд

#### 1. Модель TimesheetPeriod

`app/models/timesheet_periods.py`:

```python
class TimesheetPeriod(Base):
    __tablename__ = "timesheet_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    # draft | pending_review | closed
    
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    
    # created_at, updated_at стандартные
    
    department: Mapped["Department | None"] = relationship()
    submitted_by: Mapped["Employee | None"] = relationship(foreign_keys=[submitted_by_id])
    reviewed_by: Mapped["Employee | None"] = relationship(foreign_keys=[reviewed_by_id])
    closed_by: Mapped["Employee | None"] = relationship(foreign_keys=[closed_by_id])
    
    __table_args__ = (
        # уникальность: один период на (department_id, year, month)
        # NULL в department_id для группы "без отдела" — но PG считает NULL != NULL,
        # поэтому добавляем partial unique index ниже отдельно
        Index("ix_period_department_year_month", "department_id", "year", "month"),
        CheckConstraint("month >= 1 AND month <= 12", name="ck_period_month_range"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="ck_period_year_range"),
        CheckConstraint("status IN ('draft', 'pending_review', 'closed')", name="ck_period_status"),
    )
```

В миграции добавить **два partial unique index** вручную через `op.execute`:
```python
op.execute("""
    CREATE UNIQUE INDEX uq_period_dept_year_month
    ON timesheet_periods (department_id, year, month)
    WHERE department_id IS NOT NULL
""")
op.execute("""
    CREATE UNIQUE INDEX uq_period_null_dept_year_month
    ON timesheet_periods (year, month)
    WHERE department_id IS NULL
""")
```

Это даст уникальность для (department_id, year, month) когда department_id NOT NULL, и отдельную уникальность для «без отдела».

#### 2. Миграция

`alembic revision --autogenerate -m "create timesheet periods"` → ручная правка миграции для partial indexes → `alembic upgrade head`.

#### 3. Сервис

`app/services/timesheet_periods.py`:

```python
def get_or_create_period(db, department_id: int | None, year: int, month: int) -> TimesheetPeriod:
    """
    Возвращает существующий период или создаёт новый со status='draft'.
    Идемпотентно. Atomic upsert.
    """

def can_edit_cells(period: TimesheetPeriod) -> bool:
    """True если status='draft'."""

def submit_for_review(db, period: TimesheetPeriod, actor: Employee) -> TimesheetPeriod:
    """
    draft → pending_review. Только manager своего отдела или admin.
    Если department_id IS NULL — нельзя (там нет manager-а).
    Audit log: action='period_submitted'.
    """

def return_to_draft(db, period: TimesheetPeriod, actor: Employee, reason: str) -> TimesheetPeriod:
    """
    pending_review → draft. Только accountant или admin.
    reason обязателен (бросать ValueError если пустой).
    Audit log: action='period_returned', с reason.
    """

def close_period(db, period: TimesheetPeriod, actor: Employee) -> TimesheetPeriod:
    """
    pending_review → closed. Только accountant или admin.
    Для department_id IS NULL: разрешено сразу из draft (минуя pending_review).
    Audit log: action='period_closed'.
    """

def reopen_period(db, period: TimesheetPeriod, actor: Employee, reason: str) -> TimesheetPeriod:
    """
    closed → draft. Только admin (НЕ accountant).
    reason обязателен.
    Audit log: action='period_reopened', с reason.
    """
```

В `app/services/timesheet.py` обновить `upsert_cell`:
- Перед сохранением проверяем статус периода для (employee.department_id, year, month работника)
- Если статус не draft — бросать `PeriodLockedException`
- Это работает и для admin (admin тоже не может править закрытый период, пока не reopen)
- Если периода нет — `get_or_create_period` создаёт draft

#### 4. Pydantic-схемы

`app/schemas/timesheet_period.py`:

```python
PeriodStatus = Literal["draft", "pending_review", "closed"]

class TimesheetPeriodRead(BaseModel):
    id: int
    department_id: int | None
    department_name: str | None  # из join, или "Без отдела" если null
    year: int
    month: int
    status: PeriodStatus
    submitted_at: datetime | None
    submitted_by_name: str | None
    reviewed_at: datetime | None
    reviewed_by_name: str | None
    closed_at: datetime | None
    closed_by_name: str | None
    can_edit: bool  # для текущего пользователя: может ли он редактировать ячейки в этом периоде
    can_submit: bool
    can_close: bool
    can_return: bool
    can_reopen: bool

class StatusChangeReason(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
```

Обновить `TimesheetMonthResponse`:
```python
class TimesheetMonthResponse(BaseModel):
    year: int
    month: int
    employees: list[EmployeeRead]
    companies: list[CompanyRead]
    entries: list[TimesheetEntryRead]
    periods: list[TimesheetPeriodRead]  # ⬅️ НОВОЕ. По одному периоду на каждый department в видимости + один для NULL если есть сотрудники без отдела
```

#### 5. Роутер

`app/routers/timesheet.py` — добавить эндпойнты:

- `POST /api/timesheet/periods/{period_id}/submit` — отправить на проверку.
  - Тело пустое
  - Права: manager своего отдела или admin
  - 422 если статус не draft, или department_id IS NULL
  - Возвращает обновлённый `TimesheetPeriodRead`
  - Audit log

- `POST /api/timesheet/periods/{period_id}/return` — вернуть в draft.
  - Тело: `StatusChangeReason`
  - Права: accountant или admin
  - 422 если статус не pending_review
  - Audit log с reason

- `POST /api/timesheet/periods/{period_id}/close` — закрыть.
  - Тело пустое
  - Права: accountant или admin
  - 422 если статус не pending_review (кроме случая department_id IS NULL, тогда можно из draft)
  - Audit log

- `POST /api/timesheet/periods/{period_id}/reopen` — переоткрыть закрытый.
  - Тело: `StatusChangeReason`
  - Права: admin (НЕ accountant)
  - 422 если статус не closed
  - Audit log с reason

Обновить существующий `GET /api/timesheet/{year}/{month}`:
- В ответе теперь есть `periods[]`
- Логика: для каждого department из ответа находим или создаём period (lazy). Плюс period для NULL-department если есть видимые сотрудники без отдела.

Обновить `PUT /api/timesheet/cell`:
- Если период не в draft — 409 «Период закрыт для редактирования, статус: {status}»
- Если admin пытается править closed — тоже 409 (надо сначала reopen, это намеренно — чтобы не было «случайных» правок)

#### 6. Тесты

`tests/test_timesheet_periods.py`:

**Создание и доступ:**
- Период создаётся автоматически при первом обращении к табелю
- Период для NULL-department создаётся отдельный

**Workflow Manager → Accountant → Closed:**
- Manager в своём отделе делает submit → status=pending_review, submitted_by заполнен
- Manager чужого отдела пытается submit → 403
- Accountant делает close → status=closed
- Accountant пытается close period другого отдела где не pending_review → 422 (или подходящий код)

**Возвраты:**
- Accountant с reason возвращает period в draft → ok
- Accountant без reason (пустой или короткий) → 422
- Manager пытается вернуть (не его права) → 403
- Admin reopens closed period с reason → ok, status=draft

**Защита ячеек:**
- В status=pending_review попытка PUT /cell → 409
- В status=closed попытка PUT /cell от admin → 409
- После reopen — admin может править ячейки

**NULL-department:**
- Бухгалтер для NULL-department нажимает close (минуя pending) → ok
- Manager не может submit для NULL-department → 422 (нет менеджера)

**Audit log:**
- При submit запись с action='period_submitted'
- При return запись с action='period_returned' и reason в before/after или metadata
- При close запись с action='period_closed'
- При reopen запись с action='period_reopened' и reason

### Часть Б — фронтенд

#### 7. Типы

`frontend/src/types/api.ts`:

```typescript
export type PeriodStatus = 'draft' | 'pending_review' | 'closed';

export interface TimesheetPeriod {
  id: number;
  department_id: number | null;
  department_name: string | null;
  year: number;
  month: number;
  status: PeriodStatus;
  submitted_at: string | null;
  submitted_by_name: string | null;
  reviewed_at: string | null;
  reviewed_by_name: string | null;
  closed_at: string | null;
  closed_by_name: string | null;
  can_edit: boolean;
  can_submit: boolean;
  can_close: boolean;
  can_return: boolean;
  can_reopen: boolean;
}
```

Расширить `TimesheetMonthResponse` полем `periods: TimesheetPeriod[]`.

#### 8. API-клиент

`frontend/src/api/timesheet.ts` добавить:

```typescript
submitPeriod(periodId: number): Promise<TimesheetPeriod>
closePeriod(periodId: number): Promise<TimesheetPeriod>
returnPeriod(periodId: number, reason: string): Promise<TimesheetPeriod>
reopenPeriod(periodId: number, reason: string): Promise<TimesheetPeriod>
```

#### 9. UI табеля — статус-панель

Над сеткой табеля (между header и сеткой) — **панель статусов**:

Если в табеле виден один отдел (manager или фильтр admin/accountant):
- Одна большая карточка статуса:
  - Бейдж с цветом: draft (серый «Черновик»), pending_review (жёлтый «На проверке»), closed (зелёный «Закрыт»)
  - Под бейджем — детали: «Отправлено 28 мая Бублий А.В.», «Утверждено 30 мая Иванова О.П.» и т.д.
  - Справа — кнопки действий по правам пользователя:
    - Manager в draft: «Отправить на проверку»
    - Accountant в pending_review: «Утвердить» (primary) и «Вернуть на доработку» (secondary)
    - Admin в closed: «Переоткрыть для правок»
    - Accountant в draft для NULL-department: «Закрыть» (специальная кнопка)

Если в табеле видны несколько отделов (admin/accountant без фильтра):
- Сворачиваемый список карточек статусов всех видимых отделов
- Можно кликнуть «Только этот отдел» в карточке — отфильтровать таблицу

Бейдж статуса показывать также в шапке таблицы рядом с названием отдела (если несколько).

#### 10. Модал «Причина возврата» / «Причина переоткрытия»

При нажатии «Вернуть на доработку» или «Переоткрыть» — модал:
- Текст-поле «Причина» (textarea, min 3 символа, max 500)
- Кнопки «Отмена» / «Подтвердить»
- При отправке — вызов API, тост успеха/ошибки, рефреш данных

#### 11. UI ячеек — блокировка при не-draft

- Если период не в draft — `<input>` ячейки становится **disabled** и красится в `bg-gray-50 text-gray-500 cursor-not-allowed`
- При попытке клика — tooltip «Период закрыт для редактирования»
- На сетке поверх ячеек прозрачный hover-overlay показывающий «🔒 Период {status}»

#### 12. История периода (мини-журнал)

В карточке статуса — раскрывающийся блок «История» с записями:
- 28 мая 14:23 — Бублий А.В. отправил на проверку
- 30 мая 10:15 — Иванова О.П. вернула на доработку: «не сошёлся итог»
- 30 мая 16:40 — Бублий А.В. снова отправил на проверку
- 31 мая 09:00 — Иванова О.П. утвердила (закрыто)

Источник данных — Audit log с фильтром по entity_type='timesheet_period', entity_id={period.id}. Добавить отдельный эндпойнт:

`GET /api/timesheet/periods/{period_id}/history` — возвращает упорядоченный список audit-записей по этому периоду. Тоже добавить тесты.

#### 13. Empty state и edge cases

- Если в видимости нет сотрудников «без отдела» — карточку для NULL-department не показывать
- Если month/year выбран в будущем — все статусы draft, без кнопок (всё равно нечего отправлять/закрывать, нет часов)
- Если попытались сохранить ячейку и пришёл 409 — тост «Период закрыт, обновите страницу»

### Часть В — общее

#### 14. CLAUDE.md

Добавить раздел «Timesheet Periods»:
- Workflow: draft → pending_review → closed
- Возвраты через reason, обязательный audit log
- Период привязан к (department_id, year, month). NULL-department — отдельная группа.
- Ячейки можно править только в draft. Admin тоже подчиняется этому правилу — должен reopen чтобы править.
- Period создаётся lazy (по обращению к табелю)

#### 15. Коммиты

- `feat(db): timesheet_periods table with partial unique indexes`
- `feat(backend): period workflow service and endpoints`
- `feat(backend): protect cell editing by period status`
- `feat(frontend): period status panel and workflow actions`
- `feat(frontend): period history view from audit log`

## Acceptance criteria

```bash
pytest -v
# 95+ зелёных (было 80 + ~15 новых)
```

В UI:

1. **Admin создаёт нескольких сотрудников в разных отделах**, выдаёт manager-у доступ к своему отделу
2. **Manager** входит в свой отдел в табеле:
   - Видит статус «Черновик»
   - Заполняет часы
   - Жмёт «Отправить на проверку» — статус становится «На проверке»
   - Часы становятся disabled
3. **Accountant** заходит в табель, видит несколько отделов:
   - У отдела Бублия — статус «На проверке», кнопка «Утвердить»
   - У других отделов — «Черновик»
4. **Accountant** жмёт «Вернуть на доработку», в модале пишет причину, отправляет — статус снова «Черновик»
5. **Manager** видит уведомление (через рефреш — лайв нет), правит часы, опять отправляет
6. **Accountant** утверждает — статус «Закрыт»
7. Никто (даже admin) не может править закрытые часы напрямую
8. **Admin** жмёт «Переоткрыть», вводит причину — статус снова «Черновик», часы доступны для редактирования
9. **Для сотрудников без отдела** (admin@example.com) — отдельная группа, у Accountant сразу кнопка «Закрыть»
10. **В истории периода** видны все переходы статусов с авторами и временем

## Подводные камни

- **NULL в unique constraint**: Postgres считает NULL != NULL. Без partial index можно создать 100 периодов «без отдела» на один месяц. Поэтому **обязательно** partial index в миграции.
- **«can_edit» вычисляется на бэке** — фронт не должен решать сам. Бэк возвращает `can_edit: bool` в каждом TimesheetPeriodRead, фронт просто отображает.
- **Lazy create periods**: при первом GET месяца создавать недостающие периоды для всех видимых департаментов. Без этого manager не сможет нажать submit (нет периода).
- **Атомарность смены статуса**: использовать `with_for_update()` или просто проверку в той же транзакции — чтобы две параллельные кнопки «Утвердить» не сработали дважды.
- **Существующие entries**: они остаются в БД при reopen/return — статус меняется только у периода. Ничего не дублируется, ничего не теряется.
- **employee.department_id может измениться**: если сотрудник перешёл из одного отдела в другой, его старые часы остаются в табеле, и их «закрывает» новый отдел при следующем закрытии. Это OK для MVP. Реальный edge case для отдельной задачи позже.

## Что НЕ делать

- Не делать уведомления (email/push) — это позже
- Не делать массовое «Закрыть все отделы» одной кнопкой — отдельно по отделам, как договорились
- Не делать комментарии к конкретным ячейкам (только к статусу периода)
- Не делать backdating (закрытие задним числом) — статусы текущие, время фиксируется now()
- Не делать смену department_id у сотрудника с автоматическим переносом часов — оставить on hold

## В конце

1. Скрин табеля manager-а со статусом «На проверке» (часы disabled)
2. Скрин табеля accountant-а с несколькими отделами и разными статусами
3. Скрин истории периода (мини-журнал)
4. `pytest -v` (95+ green)
