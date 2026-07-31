# Задача 2.1 — фронтенд: скелет и страница логина

## Контекст

Бэкенд готов и работает: FastAPI на http://localhost:8000, эндпойнты `/api/auth/login`, `/api/auth/me`, `/api/auth/change-password`, JWT-токены, ролевая модель. Сейчас создаём фронтенд: React-приложение которое умеет авторизоваться через API.

Это **первая половина** Этапа 2. Следующая часть (2.2) — админ-панель со справочниками. В этой задаче — только скелет приложения, логин, защищённый роут.

Папка `frontend/` уже существует пустая.

## Стек

- **Vite** — сборщик и dev-сервер
- **React 18+** с TypeScript
- **React Router 6+** — роутинг
- **TailwindCSS** — стили (классика, без UI-китов)
- **Axios** — HTTP-клиент
- **Zustand** — простой store для auth (легче Redux, без лишнего)
- **React Hook Form + Zod** — формы и валидация
- **ESLint + Prettier** — линтер и форматирование

Никаких компонентных библиотек типа MUI/AntD/Chakra. Свои простые компоненты на Tailwind — потом будем дополнять. Меньше зависимостей = меньше проблем при сборке.

## Что нужно сделать

### 1. Инициализация проекта

```bash
cd frontend
npm create vite@latest . -- --template react-ts
```

Если попросит подтвердить overwrite пустой папки — yes.

Установить дополнительные зависимости:
```bash
npm install react-router-dom axios zustand react-hook-form @hookform/resolvers zod
npm install -D tailwindcss @tailwindcss/postcss postcss prettier eslint-plugin-prettier
```

### 2. Настройка TailwindCSS

Tailwind v4 устанавливается через PostCSS-плагин:

`postcss.config.js`:
```js
export default {
  plugins: {
    '@tailwindcss/postcss': {},
  },
}
```

В `src/index.css` оставить только:
```css
@import "tailwindcss";

@theme {
  --color-brand: #2563eb;
}
```

Удалить все CSS-сбросы и Vite-стили которые шаблон положил по умолчанию.

### 3. Структура

```
frontend/
├── src/
│   ├── api/
│   │   ├── client.ts            # axios instance с interceptors
│   │   └── auth.ts              # login, getMe, changePassword
│   ├── store/
│   │   └── auth.ts              # Zustand: user, token, login, logout
│   ├── routes/
│   │   ├── AppRouter.tsx        # роутинг с защищёнными роутами
│   │   └── PrivateRoute.tsx
│   ├── pages/
│   │   ├── LoginPage.tsx
│   │   ├── ChangePasswordPage.tsx
│   │   └── DashboardPage.tsx    # заглушка "Добро пожаловать"
│   ├── layouts/
│   │   └── AppLayout.tsx        # верхняя панель + контент
│   ├── components/
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── FormField.tsx
│   │   └── ErrorBox.tsx
│   ├── types/
│   │   └── api.ts               # типы User, TokenResponse и т.д.
│   ├── App.tsx
│   ├── main.tsx
│   └── index.css
├── .env.development             # VITE_API_URL=http://localhost:8000
├── .env.example
├── tailwind.config.js
├── postcss.config.js
├── tsconfig.json
├── vite.config.ts
└── package.json
```

### 4. API-клиент

`src/api/client.ts`:
- Создать axios instance с `baseURL = import.meta.env.VITE_API_URL`
- Interceptor запросов: добавлять `Authorization: Bearer <token>` если токен есть в store
- Interceptor ответов: если 401 — вызывать `logout()` и редирект на `/login`
- Все ошибки от API парсить и приводить к нормальному виду (брать `error.response.data.detail` если есть)

`src/api/auth.ts`:
```typescript
export async function login(email: string, password: string): Promise<TokenResponse>
export async function getMe(): Promise<User>
export async function changePassword(currentPassword: string, newPassword: string): Promise<void>
```

### 5. Auth store (Zustand)

`src/store/auth.ts`:
- `user: User | null`
- `token: string | null`
- `mustChangePassword: boolean`
- `login(email, password): Promise<void>` — вызывает API, сохраняет токен в localStorage + state
- `logout(): void` — чистит state и localStorage
- `loadUserFromToken(): Promise<void>` — при старте приложения: если в localStorage есть токен, вызвать `/auth/me`, восстановить пользователя; при 401 — logout
- Подписаться на изменения и зеркалить токен в localStorage

### 6. Роутинг

`src/routes/AppRouter.tsx`:
- `/login` → LoginPage (public)
- `/change-password` → ChangePasswordPage (private, доступна когда `mustChangePassword=true` принудительно)
- `/dashboard` → DashboardPage (private)
- `/` → редирект на `/dashboard` если авторизован, иначе на `/login`

`PrivateRoute.tsx`:
- Если нет user — редирект на `/login` с сохранением исходного URL
- Если `mustChangePassword=true` и пользователь пытается зайти не на `/change-password` — принудительный редирект на `/change-password`
- Иначе — рендерить детей

### 7. LoginPage

