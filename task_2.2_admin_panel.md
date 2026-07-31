# Задача 2.2 — админ-панель: справочники в интерфейсе

## Контекст

Бэкенд работает с моделями: users, departments, companies, schedules, employees, audit_log. CRUD-эндпойнты есть только для users (из задачи 1.2). Фронтенд имеет логин, смену пароля, дашборд (из задачи 2.1).

Эта задача добавляет:
- Бэкенд: CRUD-роутеры и Pydantic-схемы для departments, companies, schedules, employees
- Фронтенд: страницы списков и форм для всех 5 справочников + улучшенный дашборд с навигацией

## Часть А — бэкенд

### 1. Схемы

Создать в `backend/app/schemas/`:

**`department.py`** — DepartmentBase, DepartmentCreate, DepartmentRead, DepartmentUpdate. Поля: name, code.

**`company.py`** — CompanyBase, CompanyCreate, CompanyRead, CompanyUpdate. Поля: code, name, inn.

**`schedule.py`** — ScheduleBase, ScheduleCreate, ScheduleRead, ScheduleUpdate. Поля: name, hours_per_shift, description.

**`employee.py`** — EmployeeBase, EmployeeCreate, EmployeeRead, EmployeeUpdate. Поля: tab_number, full_name, position, department_id, schedule_id, default_company_id, rate, is_active, hire_date, dismissal_date. EmployeeRead дополнительно содержит вложенные `department: DepartmentRead | None`, `schedule: ScheduleRead | None`, `default_company: CompanyRead | None` для UI.

Все схемы используют `model_config = {"from_attributes": True}` где нужно.

Все Update-схемы должны быть с optional полями (через `Field(None)` или `| None = None`), чтобы можно было обновлять частично через PATCH.

### 2. Роутеры

Создать в `backend/app/routers/`:

**`departments.py`** — префикс `/api/departments`. Эндпойнты:
- `GET /` — список (для admin/accountant/manager — все; для employee — 403)
- `POST /` — создать (admin only)
- `GET /{id}` — карточка (admin/accountant/manager — любой; employee — только свой департамент)
- `PATCH /{id}` — обновить (admin only)
- `DELETE /{id}` — мягкое удаление через флаг is_active. Если флага нет в модели — добавить миграцией. Если есть сотрудники — 409 с понятным сообщением.

**`companies.py`** — префикс `/api/companies`. Та же логика: list/get для всех ролей, create/update/delete только admin. При удалении компании, если есть сотрудники с `default_company_id` — 409.

**`schedules.py`** — префикс `/api/schedules`. То же, при удалении графика проверять есть ли сотрудники.

**`employees.py`** — префикс `/api/employees`. Сложнее остальных:
- `GET /` — параметры query: `department_id`, `is_active`, `search` (по ФИО и табельному номеру через ILIKE). Для **manager** — принудительный фильтр `department_id = current_user.department_id` (игнорировать query-параметр, если он не совпадает). Для admin/accountant — без фильтра. Для employee — только себя.
- `POST /` — создать (admin only)
- `GET /{id}` — карточка с вложенными отделом/графиком/компанией. Manager — только свой департамент.
- `PATCH /{id}` — admin only.
- `DELETE /{id}` — мягкое удаление через `is_active=False`.

Все мутации записываются в audit_log через хелпер из core.audit.

### 3. Подключение роутеров

В `main.py` добавить все новые роутеры:
```python
app.include_router(departments_router)
app.include_router(companies_router)
app.include_router(schedules_router)
app.include_router(employees_router)
```

### 4. Миграция

Если в таблицах departments/companies/schedules ещё нет колонки `is_active` (boolean default true) — добавить миграцией. Проверь через `alembic revision --autogenerate -m "add is_active to references"`.

### 5. Тесты

В `backend/tests/`:

**`test_departments.py`**, **`test_companies.py`**, **`test_schedules.py`** — каждый: создание (только admin), список, обновление, мягкое удаление, попытка обращения от manager/employee (403 где надо).

**`test_employees.py`** — дополнительно:
- Manager видит только сотрудников своего департамента
- Manager не видит сотрудников другого департамента (404, не 403, чтобы не утекала информация о существовании)
- Search работает (ILIKE по ФИО и tab_number)
- Удалить компанию/график/отдел, на которых висят сотрудники — 409

Все тесты должны пройти.

## Часть Б — фронтенд

### 6. Новые типы

