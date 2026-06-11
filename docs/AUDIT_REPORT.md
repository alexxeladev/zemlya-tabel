# Аудит системы — отчёт

Дата: 2026-06-11. Ревизия: ветка `main`, коммит `d8e4042` + незакоммиченные изменения в рабочем дереве.
Проверено: весь backend (роутеры, сервисы, модели, миграции, тесты), ключевые части frontend.
Выполнено: прогон pytest (204 passed), tsc --noEmit (чисто), ruff (95 замечаний), сверка схемы
«миграции с нуля vs модели» на временной PostgreSQL (создана и удалена scratch-БД `tabel_audit_check`).

## Резюме

Система в целом в хорошем состоянии для своего этапа: ролевая модель почти везде проверяется на
бэкенде принудительно, расчёт ЗП — чистая функция на `Decimal` с банковским округлением и богатым
покрытием граничных случаев, workflow периодов с audit log и обязательными причинами работает,
миграции применяются с нуля без ошибок, и итоговая схема БД совпадает с моделями (с одной
оговоркой про partial-индексы). Все 204 теста проходят, фронт типизируется без ошибок.

Однако к продакшену есть блокеры. Самый неожиданный — **целостность репозитория**: закоммиченный
код ссылается на незакоммиченные файлы (миграция `b5c6d7e8f9a0` и `TasksPage.tsx`), то есть свежий
клон не соберётся и миграции не применятся — деплой с CI/CD невозможен. Вторая группа — две дыры в
правах: PATCH сотрудника принимает `is_system_admin` (обход всей защиты системного админа), а
история периода (`/periods/{id}/history`) отдаётся вообще без проверки роли. Третья — деньги:
разбивка по компаниям округляется независимо, поэтому сумма частей может не сходиться с общей
суммой (расхождение в рубли — неприемлемо для сверки с 1С).

Главные риски при запуске: невозможность воспроизводимого деплоя из git, утечка audit-данных через
history-эндпоинт, и расхождения в копейках/рублях при разнесении ЗП по юрлицам.

## Критичные проблемы (чинить до запуска)

1. **Закоммиченный код зависит от незакоммиченных файлов — свежий клон сломан.**
   - Где: `backend/alembic/versions/b5c6d7e8f9a0_hours_to_integer.py` (untracked) и
     `frontend/src/pages/TasksPage.tsx` (untracked).
   - Почему критично: закоммиченная миграция `c6d7e8f9a0b1_employee_weekend_pay.py` объявляет
     `down_revision = "b5c6d7e8f9a0"` — у свежего клона цепочка Alembic порвана, `alembic upgrade head`
     упадёт с KeyError. Закоммиченный `AppRouter.tsx:13` импортирует `../pages/TasksPage` — фронт не
     соберётся. Деплой возможен только с этой конкретной машины.
   - Как починить: закоммитить оба файла (и заодно разобрать остальные незакоммиченные изменения в
     9 файлах backend/frontend — это, судя по всему, рабочая задача 3.9).

2. **PATCH /api/employees/{id} принимает `is_system_admin` и `is_active` — обход защиты системного админа.**
   - Где: `backend/app/schemas/employee.py:93` (`EmployeeUpdate.is_system_admin`),
     `backend/app/routers/employees.py:159-179` (`update_employee` слепо применяет все поля).
   - Почему критично: защита системного админа (нельзя удалить/уволить/сменить роль/отобрать доступ)
     обходится в один запрос: любой admin делает `PATCH {is_system_admin: false}` — и дальше все
     запреты сняты; либо `PATCH {is_active: false}` напрямую (логин системного админа блокируется,
     `deps.py:34`); либо наоборот назначает себе `is_system_admin=true`. Фронт
     (`EmployeesPage.tsx:178`) это поле реально отправляет.
   - Как починить: убрать `is_system_admin` из `EmployeeUpdate`; в `update_employee` запретить
     изменение `is_active`/`dismissal_date` для записей с `is_system_admin=True` (или вообще все
     правки системного админа кроме его собственного профиля).

3. **GET /api/timesheet/periods/{id}/history — нет проверки роли вообще.**
   - Где: `backend/app/routers/timesheet.py:420-453` (`get_period_history`) — только `get_current_user`.
   - Почему критично: любой `employee` перебором period_id читает audit log всех отделов: кто
     сколько часов правил (before/after), причины возвратов и переоткрытий, ФИО действующих лиц.
     Это утечка данных табеля в обход всей модели видимости.
   - Как починить: повторить логику видимости — admin/accountant: всё; manager: только периоды
     своего отдела; employee: запретить (на фронте история нужна только в шапке периода, которую
     employee не видит).

