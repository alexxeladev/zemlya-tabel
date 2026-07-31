# Задача 2.3 — объединение Users и Employees в одну сущность

## Контекст

Сейчас в системе две связанные сущности: `User` (учётка для входа с email/паролем/ролью) и `Employee` (карточка сотрудника с табельным номером/отделом/графиком/окладом). Они логически разные, но фактически указывают на одного человека — и заводить надо обе вручную, что неудобно и легко ошибиться.

**Решение:** сливаем в одну сущность `Employee`. Поля учётки (`email`, `hashed_password`, `role`, `must_change_password`, `last_login_at`) становятся опциональными полями карточки сотрудника. Если они заполнены — у сотрудника есть доступ в систему, он может войти. Если нет — у него только табель.

Таблицу `users` удаляем.

## Архитектурные решения

1. **Имя сущности.** Оставляем `Employee` (на бэке) и «Сотрудник» (на фронте). Это семантически точнее: пользователь системы = сотрудник компании.

2. **Системный admin (первый, создаваемый CLI).** Это обычная запись `Employee` с ролью `admin`. Но с двумя ограничениями:
   - При попытке удалить — 403 «Нельзя удалить системного администратора»
   - При попытке изменить роль — 403 «Нельзя сменить роль системного администратора»
   - Системность определяется флагом `is_system_admin: bool` в БД (default false; ставится в true только через CLI `create-admin`).
   - На фронте показывается как обычный сотрудник в общем списке. Все поля кроме email/full_name/role могут быть пусты. Бейдж «Системный» рядом с ФИО.

3. **Отдел опционален.** Поле `department_id` становится nullable. Для роли `manager` фильтр данных идёт по `department_id` — если у manager нет отдела, он не видит никого (фронт показывает понятный экран «У вас не задан отдел, обратитесь к админу»).

4. **Поля карточки.** Карточка `Employee` делится на 4 блока:
   - **Личная информация**: ФИО, должность, табельный номер
   - **Структура**: отдел (опц.), график (опц.), основная компания (опц.)
   - **Финансы**: оклад (опц., нужен только если будем считать ЗП через систему), даты приёма/увольнения
   - **Доступ в систему**: галка «Есть доступ»; если включена — email, роль, начальный пароль (при создании), кнопки «Сбросить пароль» / «Заблокировать вход» (при редактировании)
   
   Если галка «Есть доступ» выключена — поля email/роль/пароль скрыты и не валидируются.

5. **Что бывает с employee без учётки.** Просто запись в БД с заполненными «личными» полями, без email и без хеша пароля. Он не может зайти. Когда понадобится — админ ставит галку «Есть доступ», заполняет email и роль, и человек получает доступ.

6. **Аудит.** Все ссылки в `audit_log.actor_id` теперь смотрят на `employees.id` (вместо `users.id`).

## Что нужно сделать

### Часть А — бэкенд

#### 1. Модель Employee

Добавить в `app/models/employees.py` поля:
- `email: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)`
- `hashed_password: Mapped[str | None] = mapped_column(String, nullable=True)`
- `role: Mapped[str | None] = mapped_column(String, nullable=True)` — enum: admin/manager/accountant/employee
- `must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)`
- `last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)`
- `is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)`
- `department_id` — сделать **nullable=True** (изменить существующее поле)
- `schedule_id`, `default_company_id`, `rate` — все **nullable=True** (сделать опциональными)

Проверочные ограничения на БД-уровне (через CheckConstraint):
- Если `email IS NOT NULL` → `hashed_password IS NOT NULL AND role IS NOT NULL`
- `is_system_admin` может быть TRUE только если `role = 'admin'`

#### 2. Миграция данных

Создать миграцию которая:
1. Добавляет новые колонки в `employees`
2. Делает старые поля nullable
3. **Переносит данные из `users` в `employees`**:
   - Для каждого `user` с привязанным `employee_id` — копирует email/hashed_password/role/must_change_password/last_login_at в соответствующего employee.
   - Для `user` без `employee_id` (типа нашего admin@example.com) — создаёт новую запись в `employees` с full_name из user, role из user, is_system_admin=True если user.role='admin', остальные поля NULL.
4. Меняет FK `audit_log.actor_id` с `users.id` на `employees.id`. **ВАЖНО:** перед этим обновить значения actor_id чтобы они указывали на правильных employees (использовать таблицу соответствия user.id → employee.id, построенную на шаге 3).
5. **Удаляет таблицу `users`**.

Миграцию писать аккуратно с `op.execute()` для переноса данных. Перед удалением `users` обязательно убедиться что все ссылки переехали.

#### 3. CLI

Переделать `python -m app.cli create-admin` так, чтобы он создавал запись в `employees` с `is_system_admin=True`. Если в системе уже есть system admin — выводить ошибку «System admin already exists. Use reset-password instead».

Добавить команду `python -m app.cli reset-password --email ... --new-password ...` — сбрасывает пароль конкретного employee, ставит must_change_password=True.

#### 4. Auth

