import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/auth'
import { Toaster } from '../components/Toaster'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  employee: 'Сотрудник',
}

interface NavItem {
  to: string
  label: string
}

function SidebarLink({ to, label }: NavItem) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `block rounded-md px-3 py-2 text-sm transition-colors ${
          isActive
            ? 'bg-blue-50 text-blue-700 font-medium'
            : 'text-gray-700 hover:bg-gray-50'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

function SidebarGroup({ title, items }: { title: string; items: NavItem[] }) {
  return (
    <div>
      <p className="mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-gray-400">{title}</p>
      <div className="flex flex-col gap-0.5">
        {items.map((item) => (
          <SidebarLink key={item.to} {...item} />
        ))}
      </div>
    </div>
  )
}

export function AppLayout() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  const role = user?.role

  const adminItems: NavItem[] = [
    { to: '/admin/employees', label: 'Сотрудники' },
    { to: '/admin/departments', label: 'Отделы' },
    { to: '/admin/companies', label: 'Компании' },
    { to: '/admin/schedules', label: 'Графики работы' },
    { to: '/admin/calendar', label: 'Произв. календарь' },
  ]

  const managerItems: NavItem[] = [
    { to: '/admin/employees', label: 'Сотрудники' },
  ]

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-gray-200 bg-white">
        <div className="flex h-14 items-center border-b border-gray-200 px-4">
          <span className="text-lg font-bold text-blue-700">Табель</span>
        </div>

        <nav className="flex flex-1 flex-col gap-4 overflow-y-auto p-3">
          <SidebarGroup title="Учёт" items={[
            { to: '/dashboard', label: 'Дашборд' },
            { to: '/timesheet', label: 'Табель' },
            ...((role === 'admin' || role === 'accountant') ? [{ to: '/admin/payroll', label: 'Расчёт ЗП' }] : []),
          ]} />

          {role === 'admin' && (
            <SidebarGroup title="Справочники" items={adminItems} />
          )}
          {role === 'manager' && (
            <SidebarGroup title="Справочники" items={managerItems} />
          )}
          {role === 'accountant' && (
            <SidebarGroup title="Справочники" items={[
              { to: '/admin/departments', label: 'Отделы' },
              { to: '/admin/companies', label: 'Компании' },
              { to: '/admin/schedules', label: 'Графики работы' },
              { to: '/admin/employees', label: 'Сотрудники' },
            ]} />
          )}
        </nav>

        <div className="border-t border-gray-200 p-3">
          <div className="mb-2 px-3">
            <p className="text-sm font-medium text-gray-800 truncate">{user?.full_name}</p>
            <p className="text-xs text-gray-500">{ROLE_LABELS[role ?? ''] ?? role}</p>
          </div>
          <button
            onClick={handleLogout}
            className="w-full rounded-md px-3 py-2 text-left text-sm text-gray-600 hover:bg-gray-50 transition-colors cursor-pointer"
          >
            Выйти
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="ml-60 flex flex-1 flex-col overflow-hidden min-w-0">
        <main className="flex-1 overflow-hidden min-w-0 p-6">
          <Outlet />
        </main>
      </div>

      <Toaster />
    </div>
  )
}