В `frontend/src/types/api.ts` добавить интерфейсы:
```typescript
export interface Department { id: number; name: string; code: string; is_active: boolean; }
export interface Company { id: number; code: string; name: string; inn: string | null; is_active: boolean; }
export interface Schedule { id: number; name: string; hours_per_shift: number; description: string | null; is_active: boolean; }
export interface Employee {
  id: number;
  tab_number: string | null;
  full_name: string;
  position: string | null;
  department_id: number;
  schedule_id: number;
  default_company_id: number;
  rate: number;
  is_active: boolean;
  hire_date: string | null;
  dismissal_date: string | null;
  department: Department | null;
  schedule: Schedule | null;
  default_company: Company | null;
}
```

### 7. API-клиенты

Создать в `frontend/src/api/`:

**`departments.ts`**: `listDepartments`, `getDepartment`, `createDepartment`, `updateDepartment`, `deleteDepartment`.

**`companies.ts`**: аналогично.

**`schedules.ts`**: аналогично.

**`employees.ts`**: `listEmployees(params)`, `getEmployee`, `createEmployee`, `updateEmployee`, `deleteEmployee`. Параметры list: `department_id`, `is_active`, `search`.

**`users.ts`**: `listUsers`, `getUser`, `createUser`, `updateUser`, `resetPassword`, `deleteUser` (для админки пользователей).

### 8. Универсальные компоненты

Создать в `frontend/src/components/`:

**`PageHeader.tsx`** — заголовок страницы с описанием и опциональной кнопкой действия справа. Пропсы: `title`, `description`, `action` (ReactNode).

**`Table.tsx`** — обёртка над `<table>` с базовыми классами Tailwind. Поддержка пустого состояния (props: `isEmpty`, `emptyText`). Поддержка состояния загрузки (`isLoading` — показывать skeleton-строки).

**`Badge.tsx`** — варианты: `gray | green | red | blue | amber`.

**`Modal.tsx`** — простой модал на Tailwind с overlay, без сторонних либ. Принимает `isOpen`, `onClose`, `title`, `children`, `actions` (footer-кнопки).

**`Select.tsx`** — обёртка над `<select>` с поддержкой error и опций `{value, label}[]`.

**`Confirm.tsx`** — диалог подтверждения через Modal. Хелпер `confirm({title, message, danger?}): Promise<boolean>` через ImperativeHandle или Promise-обёртку. Если сложно — обычный компонент с пропсами `isOpen/onConfirm/onCancel`.

### 9. Реусабельные хуки

Создать `frontend/src/hooks/`:

**`useApi.ts`** — простой хук-обёртка над функцией: возвращает `{ data, isLoading, error, refetch }`. Без react-query (чтобы не тянуть зависимость).

**`useAuth.ts`** — обёртка над zustand-стором: возвращает `user`, `role`, хелперы `canAdmin()`, `canManage()`, `isManager()`.

### 10. Навигация

Обновить `AppLayout.tsx`: вместо одной плоской верхней панели — добавить **левую боковую панель** (sidebar) с разделами навигации.

Структура:

- **Sidebar (фиксированный, слева)**:
  - Логотип «Табель» сверху
  - Группа «Учёт»: Дашборд
  - Группа «Справочники» (видна только admin):
    - Пользователи
    - Отделы
    - Компании
    - Графики работы
    - Сотрудники
  - Группа «Справочники» (для manager — только Сотрудники)
  - Внизу sidebar: блок текущего пользователя (имя, роль) + кнопка «Выйти»
  
- **Topbar (вверху, узкий)**:
  - Хлебные крошки или заголовок текущей страницы (можно через context или прямо в каждой странице)

Цвета и стиль:
- Sidebar: `bg-white border-r`, ширина `w-60`
- Активный пункт меню: `bg-blue-50 text-blue-700`
- Hover: `bg-gray-50`
- Группа: маленький заголовок uppercase серый

### 11. Страницы списков

Создать `frontend/src/pages/admin/`:

**`UsersPage.tsx`** — список пользователей. Колонки: ФИО, Email, Роль (badge), Отдел, Статус (Active/Inactive badge), Действия (редактировать, сбросить пароль, деактивировать). Кнопка «Добавить пользователя» в PageHeader. Фильтры (выпадающие): по роли, по отделу, по статусу.

**`DepartmentsPage.tsx`** — простой список: код, название, действия. Кнопка «Добавить отдел».

**`CompaniesPage.tsx`** — код, название, ИНН, действия.

**`SchedulesPage.tsx`** — название, часов/смена, описание, действия.

**`EmployeesPage.tsx`** — табельный №, ФИО, должность, отдел, график, основная компания, оклад, статус, действия. Фильтры: отдел, статус (только активные/все). Поиск-инпут вверху (debounced, 300мс).