В `app/core/security.py` и `app/core/deps.py`:
- Везде где было `User` — заменить на `Employee`
- В JWT subject теперь employee.id
- `get_current_user()` (можно переименовать в `get_current_employee()`, но для совместимости оставлю старое имя) возвращает Employee. Если `employee.email is None` или `employee.role is None` или `not employee.is_active` — 401.
- В `require_role(*roles)` — проверка `current.role in roles`

#### 5. Схемы

В `app/schemas/`:
- Удалить `user.py` (или оставить как deprecated с импортом из employee.py — лучше удалить)
- В `employee.py` расширить:
  - `EmployeeBase`: tab_number, full_name, position, department_id (Optional), schedule_id (Optional), default_company_id (Optional), rate (Optional), hire_date, dismissal_date, is_active
  - `EmployeeAccessBase`: email, role, must_change_password (для секции «Доступ»)
  - `EmployeeCreate`: всё из EmployeeBase + опциональный `access: EmployeeAccessCreate | None`, где `EmployeeAccessCreate` содержит email, role, initial_password
  - `EmployeeRead`: всё из EmployeeBase + computed-поле `has_access: bool` (true если email is not None) + `email`, `role`, `must_change_password`, `last_login_at`, `is_system_admin` + вложенные department/schedule/default_company
  - `EmployeeUpdate`: все поля optional (PATCH-семантика)
  - `EmployeeAccessGrant`: email, role, initial_password (для эндпойнта «Выдать доступ»)
  - `EmployeeAccessUpdate`: только role (для смены роли)

В `auth.py` схемы остаются — login/changePassword не меняются по форме.

#### 6. Роутеры

**Удалить `app/routers/users.py`.**

В `app/routers/employees.py` добавить эндпойнты управления доступом (все admin only, кроме явных пометок):

- `POST /api/employees/{id}/access` — выдать доступ. Принимает `EmployeeAccessGrant`. Если у employee уже есть email — 409. Хеширует пароль, ставит `must_change_password=True`. Audit log: action="access_granted".
- `PATCH /api/employees/{id}/access` — изменить роль. Принимает `EmployeeAccessUpdate`. Если `is_system_admin=True` — 403 «Нельзя сменить роль системного администратора». Audit log: action="role_changed", before/after с ролью.
- `POST /api/employees/{id}/reset-password` — сгенерировать новый временный пароль. Если у employee нет email — 400. Если `is_system_admin=True` — разрешено (system admin тоже может потерять пароль). Возвращает новый временный пароль в ответе (одноразово). Ставит `must_change_password=True`.
- `DELETE /api/employees/{id}/access` — отобрать доступ. Email/hashed_password/role/must_change_password → NULL. Если `is_system_admin=True` — 403. Audit log: action="access_revoked".

В существующих эндпойнтах:
- `DELETE /api/employees/{id}`: если `is_system_admin=True` — 403 «Нельзя удалить системного администратора».
- В фильтрации списка для роли `manager` если у current.department_id IS NULL — возвращать пустой список (видимо, ему нечего показывать).

#### 7. main.py

Убрать подключение `users_router` (мы его удалили). Оставить `auth_router`, `employees_router`, `departments_router`, `companies_router`, `schedules_router`.

#### 8. Тесты

- **Удалить `tests/test_users.py`.** Перенести релевантные кейсы в `tests/test_employees.py` под раздел «access management».
- В `tests/test_employees.py` добавить:
  - Создание employee без доступа: role/email NULL, has_access=false
  - Создание employee сразу с доступом (через POST со вложенным `access`)
  - Выдать доступ существующему employee
  - Сменить роль
  - Сбросить пароль — возвращает новый пароль одноразово
  - Отобрать доступ — email/role обнуляются
  - System admin: нельзя удалить, нельзя сменить роль, **можно** сбросить пароль
  - Manager без department_id видит пустой список employees
  - Manager с department_id видит только свой отдел
  - При логине, если employee.role IS NULL — 401 (нет доступа)
- В `tests/test_auth.py` — поменять создание тестового user-а в фикстурах на создание employee с доступом.
- Прогнать `pytest -v` — все должны быть зелёные.

### Часть Б — фронтенд

#### 9. Удалить страницу Users

- `frontend/src/pages/admin/UsersPage.tsx` — удалить
- `frontend/src/api/users.ts` — удалить
- В sidebar навигации убрать пункт «Пользователи»

#### 10. Типы

В `frontend/src/types/api.ts`:
- Удалить `interface User`
- В `interface Employee` добавить: `email: string | null`, `role: UserRole | null`, `has_access: boolean`, `must_change_password: boolean`, `last_login_at: string | null`, `is_system_admin: boolean`
- `department_id`, `schedule_id`, `default_company_id`, `rate` — стали nullable

#### 11. API-клиент

В `frontend/src/api/employees.ts` добавить:
- `grantAccess(id, { email, role, initial_password })`
- `updateRole(id, { role })`
- `resetPassword(id): Promise<{ temporary_password: string }>`
- `revokeAccess(id)`

#### 12. EmployeesPage

Полностью переделать форму создания/редактирования сотрудника. Модал должен быть **большой** (max-w-2xl) с 4 секциями (карточки или табы):

