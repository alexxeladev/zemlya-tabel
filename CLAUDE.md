# CLAUDE.md — zemlya-tabel

## Что это за проект

Система учёта рабочего времени для девелоперской группы «Земля МО».
- Несколько юрлиц; зарплата сотрудника распределяется между ними.
- Ролевая модель: Admin, Manager, Accountant, Employee.
- Workflow периода: Draft → Pending Review → Closed.
- После Closed правки только Admin с обязательным комментарием и audit log.

## Стек

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL (psycopg)
- **Схемы:** Pydantic v2 + pydantic-settings
- **Auth:** JWT Bearer (python-jose), хеширование passlib[bcrypt]
- **Тесты:** pytest + httpx, SQLite in-memory для изоляции
- **Линтер:** ruff
- **Frontend:** React 18 + TypeScript + Vite, Tailwind CSS, Zustand (store), axios, react-router-dom, zod, recharts (графики дашборда)
- **Excel:** openpyxl (экспорт Т-13)

## Структура

```
backend/
  app/
    main.py         — FastAPI app, CORS, роутеры, /health, lifespan (автозагрузка календарей)
    config.py       — Pydantic Settings (DATABASE_URL, SECRET_KEY, CORS_ORIGINS, ...)
    database.py     — engine, SessionLocal, Base, get_db()
    cli.py          — CLI: create-admin, reset-password
    models/         — отдельной таблицы users НЕТ: auth-поля (email, role, ...) в Employee
      employees.py  — Employee (персональные + финансовые + auth-поля, is_system_admin)
      departments.py / companies.py / schedules.py
      timesheet_entries.py   — TimesheetEntry (employee, work_date, company, hours int)
      timesheet_periods.py   — TimesheetPeriod (workflow draft/pending_review/closed)
      production_calendars.py — ProductionCalendar (JSONB с xmlcalendar.ru)
      audit_log.py  — AuditLog (append-only)
    schemas/        — auth, employee, department, company, schedule, calendar,
                      timesheet, timesheet_period, payroll, dashboard
    routers/
      auth.py       — POST /api/auth/login, /auth/change-password, GET /api/auth/me
      employees.py  — CRUD /api/employees + dismiss/rehire + access/reset-password (admin)
      departments.py / companies.py / schedules.py — справочники (чтение: не-employee, CUD: admin)
      calendar.py   — /api/calendar: import, load, {year}, summary
      timesheet.py  — /api/timesheet: месяц, ячейки, периоды, autofill, payroll, export, tasks
      dashboard.py  — GET /api/dashboard/{year}/{month} (сводный, видимость по ролям в сервисе)
    core/
      security.py   — hash_password, verify_password, create_access_token, decode_token
      deps.py       — get_current_user, require_role(*roles)
      audit.py      — log_action(db, actor, entity_type, entity_id, action, ...)
    services/
      calendar.py   — производственный календарь (parsing, нормы, fetch с xmlcalendar.ru)
      timesheet.py  — visible_employees_for_actor, upsert_cell, autofill
      timesheet_periods.py — workflow периодов, tasks inbox
      payroll.py    — calculate_employee_payroll (чистая функция, Decimal)
      dashboard.py  — build_dashboard (агрегация поверх payroll, НЕ дублирует формулы)
      timesheet_export.py  — generate_t13_excel
  alembic/          — миграции
  tests/            — conftest (client, db_session SQLite, фикстуры ролей) + тесты по модулям
  pyproject.toml / alembic.ini / docker-compose.dev.yml / .env.example

frontend/
  src/
    api/            — axios-клиент (401-интерсептор) + модули по сущностям
    store/          — zustand: auth, toasts, timesheetView (режим табеля classic/company)
    routes/         — AppRouter (RoleRoute), PrivateRoute (must_change_password gate)
    pages/          — TimesheetPage (классический табель), TimesheetCompanyView (вид «по компаниям»),
                      TasksPage, DashboardPage, Login, ChangePassword
    pages/admin/    — Employees, Departments, Companies, Schedules, Calendar, Payroll
    components/ / hooks/ / layouts/ / types/
    utils/          — money.ts (formatMoney/formatHours), colors.ts (общая палитра компаний
                      и статусов — единая для чипов табеля и графиков дашборда)
```

## Команды

```bash
# БД (Docker)
cd backend && docker compose -f docker-compose.dev.yml up -d

# Установка
cd backend && pip install -e ".[dev]"

# Запуск
uvicorn app.main:app --reload

# Создать первого админа / сбросить пароль
python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
python -m app.cli reset-password --email admin@example.com --new-password newpass

# Миграции
alembic upgrade head
alembic current
alembic revision --autogenerate -m "describe change"

# Тесты
pytest
pytest -v --tb=short

# Frontend
cd frontend && npm install
npm run dev        # дев-сервер на :5173
npm run build      # tsc + vite build
```