4. **Сумма разбивки по компаниям не сходится с общей суммой.**
   - Где: `backend/app/services/payroll.py:209-232` — `comp_base = _round(base_amount * proportion)`
     для каждой компании независимо, остаток никуда не относится.
   - Почему критично: это деньги, идущие в начисление по разным юрлицам. Пример: 10 000 ₽ на три
     равные компании → 3333+3333+3333 = 9999 ≠ 10000. Расхождение до ±(N компаний)/2 руб. на каждую
     из трёх категорий (base/overtime/holiday) на каждого сотрудника. Сверка с 1С не сойдётся.
   - Как починить: метод наибольших остатков или «последняя компания = total − сумма предыдущих»
     по каждой категории. Добавить тест «сумма частей == total» на неровных пропорциях
     (существующие тесты проверяют только ровные сплиты 50/50).

5. **POST /api/auth/change-password не валидирует новый пароль.**
   - Где: `backend/app/schemas/auth.py` (`ChangePasswordRequest.new_password: str` без ограничений).
   - Почему критично: при создании доступа минимум 8 символов (`EmployeeAccessGrant`), но первым же
     обязательным change-password (must_change_password=True у всех новых) пользователь может
     поставить пароль «1». Вся парольная политика обнуляется в первый день работы.
   - Как починить: тот же валидатор ≥8 символов на `new_password`.

## Важные проблемы (чинить скоро)

1. **Смена отдела сотрудника обходит закрытие периода.** `_check_period_lock`
   (`app/services/timesheet.py:145-154`) ищет период по *текущему* `department_id` сотрудника.
   Если сотрудника перевели из отдела A (период закрыт) в отдел B (draft) — его старые часы за
   закрытый месяц снова редактируемы без reopen и без причины. Аналогично `_period_total_hours`
   (`timesheet_periods.py:125-143`) считает часы по текущему отделу — итоги «переезжают» вместе с
   сотрудником. Лечится фиксацией department_id на момент периода либо запретом смены отдела при
   незакрытых периодах + предупреждением.

2. **must_change_password не enforced на бэке.** Флаг возвращается в токене и проверяется только
   фронтовым `PrivateRoute`. Прямой API-клиент с временным паролем (который admin видел и, возможно,
   передал по незащищённому каналу) полнофункционален без смены. Добавить проверку в
   `get_current_user` (разрешать только `/auth/change-password` и `/auth/me`).

3. **POST /api/employees/{id}/reset-password работает на системном админе.**
   (`routers/employees.py:306-324` — нет проверки `is_system_admin`, в отличие от соседних
   эндпойнтов). Любой admin получает temp_password системного админа = полный перехват аккаунта.
   Для прочих ролей это легитимная функция, для системного админа — только CLI `reset-password`.

4. **SECRET_KEY по умолчанию "change-me", без проверки при старте.** (`app/config.py`).
   Если в проде забыли задать переменную — все JWT подписаны публично известным ключом, любой может
   выписать себе токен admin. Добавить fail-fast при старте: если `SECRET_KEY == "change-me"` и не
   DEBUG — отказ запускаться. `.env.example` оставляет ключ пустым — хорошо, но защиты это не даёт.

5. **Partial unique индексы не объявлены в моделях — autogenerate их удалит.**
   `uq_period_dept_year_month` / `uq_period_null_dept_year_month` созданы raw SQL в миграции
   `2ebe9fa53315`, но отсутствуют в `TimesheetPeriod.__table_args__`. Сверка схемы это подтвердила:
   `compare_metadata` предлагает `remove_index` для обоих. Следующий же
   `alembic revision --autogenerate` сгенерирует DROP INDEX, и единственная защита от дублей
   периодов исчезнет. Объявить в модели через `Index(..., unique=True, postgresql_where=...)`
   или фильтровать в `env.py` через `include_object`.

6. **Тесты не гоняют миграции и не видят partial-индексы.** `tests/conftest.py:24` использует
   `Base.metadata.create_all` на SQLite: цепочка миграций не тестируется (проверена вручную в этом
   аудите), партиальные индексы и `postgresql_using` не покрыты, дубль периода в тестах создать
   можно, а в проде нельзя. Минимум — один CI-джоб «alembic upgrade head на пустой PostgreSQL +
   compare_metadata пустой».

