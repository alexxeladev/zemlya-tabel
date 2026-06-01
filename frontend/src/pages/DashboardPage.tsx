import type { UserRole } from '../types/api'
import { useAuthStore } from '../store/auth'

const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  employee: 'Сотрудник',
}

const ROLE_FEATURES: Record<UserRole, string[]> = {
  admin: [
    'Управление пользователями',
    'Управление отделами',
    'Управление компаниями',
    'Управление графиками работы',
    'Управление карточками сотрудников',
  ],
  manager: ['Просмотр сотрудников подразделения', 'Ведение и утверждение табелей'],
  accountant: ['Просмотр табелей всех сотрудников', 'Экспорт данных для расчёта зарплаты'],
  employee: ['Просмотр своего табеля'],
}

export function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  if (!user) return null

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-gray-900">Здравствуйте, {user.full_name}!</h1>
        <p className="mt-1 text-sm text-gray-500">{ROLE_LABELS[user.role]}</p>
      </div>
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Доступно вашей роли
        </h2>
        <ul className="space-y-2">
          {ROLE_FEATURES[user.role].map((feature) => (
            <li key={feature} className="flex items-center gap-2 text-sm text-gray-700">
              <span className="h-1.5 w-1.5 rounded-full bg-brand" />
              {feature}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