## Конвенции

### Таблицы
- Имена: snake_case, **множественное число** (`employees`, `departments`, `timesheet_entries`)

### Pydantic-схемы
Три схемы на каждую сущность:
- `XxxBase` — общие поля
- `XxxCreate(XxxBase)` — поля для создания (пароль, foreign keys)
- `XxxRead(XxxBase)` — поля для чтения (id, created_at, вложенные объекты), `from_attributes=True`

### Миграции
- **Только через Alembic**, никогда `Base.metadata.create_all()` в проде.
- `alembic revision --autogenerate -m "короткое описание"` — создать миграцию.
- Коммитить файл миграции вместе с изменениями модели.
- В `alembic/env.py` — `import app.models` чтобы все модели были зарегистрированы в metadata.

### Soft delete
Пользователи (и другие сущности с FK из audit_log) **никогда не удаляются физически** — `is_active=False`.

### Audit log
- Вызывать `log_action(...)` из `app.core.audit` при каждом CRUD и смене статуса.
- `log_action` делает `db.add()` — коммит вызывается снаружи вместе с основным изменением.

### Роутеры
- Регистрировать в `app/main.py` через `app.include_router(...)`.
- Префикс `/api/...` (не `/api/v1` — версионирование добавим позже при необходимости).

## Подводные камни

### Email-адреса в тестах
Всегда использовать `@example.com` или `@example.org` (RFC 2606, зарезервированы для тестов).
**Никогда не использовать `.local`** — он зарезервирован под mDNS и отвергается валидатором `pydantic[email]` с ошибкой «special-use or reserved name».

### Права на файлы при инициализации проектов
Никогда не запускать `npm`, `pip` и другие пакетные менеджеры через `sudo`.
Если файлы всё же созданы от root — исправляется одной командой:
```bash
sudo chown -R <user>:<user> <папка>
```

### Миграции Alembic — всегда применять после создания
После создания файла миграции **обязательно** применить её и проверить:
```bash
alembic upgrade head
alembic current  # должен совпадать с head
```
Миграция без применения создаёт расхождение между моделью SQLAlchemy и реальной схемой БД — API падает при старте или при первом обращении к изменённой таблице. Не коммитить миграцию без прогона `upgrade head`.

## Производственный календарь

- **Источник:** xmlcalendar.ru — `https://xmlcalendar.ru/data/ru/{year}/calendar.json`
- **Формула нормы:** `Норма = workdays × hours_per_shift − short_days`
- Все вычисления норм — только через `app.services.calendar`, не дублировать формулу в роутерах.
- В тестах **обязательно мокать** `app.services.calendar.fetch_calendar_from_remote` через `monkeypatch` — никаких реальных сетевых запросов.
- Тип дня в строке `days`: обычное число → выходной/праздник, `N+` → праздничный (тоже нерабочий), `N*` → сокращённый рабочий.

## Переменные окружения

| Переменная                  | Описание                              | Default                              |
|-----------------------------|---------------------------------------|--------------------------------------|
| DATABASE_URL                | postgresql+psycopg://...              | tabel:tabel@localhost:5432/tabel     |
| SECRET_KEY                  | Случайная строка для JWT              | change-me                            |
| ACCESS_TOKEN_EXPIRE_MINUTES | Время жизни токена (минуты)           | 480 (8 ч)                            |
| DEBUG                       | Включить SQL echo                     | false                                |
| CORS_ORIGINS                | Разрешённые origins, через запятую    | http://localhost:5173                |

## Timesheet

- Одна запись = (employee, work_date, company, hours)
- `hours=0` не существует — при сохранении с hours=0 запись удаляется из БД
- Все мутации только через `upsert_cell` сервиса (audit log там)
- Фильтрация по department_id на бэке принудительная для manager
- Один сотрудник может иметь несколько ячеек в один день на разные компании
- Unique constraint: `(employee_id, work_date, company_id)`
- **Системные пользователи (is_system_admin=True) скрыты из табеля** — никогда не попадают в выдачу
- Компании сотрудника в табеле: только default_company по умолчанию + те, где есть часы. Бэк отдаёт `extra_companies_by_employee: dict[int, list[int]]`. UI хранит «expanded companies» в локальном state (не zustand), загружает с сервера при старте.

## Два вида табеля (задача 3.10)