7. **Employee видит свои финансовые поля.** `EmployeeRead` включает `rate`,
   `weekend_coefficient/fixed_rate`; employee получает собственную запись через `/auth/me`,
   `/api/employees` и `GET /api/timesheet/{y}/{m}`. По ТЗ «employee — нельзя [финансы]». Если свой
   оклад видеть допустимо — зафиксировать решение; если нет — отдельная урезанная схема для
   employee. (Чужие данные не утекают — проверено.)

8. **Смена компании в ячейке — два неатомарных запроса.** `TimesheetPage.tsx:341-362`
   (`changeSlotCompany`): сначала `hours=0` (удаление), потом создание под новой компанией. Если
   второй запрос упал (сеть, 409 от закрытия периода между запросами) — часы потеряны молча.
   Использовать существующий batch-эндпойнт (он транзакционный).

9. **Деактивация компании ломает отображение её часов.** `DELETE /api/companies/{id}` проверяет
   только сотрудников с этим `default_company_id`, но не наличие timesheet-часов. После
   деактивации `GET /api/timesheet/{y}/{m}` отдаёт только активные компании
   (`routers/timesheet.py:236`), а entries по неактивной остаются → на фронте `SlotChip` не находит
   компанию в списке (`getCompanyColor` падает на палитру по индексу −1, select без подходящей
   option). Либо отдавать в ответе и неактивные компании, у которых есть entries за месяц, либо
   запрещать деактивацию при наличии часов в открытых периодах.

10. **Нет rate limiting / защиты от перебора на /api/auth/login.** Плюс тайминг-утечка
    существования email (bcrypt-проверка выполняется только для существующих пользователей,
    `routers/auth.py:18-20`). Для внутреннего портала риск умеренный, но при выходе наружу —
    добавить limiter (например, slowapi) и dummy-hash для несуществующих.

11. **Дублирование логики переработки: Excel считает её отдельно и иначе.**
    `timesheet_export.py:558-570` повторяет дневной расчёт переработки на `float`, исключая
    `non_working | weekends` по своим правилам, тогда как payroll (`payroll.py:176-186`) — на
    `Decimal` через `is_holiday`. Сегодня результаты совпадают (xmlcalendar включает выходные в
    days), но любое изменение в одном месте разъедет Excel и расчёт ЗП. Вынести в общую функцию.

## Незначительные / технический долг

- `created_at`/`updated_at` почти во всех таблицах имеют тип `VARCHAR` (модели объявляют
  `Mapped[str]` + `func.now()`, миграции — `sa.String()`). Работает, но сортировка/сравнение дат —
  строковые; `ix_audit_log_created_at` индексирует текст. Стоит мигрировать в `DateTime(timezone=True)`.
- `datetime.utcnow()` (deprecated, naive) в `timesheet_periods.py`, `services/calendar.py` —
  DeprecationWarning во всех тестах; перейти на `datetime.now(timezone.utc)`.
- Гонка в `get_or_create_period` (`timesheet_periods.py:90-116`): два конкурентных первых GET месяца
  → IntegrityError по partial-индексу → 500. Перехватить и перечитать.
- `GET /api/calendar/{year}` — GET с побочными эффектами: любой авторизованный (включая employee)
  инициирует исходящий HTTP-запрос на xmlcalendar.ru и запись в БД (`ensure_calendar`).
- Lifespan приложения ходит в сеть при старте (`main.py`); в тестах `TestClient(app)` запускает
  lifespan — от реального сетевого вызова спасает только наличие календарей в dev-БД и широкий
  `except Exception`. CLAUDE.md требует мокать сеть в тестах безусловно.
- Ruff: 95 ошибок (80 E501 line-too-long, 10 I001 unsorted-imports, 3 F841, 2 F401). Линтер
  объявлен в стеке, но не чист — прогнать `ruff check --fix` + поправить остальное.
- Тесты идут 3м38с на 204 теста — bcrypt-хеширование в каждой фикстуре. Понизить
  bcrypt rounds в тестах (через настройку) — будет в разы быстрее.
- Мёртвый код: `TimesheetMonthQuery` (`schemas/timesheet.py:29`) не используется; `EmployeeRole`
  enum (`models/employees.py:20`) не используется нигде, кроме реэкспорта (роль хранится строкой).