Простая форма по центру:
- Заголовок «Табель» крупно
- Подзаголовок «Вход в систему»
- Поля: email, пароль
- Кнопка «Войти»
- Под кнопкой — место для ошибок (ErrorBox)

Валидация через Zod:
- email — обязателен, формат email
- password — обязателен, минимум 6 символов

При успехе:
- Если `must_change_password=true` → `/change-password`
- Иначе → исходный URL или `/dashboard`

При ошибке — показать ошибку под формой (от 401: «Неверный email или пароль», от 403: «Учётная запись заблокирована», от других: текст из API или «Ошибка сервера»).

### 8. ChangePasswordPage

- Заголовок «Сменить пароль»
- Текст «При первом входе необходимо сменить пароль»
- Поля: текущий пароль, новый пароль, повтор нового пароля
- Кнопка «Сменить пароль»

Валидация:
- Новый пароль ≠ текущему
- Минимум 8 символов
- Повтор должен совпадать

После успеха — toast «Пароль изменён», редирект на `/dashboard`.

### 9. AppLayout

Верхняя панель (sticky top):
- Слева: «Табель» (логотип-текст, кликабельно → `/dashboard`)
- Справа: имя пользователя + роль (badge) + кнопка «Выйти»

Контент — `<Outlet />` снизу.

Цвета:
- Фон страницы: `bg-gray-50`
- Верхняя панель: `bg-white border-b`
- Акцент: `text-brand` (синий из @theme)

### 10. DashboardPage (заглушка)

Просто карточка с приветствием:
- Заголовок: «Здравствуйте, {full_name}»
- Подзаголовок: текущая роль на русском (Admin → «Администратор», Manager → «Руководитель», Accountant → «Бухгалтер», Employee → «Сотрудник»)
- Под этим: список того, что доступно вашей роли (просто текст, без ссылок пока). Например для админа: «Управление пользователями, отделами, компаниями, графиками, сотрудниками».

### 11. Компоненты

Простые reusable компоненты на Tailwind, типобезопасные. Без UI-китов.

`Button.tsx` — варианты `primary | secondary | ghost`, размеры `sm | md`, состояние `loading` (показывать спиннер и блокировать).

`Input.tsx` — обёртка над `<input>` с классами Tailwind, поддержка `type`, `error` (бордер красный), `disabled`.

`FormField.tsx` — лейбл сверху, инпут под ним, текст ошибки красным под инпутом. Принимает `label`, `error`, `children`.

`ErrorBox.tsx` — красный блок с иконкой, рендерит ошибку если `message` непуст.

### 12. CORS на бэкенде

В `.env.example` бэкенда добавить `CORS_ORIGINS=http://localhost:5173` (Vite dev порт). Убедиться что в `main.py` CORSMiddleware уже подключен (это было в задаче 1.2).

### 13. .env файлы

`frontend/.env.example`:
```
VITE_API_URL=http://localhost:8000
```

`frontend/.env.development`:
```
VITE_API_URL=http://localhost:8000
```

В `.gitignore` фронтенда: `node_modules`, `dist`, `.env.local`, `.env.*.local`.

### 14. README

Дополнить корневой `README.md` секцией «Frontend»: установка зависимостей, dev-сервер, прод-сборка.

### 15. Коммиты

Логичные коммиты:
- `feat(frontend): scaffold React + Vite + TypeScript`
- `feat(frontend): tailwind setup, base components`
- `feat(frontend): auth store and API client`
- `feat(frontend): login and change-password pages`

Запушить на main.

## Acceptance criteria

После выполнения:

```bash
# Бэкенд работает
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
# (в другом окне)

# Фронтенд работает
cd frontend
npm run dev
# открывает http://localhost:5173
```

Сценарий проверки:
1. Открыть http://localhost:5173 — редирект на `/login`, показывает форму входа
2. Войти как `admin@test.local` / `admin123` (этот пользователь создан в задаче 1.2)
3. Так как `must_change_password=true` — редирект на `/change-password`
4. Сменить пароль на `newpass123`
5. Редирект на `/dashboard` — приветствие «Здравствуйте, Test Admin», роль «Администратор»
6. Кнопка «Выйти» — возврат на `/login`
7. Логин с новым паролем — сразу на `/dashboard`, без смены пароля
8. Логин с неправильным паролем — ошибка под формой
9. Открыть `/dashboard` без авторизации — редирект на `/login`
10. F5 на `/dashboard` — пользователь остаётся залогинен (токен в localStorage)

## Что НЕ делать

- Не делать админ-панель (это 2.2)
- Не делать страницы справочников
- Не делать табель
- Не настраивать i18n
- Не подключать UI-киты (MUI, AntD, shadcn — ничего)
- Не делать тёмную тему
- Не делать PWA / мобильную адаптацию (это потом)
- Не реализовывать запоминание пароля «Remember me»
- Не делать recovery пароля по email (его пока некуда отсылать)

## В конце

Покажи:
1. Структуру `frontend/src/`
2. Скриншот работающего логина (или хотя бы скажи что dev-сервер запустился и страница рендерится)
3. Результат `npm run build` (должен пройти без ошибок)