- Тумблер в шапке: **«Классический»** (`TimesheetPage`, одна строка/сотрудник, слоты компаний в ячейке дня) и **«По компаниям»** (`TimesheetCompanyView`, сотрудник = N строк по компаниям). Выбор в zustand `store/timesheetView` (НЕ localStorage — в окружении ненадёжен), не сбрасывается при смене месяца. По умолчанию — классический.
- Оба вида используют **одни и те же данные** с бэка (`GET /api/timesheet/{y}/{m}?include_payroll=`), отличается только рендеринг. `TimesheetPage` — владелец данных, передаёт их и колбэки в `TimesheetCompanyView` пропсами.
- Вид «по компаниям»: emp-колонки (ФИО/Отдел/График слева; Итого Ч/Итого ₽/Δ/Норма справа) объединены через **rowspan** на первой строке сотрудника. Колонки компании (дни, Итого Ч компании, Оклад/Сверхур.Ч/Празд.Ч/Сверхур.₽/Празд.₽) — на каждой строке. Финансы видят только admin/accountant/manager.
- Строки сотрудника: родительская (`default_company`, помечена «осн.», удалить нельзя) + где есть часы + добавленные вручную. Кнопка «+» (справа от чипа последней компании) и «×» (убрать доп. строку с 0 часов) — **только в draft** (как и редактирование часов в классике).
- Sticky слева в обоих видах: ФИО/Отдел/График (+ Компания в новом виде) — фикс. ширины + накопленные смещения (`stickyLeft()` в `TimesheetCompanyView`), иначе sticky разъезжается.

## Autofill (автозаполнение по графику)

- Эндпоинты: `POST /api/timesheet/autofill/preview` (preview без изменений), `POST /api/timesheet/autofill/apply` (применить)
- Работает только для графиков `schedule_type="standard"` (5/2 по производственному календарю)
- Графики `schedule_type="shift"` (2/2, 3/3) — пропускаются с причиной (TODO задача 3.4)
- Существующие ячейки НЕ перезаписываются
- 422 если нет производственного календаря или нет ни одного draft-периода среди видимых сотрудников

## Employee lifecycle (жизненный цикл сотрудника)

- Увольнение: `POST /api/employees/{id}/dismiss` (body: `{dismissal_date: date}`) → is_active=False, dismissal_date установлен
- Возврат: `POST /api/employees/{id}/rehire` → is_active=True, dismissal_date=NULL
- Физическое удаление (`DELETE /api/employees/{id}`) оставлено для редких случаев
- Видимость в табеле: исключаются только те у кого `dismissal_date IS NOT NULL AND dismissal_date < date(year, month, 1)`. Таким образом уволенные в середине месяца видны в этом месяце
- Часы и история сохраняются при увольнении (каскада нет)
- is_active=False → login невозможен (auth endpoint возвращает 403)

## Timesheet Periods

- Workflow: `draft → pending_review → closed`; возвраты: `pending_review → draft` (accountant+admin), `closed → draft` (только admin)
- Все возвраты и переоткрытие — обязательный `reason` (≥3 символа), audit log
- Период привязан к `(department_id, year, month)`. NULL department_id — отдельная группа «Без отдела»
- Ячейки (cells) можно редактировать **только в draft**. Admin тоже подчиняется — должен reopen чтобы править
- Период создаётся lazy при первом GET месяца (`get_or_create_period`)
- NULL-department: accountant может закрыть сразу из draft (нет manager-а, pending_review пропускается)
- Partial unique index в PG: один период на (department_id, year, month), NULL обрабатывается отдельным index

## Payroll (расчёт ЗП)

- Считаем **брутто к начислению** — НДФЛ/страховые/премии/авансы — задача 1С
- **Только `Decimal`**, никаких `float`. Округление `ROUND_HALF_EVEN` до целых рублей.
- **Переработка строго по дням** (задача 3.9): для каждого рабочего дня дневная норма = `hours_per_shift` (в сокращённый день −1). `overtime_day = max(0, факт_дня − дневная_норма)`, переработка месяца = сумма по дням. Недоработка в один день НЕ уменьшает переработку другого.
- **База оклада** от зачётных будних часов: `base = rate × min(1, зачётные_будние / norm)`, где зачётные = сумма `min(факт_дня, дневная_норма)` по будням. Праздничные/выходные часы в базу и в переработку НЕ входят.
- `overtime = overtime_hours × (rate/norm) × 1.5`
- **Праздничные/выходные часы — отдельная категория**, оплата per-employee (задача 3.9): `weekend_pay_type = coefficient` → `holiday_hours × (rate/norm) × коэффициент` (0 = не платить, дефолт 1.5); `fixed_rate` → `holiday_hours × фикс_ставка` (не зависит от оклада). Поля `weekend_pay_type / weekend_coefficient / weekend_fixed_rate` в модели Employee.
- `norm_days` (рабочих дней по календарю) и `fact_days` (дней с часами) — справочные, в деньгах не участвуют.
- Норма только для `schedule_type="standard"`. Shift-графики → `is_calculable=False`.
- Распределение по компаниям (`CompanyBreakdown`): base/overtime ₽ — пропорционально часам; holiday ₽ — пропорционально праздничным часам по компании. Поля per-company: `hours`, `percent`, `overtime_hours`, `holiday_hours`, `base_amount`, `overtime_amount`, `holiday_amount`, `total`. `holiday_hours` — точные (где отработаны), `overtime_hours` — пропорционально часам (метод наибольших остатков, сумма = итог).
- **Сумма частей по компаниям сходится с итогом точно** — распределение через `_distribute_whole_rubles()` (метод наибольших остатков: floor долей + остаток рублей компаниям с наибольшими дробными остатками, тай-брейк по company_id). НЕ округлять доли независимо — даёт расхождение до ±N/2 руб. на категорию.
- Видят только admin / accountant / **manager** (свой отдел). На бэке принудительная проверка — игнорировать `?include_payroll=true` от employee.
- Эндпоинт: `GET /api/timesheet/{year}/{month}/payroll`, параметр `?include_payroll=true` у основного GET.
- Сервис: `app/services/payroll.py` — чистая функция `calculate_employee_payroll`, не лезет в БД.
- Фронт: Decimal с бэка приходят как строки; `formatMoney()` / `formatHours()` в `frontend/src/utils/money.ts`.

