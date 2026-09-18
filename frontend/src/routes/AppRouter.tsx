import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppLayout } from '../layouts/AppLayout'
import { ChangePasswordPage } from '../pages/ChangePasswordPage'
import { DashboardPage } from '../pages/DashboardPage'
import { LoginPage } from '../pages/LoginPage'
import { AuditLogPage } from '../pages/admin/AuditLogPage'
import { OrgStructurePage } from '../pages/admin/OrgStructurePage'
import { SchedulesPage } from '../pages/admin/SchedulesPage'
import { CalendarPage } from '../pages/admin/CalendarPage'
import { EmployeesPage } from '../pages/admin/EmployeesPage'
import { PayrollPage } from '../pages/admin/PayrollPage'
import { TimesheetPage } from '../pages/TimesheetPage'
import { VahtaPage } from '../pages/VahtaPage'
import { VahtaSettingsPage } from '../pages/admin/VahtaSettingsPage'
import { TasksPage } from '../pages/TasksPage'
import { PrivateRoute } from './PrivateRoute'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import type { UserRole } from '../types/api'
import { LEGACY_VAHTA_SETTINGS_REDIRECTS, VAHTA_SETTINGS_BASE } from '../utils/vahtaSettings'

function RoleRoute({ allow, children }: { allow: UserRole[]; children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const location = useLocation()

  if (!user || !user.role || !allow.includes(user.role)) {
    toast.error('Нет доступа к этой странице')
    return <Navigate to="/dashboard" replace state={{ from: location }} />
  }
  return <>{children}</>
}

/**
 * Старый адрес → вкладка настроек вахты, с тем же `?…`: закладка
 * `/vahta/staff?position_id=N` должна открыть то же рабочее место.
 */
function LegacyVahtaRedirect({ from }: { from: string }) {
  const { search } = useLocation()
  return <Navigate to={`${VAHTA_SETTINGS_BASE}/${LEGACY_VAHTA_SETTINGS_REDIRECTS[from]}${search}`} replace />
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<PrivateRoute />}>
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/timesheet" element={<TimesheetPage />} />
            <Route
              path="/tasks"
              element={
                <RoleRoute allow={['admin', 'accountant']}>
                  <TasksPage />
                </RoleRoute>
              }
            />
            <Route path="/change-password" element={<ChangePasswordPage />} />

            {/* Модуль «Вахта» (task_vahta): табель охраны на постах.
                Сотруднику раздел не виден — бэк тем же ролям отвечает 403.
                Настройки (экипажи и посты) — админ и менеджер охраны. */}
            <Route
              path="/vahta"
              element={
                <RoleRoute allow={['admin', 'accountant', 'manager', 'timekeeper']}>
                  <VahtaPage />
                </RoleRoute>
              }
            />
            {/* Настройки вахты (task_vahta_settings_staff): всё, что ведётся как
                справочник, — вкладками одного экрана; вкладка в адресе.
                Доступ — admin и менеджер охраны, как у прежних страниц
                «Посты» и «Сотрудники охраны». */}
            <Route
              path="/vahta/settings/:tab?"
              element={
                <RoleRoute allow={['admin', 'manager']}>
                  <VahtaSettingsPage />
                </RoleRoute>
              }
            />
            {/* Прежние адреса — во вкладки; права проверяет маршрут вкладки. */}
            {Object.keys(LEGACY_VAHTA_SETTINGS_REDIRECTS).map((from) => (
              <Route key={from} path={from} element={<LegacyVahtaRedirect from={from} />} />
            ))}

            {/* Единый экран оргструктуры вместо отдельных «Компании» и «Отделы»
                (task_org_structure ч.3). Структуру и права меняет только admin. */}
            <Route
              path="/admin/org"
              element={
                <RoleRoute allow={['admin']}>
                  <OrgStructurePage />
                </RoleRoute>
              }
            />
            {/* Журнал изменений справочников (task_audit_log): показывает оклады,
                роли и доступы по всей компании — поэтому только admin. */}
            <Route
              path="/admin/audit"
              element={
                <RoleRoute allow={['admin']}>
                  <AuditLogPage />
                </RoleRoute>
              }
            />
            <Route path="/admin/departments" element={<Navigate to="/admin/org" replace />} />
            <Route path="/admin/companies" element={<Navigate to="/admin/org" replace />} />
            <Route
              path="/admin/schedules"
              element={
                <RoleRoute allow={['admin', 'accountant']}>
                  <SchedulesPage />
                </RoleRoute>
              }
            />
            <Route
              path="/admin/employees"
              element={
                <RoleRoute allow={['admin', 'manager', 'accountant']}>
                  <EmployeesPage />
                </RoleRoute>
              }
            />
            <Route
              path="/admin/calendar"
              element={
                <RoleRoute allow={['admin']}>
                  <CalendarPage />
                </RoleRoute>
              }
            />
            <Route
              path="/admin/payroll"
              element={
                <RoleRoute allow={['admin', 'accountant', 'manager']}>
                  <PayrollPage />
                </RoleRoute>
              }
            />
          </Route>
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