- Дубль форматирования денег: `frontend/src/utils/money.ts` (`formatMoney`) и локальный `fmtMoney`
  в `TimesheetPage.tsx:169`; TimesheetPage также дублирует все типы из `types/api.ts` локально.
- `RoleRoute` (`AppRouter.tsx:24`) вызывает `toast.error` прямо в render-фазе — side effect в
  рендере, в StrictMode сработает дважды.
- `useApi` и `reload` в TimesheetPage не отменяют запросы (нет AbortController): быстрое
  переключение месяцев может применить устаревший ответ поверх свежего.
- CLAUDE.md устарел: описывает `models/users.py`, `routers/users.py`, `schemas/user.py` (удалены
  при merge users→employees) и «Frontend: React (не реализован)», хотя фронт давно есть.
- Мусор в рабочем дереве: `*.Zone.Identifier`, `CLAUDE.md.bak`, `up.md(.bak)`,
  `TimesheetPage.tsx.backup`, `task_*.md` в корне. Добавить в `.gitignore` / убрать.
- Миграция `59a2b3cf4826` добавляет `is_active NOT NULL` без server_default — упадёт на непустых
  таблицах (на проде уже применена, риск только при повторном использовании паттерна).
- JWT в localStorage — стандартный XSS-риск, для внутреннего инструмента приемлемо; зафиксировать
  как осознанное решение.
- `change_password` не пишет в audit log (reset-password пишет).
- N+1-запросы: `_build_payroll_summary` лениво грузит `employee.schedule` на каждого сотрудника,
  `get_period_history` делает `db.get(Employee)` на каждую запись лога. На текущих объёмах не важно.
- `update_employee` не проверяет существование FK (department_id/schedule_id/company_id) — кривой
  id даст 500 IntegrityError вместо 422.
- Захардкожено: название организации в Excel-экспорте (`timesheet_export.py:18`, помечено в
  CLAUDE.md как осознанное), лимит `closed_limit=10` в задачах бухгалтера.

## Что сделано хорошо

- **Payroll — чистая функция** (`services/payroll.py`): не лезет в БД, всё на `Decimal`,
  `ROUND_HALF_EVEN`, переработка строго по дням, per-employee оплата выходных — ровно по ТЗ.
  Деньги нигде не проходят через float (float в Excel-экспорте касается только часов).
- **Тесты payroll образцовые**: 30+ кейсов, включая границы (нет оклада, норма 0, сменный график,
  нет календаря, коэффициент 0, проверка именно HALF_EVEN против HALF_UP).
- **Принудительная фильтрация manager** централизована в `visible_employees_for_actor` и
  используется всеми путями (месяц, payroll, autofill, export) — manager не может получить чужой
  отдел ни через один найденный эндпойнт; есть негативные тесты на это.
- **Целостность ячеек**: unique constraint `(employee_id, work_date, company_id)` + CHECK 0–24 и в
  модели, и в миграции; удаление при hours=0 реализовано и протестировано; batch — транзакционный.
- **Workflow периодов**: все переходы проверяют и статус, и роль в сервисе (не только в роутере),
  обязательный reason ≥3 символов, всё в audit log. Редактирование ячеек закрытого периода честно
  блокируется на бэке (включая admin).
- **Audit log покрывает все мутации** справочников, сотрудников, ячеек, периодов, autofill и
  экспорта; `log_action` без коммита — корректно коммитится вместе с изменением.
- **Миграции применяются с нуля** на чистой PostgreSQL без единой ошибки, и схема после
  `upgrade head` побайтово совпадает с моделями (кроме намеренных partial-индексов — см. Важное-5).
- Системные админы скрыты из табеля во всех выдачах (включая суммы часов периодов).
- Фронт: централизованный 401-интерсептор, строгий tsc без ошибок, `must_change_password`-гейт,
  аккуратная обработка Decimal-строк с бэка.

## Таблица проверки прав