## Экспорт

- **Excel (форма Т-13)** через openpyxl: `app/services/timesheet_export.py` → `generate_t13_excel(db, actor, year, month, department_id?) → bytes`
- Эндпоинт: `GET /api/timesheet/{year}/{month}/export/excel?department_id=N`
- Права: admin, accountant, manager (только свой отдел)
- Одна строка в таблице = один сотрудник × одна компания (у кого N компаний — N строк; ФИО/должность/таб.№ — merged cells по N строкам)
- Колонки (только часы, рублей нет): дни 1..N (+ итоги 1-15/16-конец), **Итого Ч компании** (per-row), **Сверхур. Ч** и **Праздн. Ч** по компании (per-row), **Итого Ч** сотрудника и **Норма** (merge через rowspan). Структура совпадает с видом «по компаниям» на экране, но без денег.
- Сотрудники без entries в периоде — пропускаются
- Праздники: красный фон (`FFFFCCCC`), сокращённые: жёлтый (`FFFFF2CC`)
- Название организации захардкожено: «ДЕВЕЛОПМЕНТ ГРУППА «ЗЕМЛЯ МО»» — потом вынесем в настройки
- **XML-экспорт не реализован** — ждёт требований 1С
- Финансовые данные в Excel НЕ выгружаются — только часы

## Dashboard

- Один комбинированный эндпойнт: `GET /api/dashboard/{year}/{month}` — часы, ФОТ (по отделам и юрлицам), статусы периодов, динамика за 6 месяцев. Видимость шьётся в сервисе по роли actor-а: admin/accountant — вся компания, manager — свой отдел (принудительно), employee — только свои часы (`payroll=None`, `periods=None`).
- Сервис `app/services/dashboard.py` агрегирует **поверх** `calculate_employee_payroll` и `visible_employees_for_actor` — формулы ЗП и правила видимости НЕ дублировать. Тест сверяет ФОТ дашборда с `/api/timesheet/{y}/{m}/payroll` — цифры обязаны совпадать.
- «Просрочено» = существующий период прошлых месяцев в статусе ≠ closed. Ограничение: lazy-создание периодов — месяц, который никто не открывал, в просрочку не попадёт.
- KPI `non_calculable_employees` — сколько сотрудников не вошло в расчёт ФОТ (нет оклада/графика, сменный график); фронт показывает предупреждение.
- Фронт: `DashboardPage` (recharts: BarChart/PieChart/LineChart, всё в ResponsiveContainer), данные одним запросом через `api/dashboard.ts`. Клик по отделу (строка статусов / столбец часов) → `/timesheet?year=&month=&department_id=`. Динамика при <2 точках — заглушка «Недостаточно данных».

## Правила работы

- Если задача говорит «не реализовывать X» — не реализовывать X. Если кажется что X всё же нужен — спросить пользователя, не делать тихо.

## Правило 3 попыток

Если ты столкнулся с проблемой которую не получается решить (тест падает, ошибка установки, синтаксическая ошибка которую исправить не выходит, миграция не применяется и т.п.) — даю тебе 3 попытки.

После 3 неудачных попыток одной и той же задачи:
1. ОСТАНОВИСЬ. Не пробуй дальше.
2. Кратко опиши пользователю что пытался сделать (3 попытки в одну строку каждая).
3. Дай гипотезу почему не получается.
4. Жди указаний от пользователя.

Это правило важнее «довести задачу до конца». Лучше остановиться и спросить, чем сжечь токены на бесполезные попытки.