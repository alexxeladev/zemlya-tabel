import { Link } from 'react-router-dom'
import { useAuthStore } from '../store/auth'
import type { UserRole } from '../types/api'

const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  employee: 'Сотрудник',
}

interface Tile {
  to: string
  title: string
  description: string
  icon: React.ReactNode
}

const ICONS = {
  users: (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
    </svg>
  ),
  departments: (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z" />
    </svg>
  ),
  companies: (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21" />
    </svg>
  ),
  schedules: (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
    </svg>
  ),
  calendar: (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5m-9-3.75h.008v.008H12v-.008zM12 15h.008v.008H12V15zm0 2.25h.008v.008H12v-.008zM9.75 15h.008v.008H9.75V15zm0 2.25h.008v.008H9.75v-.008zM7.5 15h.008v.008H7.5V15zm0 2.25h.008v.008H7.5v-.008zm6.75-4.5h.008v.008h-.008v-.008zm0 2.25h.008v.008h-.008V15zm0 2.25h.008v.008h-.008v-.008zm2.25-4.5h.008v.008H16.5v-.008zm0 2.25h.008v.008H16.5V15z" />
    </svg>
  ),
  employees: (
    <svg className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
    </svg>
  ),
}

const ADMIN_TILES: Tile[] = [
  { to: '/admin/employees', title: 'Сотрудники', description: 'Карточки сотрудников и доступ', icon: ICONS.employees },
  { to: '/admin/departments', title: 'Отделы', description: 'Структура подразделений', icon: ICONS.departments },
  { to: '/admin/companies', title: 'Компании', description: 'Юридические лица группы', icon: ICONS.companies },
  { to: '/admin/schedules', title: 'Графики работы', description: 'Режимы и смены', icon: ICONS.schedules },
  { to: '/admin/calendar', title: 'Производственный календарь', description: 'Праздники и нормы часов РФ', icon: ICONS.calendar },
]

const MANAGER_TILES: Tile[] = [
  { to: '/admin/employees', title: 'Сотрудники отдела', description: 'Карточки вашего подразделения', icon: ICONS.employees },
]

const ACCOUNTANT_TILES: Tile[] = [
  { to: '/admin/departments', title: 'Отделы', description: 'Просмотр подразделений', icon: ICONS.departments },
  { to: '/admin/companies', title: 'Компании', description: 'Юридические лица', icon: ICONS.companies },
  { to: '/admin/schedules', title: 'Графики работы', description: 'Режимы работы', icon: ICONS.schedules },
  { to: '/admin/employees', title: 'Сотрудники', description: 'Просмотр карточек', icon: ICONS.employees },
]

function TileCard({ tile }: { tile: Tile }) {
  return (
    <Link
      to={tile.to}
      className="flex items-start gap-4 rounded-xl border border-gray-200 bg-white p-5 shadow-sm transition-shadow hover:shadow-md"
    >
      <div className="rounded-lg bg-blue-50 p-2 text-blue-600">{tile.icon}</div>
      <div>
        <h3 className="font-semibold text-gray-900">{tile.title}</h3>
        <p className="text-sm text-gray-500">{tile.description}</p>
      </div>
    </Link>
  )
}

export function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  if (!user) return null

  const tiles =
    user.role === 'admin'
      ? ADMIN_TILES
      : user.role === 'manager'
        ? MANAGER_TILES
        : user.role === 'accountant'
          ? ACCOUNTANT_TILES
          : []

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-gray-900">Здравствуйте, {user.full_name}!</h1>
        <p className="mt-1 text-sm text-gray-500">{user.role ? ROLE_LABELS[user.role] : ''}</p>
      </div>

      {tiles.length > 0 ? (
        <div>
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">Быстрый доступ</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {tiles.map((tile) => (
              <TileCard key={tile.to} tile={tile} />
            ))}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm text-sm text-gray-500">
          Ваш дашборд пока пуст. Табели появятся позже.
        </div>
      )}
    </div>
  )
}