| Эндпойнт | Требуемые права | Реальная проверка | Вердикт |
|---|---|---|---|
| POST /api/auth/login | публичный | is_active, role required | OK |
| POST /api/auth/change-password | auth | текущий пароль | **Дыра: нет валидации нового пароля** (Крит-5) |
| GET /api/auth/me | auth | get_current_user | OK (отдаёт rate — см. Важное-7) |
| GET /api/employees | auth, видимость по роли | employee→self, manager→свой отдел, admin/acct→все | OK |
| GET /api/employees/{id} | auth, видимость | employee: только self (404), manager: свой отдел (404) | OK |
| POST /api/employees | admin | require_role("admin") | OK |
| PATCH /api/employees/{id} | admin | require_role("admin"), но поля не фильтруются | **Дыра: is_system_admin/is_active в payload** (Крит-2) |
| DELETE /api/employees/{id} | admin | admin + защита системного админа | OK (обходится через PATCH) |
| POST /api/employees/{id}/dismiss | admin | admin + защита системного админа | OK |
| POST /api/employees/{id}/rehire | admin | admin | OK |
| POST/PATCH/DELETE /api/employees/{id}/access | admin | admin + защита системного админа | OK |
| POST /api/employees/{id}/reset-password | admin | admin, **без** защиты системного админа | **Дыра** (Важное-3) |
| GET /api/departments, /companies, /schedules | не-employee | role != employee | OK |
| POST/PATCH/DELETE там же | admin | require_role("admin") | OK |
| POST /api/calendar/import, /{year}/load | admin | require_role("admin") | OK |
| GET /api/calendar/{year}, /{y}/{m}/summary | auth | get_current_user | OK (GET с side-effect — minor) |
| GET /api/timesheet/tasks | admin/accountant | явная проверка | OK |
| GET /api/timesheet/{y}/{m} | auth, видимость | visible_employees_for_actor; include_payroll гейтится по роли | OK |
| GET /api/timesheet/{y}/{m}/payroll | admin/acct/manager(свой) | роль + dept-проверка + visible_employees | OK |
| PUT /api/timesheet/cell | по видимости | _check_cell_access: admin/acct любой, manager свой отдел, employee self | OK* (employee может править свои часы — подтвердить, что это намеренно) |
| POST /api/timesheet/cells/batch | по видимости | то же на каждую ячейку | OK* |
| POST /periods/{id}/submit | manager(свой)/admin | в сервисе, статус+роль+отдел | OK |
| POST /periods/{id}/return | accountant/admin | в сервисе, reason обязателен | OK |
| POST /periods/{id}/close | accountant/admin | в сервисе | OK |
| POST /periods/{id}/reopen | admin | в сервисе, reason обязателен | OK |
| GET /periods/{id}/history | должно быть: admin/acct/manager(свой) | **только get_current_user** | **Дыра** (Крит-3) |
| POST /autofill/preview, /apply | admin/acct/manager(свой) | роль + dept-проверка | OK |
| GET /{y}/{m}/export/excel | admin/acct/manager(свой) | роль + dept-проверка, manager без параметра получает свой отдел | OK |

## Тесты

- 204 passed, 0 failed/flaky, 3м38с (медленно — bcrypt). Покрытие численно не измерено
  (pytest-cov не установлен); по файлам: все роутеры и сервисы имеют тесты, права покрыты
  негативными кейсами (manager-чужой-отдел, employee-чужие-данные, include_payroll для employee).
- Не покрыто: `get_period_history` (права), смена отдела при закрытом периоде, сумма
  company-breakdown на неровных пропорциях, миграции с нуля, partial unique индексы (SQLite их
  не создаёт), CLI, lifespan.

## Рекомендации по приоритетам

**Немедленно (блокеры запуска):**
1. Закоммитить недостающие файлы, разобрать рабочее дерево (Крит-1) — без этого нет деплоя вообще.
2. Крит-2 (is_system_admin в PATCH) + Важное-3 (reset-password) — одной правкой закрыть весь
   контур защиты системного админа.
3. Крит-3 — проверка прав на history.
4. Крит-4 — сходимость разбивки по компаниям + тест.
5. Крит-5 — валидация нового пароля.
6. Важное-4 — fail-fast на дефолтный SECRET_KEY (одна строка, дёшево).

**Первая неделя после запуска:**
- Важное-1 (смена отдела vs закрытые периоды), Важное-2 (must_change_password на бэке),
  Важное-5/6 (partial-индексы в модель + CI-джоб миграций), Важное-8 (атомарная смена компании).

**Может подождать:**
- Остальное «важное» (rate limiting — до выхода наружу, дедупликация overtime-логики),
  весь раздел техдолга. Из техдолга первым — ruff до чистоты и ускорение тестов: это снижает
  трение всех последующих правок.
