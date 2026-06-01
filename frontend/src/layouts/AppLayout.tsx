import { Link, Outlet, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  employee: 'Сотрудник',
}

export function AppLayout() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="sticky top-0 z-10 border-b border-gray-200 bg-white">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
          <Link to="/dashboard" className="text-lg font-semibold text-brand">
            Табель
          </Link>
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-700">{user?.full_name}</span>
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
              {ROLE_LABELS[user?.role ?? ''] ?? user?.role}
            </span>
            <button
              onClick={handleLogout}
              className="cursor-pointer text-sm text-gray-500 transition-colors hover:text-gray-900"
            >
              Выйти
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