### 12. Формы

Для каждой сущности — форма создания и редактирования в модальном окне. Использовать react-hook-form + Zod.

**Форма отдела:** name (required), code (required, 2-10 символов).

**Форма компании:** code (1-5 символов), name, inn (опционально, 10 или 12 цифр если заполнено).

**Форма графика:** name, hours_per_shift (1-24), description (опционально).

**Форма сотрудника:** табельный номер, ФИО, должность, отдел (select), график (select), основная компания (select), оклад (число), дата приёма, дата увольнения, активен. Все select подгружают опции через API при открытии модала.

**Форма пользователя (создание):** email, full_name, role (select), department_id (select, обязателен если role=manager, опционален иначе), employee_id (select, опционален), начальный пароль (генерируется на бэке или вводится — пусть вводится для простоты).

**Форма пользователя (редактирование):** те же поля кроме пароля. Отдельные кнопки «Сбросить пароль» и «Деактивировать».

### 13. Сценарии удаления

При нажатии «Удалить» — модал подтверждения с предупреждением. Если бэкенд вернул 409 (есть зависимости) — toast с понятным текстом «Нельзя удалить: на этом отделе ХХ сотрудников».

### 14. Роутинг

В `AppRouter.tsx` добавить роуты под `/admin/...`:
- `/admin/users`
- `/admin/departments`
- `/admin/companies`
- `/admin/schedules`
- `/admin/employees`

Все защищены ролью admin (для admin виден весь набор). Manager — только `/admin/employees` (свой департамент). Если роль не подходит — редирект на дашборд с toast «Нет доступа».

### 15. Дашборд: ссылки на админку

Превратить заглушку дашборда в красивую плитку. Для admin: 5 карточек со ссылками на справочники, иконки SVG inline. Для manager: 1 карточка «Сотрудники моего отдела». Для accountant: пока тоже плитки на чтение справочников.

### 16. Тосты

Добавить простой механизм тостов (без сторонней библиотеки, кастомный store + компонент). Использовать в успехах/ошибках CRUD.

### 17. Коммиты

Логичные:
- `feat(backend): CRUD for departments, companies, schedules, employees`
- `feat(backend): role-based visibility for employees`
- `feat(frontend): sidebar navigation`
- `feat(frontend): admin pages — users, departments, companies, schedules, employees`
- `feat(frontend): toasts and confirm dialogs`

Запушить на main.

## Acceptance criteria

После выполнения:

1. **Бэкенд тесты**: `pytest` все проходят (включая новые тесты для всех справочников).

2. **Залогинившись как admin**:
   - Видны 5 пунктов справочников в sidebar
   - Можно создать отдел («Дирекция»), компанию («А», «ООО Альфа»), график («5/2», 8 часов), сотрудника
   - Список обновляется после создания
   - Редактирование работает
   - Удаление с зависимостями возвращает 409 → понятный тост
   - Поиск сотрудников по ФИО работает
   - Фильтр по отделу работает

3. **Залогинившись как manager** (создать через UI пользователя с role=manager и department_id выставленным):
   - В sidebar — только «Сотрудники»
   - В списке сотрудников видны ТОЛЬКО сотрудники своего департамента
   - При попытке открыть `/admin/users` напрямую — редирект с тостом «Нет доступа»

4. **Залогинившись как employee**:
   - Открывается дашборд с приветствием
   - В sidebar — никаких админ-пунктов
   - Прямой переход на `/admin/users` — редирект

## Что НЕ делать

- Не делать табель (это этап 3)
- Не делать дашборд руководителя с графиками (этап 8)
- Не делать страницу профиля «Моя учётка»
- Не делать смену пароля админом другого пользователя (только reset на новый временный)
- Не делать импорт сотрудников из CSV (этап 7)
- Не делать i18n
- Не подключать react-query, MUI, AntD — только наши простые компоненты

## В конце

1. Покажи актуальную структуру `frontend/src/`
2. Покажи `pytest -v` (все зелёные)
3. Перечисли созданные/изменённые файлы группами (бэкенд + фронтенд)

## Подводные камни

- Email-валидация: используйте `@example.com` или `@example.org`, не `.local`
- Если запускали что-то через sudo — `chown -R axss:axss .` чтобы не сломать права
- Кнопки в формах: тип `type="button"` по умолчанию, чтобы не сабмитить родительскую форму случайно
- При фильтрации сотрудников по department_id для manager — фильтр на бэке принудительный, на фронте просто скрыть выбор отдела