1. **Личная информация**
   - Табельный номер (опц.)
   - ФИО (обязательно)
   - Должность (опц.)

2. **Структура**
   - Отдел (select, опц., «Без отдела» как пустое значение)
   - График (select, опц.)
   - Основная компания (select, опц.)

3. **Финансы**
   - Оклад в рублях (опц.)
   - Дата приёма (опц.)
   - Дата увольнения (опц., если заполнено — статус будет неактивен)

4. **Доступ в систему**
   - Чекбокс «Есть доступ в систему»
   - Если включён — показываются поля:
     - Email (обязательно)
     - Роль (select: admin / manager / accountant / employee)
     - Начальный пароль (при создании) или информация «Сменён ХХ» (при редактировании)
   - Если редактируем существующего с доступом — кнопки:
     - «Сбросить пароль» — модал с подтверждением, после сброса показать сгенерированный временный пароль с кнопкой «Скопировать»
     - «Отобрать доступ» — модал с подтверждением, после — email/role обнуляются

В списке employees добавить колонку «Доступ» — badge:
- Серый «Нет» если has_access=false
- Зелёный «Admin» / «Руководитель» / «Бухгалтер» / «Сотрудник» если has_access=true
- Если is_system_admin — фиолетовый badge «Системный»

Для системного admin при редактировании:
- Поля «Личная информация», «Структура», «Финансы» можно редактировать
- Поле «Роль» в секции «Доступ» — disabled, подпись «Системный администратор, роль изменить нельзя»
- Кнопки «Отобрать доступ» нет
- Кнопка «Удалить» в списке — disabled с подсказкой

#### 13. Дашборд

Убрать плитку «Пользователи». Оставить плитки: Сотрудники, Отделы, Компании, Графики работы.

#### 14. ChangePasswordPage

Не меняется по форме — но за кулисами теперь работает через employees.

#### 15. Коммиты

- `feat(db): merge users into employees, system_admin flag, access fields nullable`
- `feat(backend): employees with access management endpoints`
- `refactor(backend): remove users module, migrate audit_log refs`
- `feat(frontend): unified employees page with access section`
- `refactor(frontend): remove users page from admin panel`

## Acceptance criteria

```bash
# Все тесты проходят
cd backend
source .venv/bin/activate
pytest -v
# 40+ tests, все green

# Старая БД мигрируется без потерь
alembic upgrade head
# Проверить что таблица users отсутствует, employees содержит данные
docker exec -it $(docker ps -q -f ancestor=postgres:16) psql -U tabel -d tabel -c "\dt"
docker exec -it $(docker ps -q -f ancestor=postgres:16) psql -U tabel -d tabel -c "SELECT id, full_name, email, role, is_system_admin FROM employees;"
# Видим что system admin перенесён, остальные employees сохранены
```

В UI (под admin):

1. В sidebar нет пункта «Пользователи», есть «Сотрудники»
2. На странице «Сотрудники» виден системный админ с бейджем «Системный» и его email
3. Можно создать нового сотрудника **без доступа** — заполнить только ФИО, должность, отдел. В колонке «Доступ» — серый бейдж «Нет»
4. Можно открыть этого сотрудника, включить «Есть доступ в систему», заполнить email/роль/пароль, сохранить → в списке его badge становится зелёным
5. Можно сбросить пароль сотруднику — получаем временный
6. Можно отобрать доступ — поля access обнуляются
7. Системный admin: попытка изменить роль — disabled поле, попытка удалить — disabled кнопка
8. Создать manager с отделом, войти под ним — видит только свой отдел в employees
9. Создать manager БЕЗ отдела, войти под ним — в employees пустой список + сообщение «У вас не задан отдел»

## Подводные камни

- **Миграция данных критична.** Если упадёт посреди — можно потерять данные. Перед миграцией обязательно сделать дамп БД на всякий случай: `docker exec -t $(docker ps -q -f ancestor=postgres:16) pg_dump -U tabel tabel > /tmp/backup_$(date +%s).sql`. Положить эту команду в комментарии миграции.
- Email с `.local` всё ещё может быть в БД у старого админа — заменить на `.com` через UPDATE до миграции (или прямо в самой миграции, в шаге переноса данных).
- При очистке кэша zustand-стора на фронте старые ссылки на роуты `/admin/users` могут остаться у разработчика в localStorage — это нормально, redirect отработает.
- При создании Pydantic-схемы EmployeeAccessCreate валидировать формат пароля (мин. 8 символов) и email (но не .local!).
- `password` в схеме никогда не отдавать в Read — только хеш храним, наружу не светим.

## Что НЕ делать

- Не делать тонкие пермишены (бухгалтер без права 1С) — рано
- Не делать страницу «Мой профиль» сотрудника — этап потом
- Не добавлять восстановление пароля по email — нет почтового сервера
- Не делать 2FA — оверкилл сейчас

## В конце

Покажи:
1. Список таблиц в БД (`\dt` через psql) — должно быть 6 таблиц без users
2. Структура employees из psql — `\d employees`
3. Результат `pytest -v`
4. Структуру `frontend/src/pages/` — без папки `admin/UsersPage.tsx`
