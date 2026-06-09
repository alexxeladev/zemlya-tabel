import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppLayout } from '../layouts/AppLayout'
import { ChangePasswordPage } from '../pages/ChangePasswordPage'
import { DashboardPage } from '../pages/DashboardPage'
import { LoginPage } from '../pages/LoginPage'
import { DepartmentsPage } from '../pages/admin/DepartmentsPage'
import { CompaniesPage } from '../pages/admin/CompaniesPage'
import { SchedulesPage } from '../pages/admin/SchedulesPage'
import { CalendarPage } from '../pages/admin/CalendarPage'
import { EmployeesPage } from '../pages/admin/EmployeesPage'
import { PayrollPage } from '../pages/admin/PayrollPage'
import { TimesheetPage } from '../pages/TimesheetPage'
import { TasksPage } from '../pages/TasksPage'
import { PrivateRoute } from './PrivateRoute'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import type { UserRole } from '../types/api'

function RoleRoute({ allow, children }: { allow: UserRole[]; children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const location = useLocation()

  if (!user || !user.role || !allow.includes(user.role)) {
    toast.error('Нет доступа к этой странице')
    return <Navigate to="/dashboard" replace state={{ from: location }} />
  }
  return <>{children}</>
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

            <Route
              path="/admin/departments"
              element={
                <RoleRoute allow={['admin', 'accountant']}>
                  <DepartmentsPage />
                </RoleRoute>
              }
            />
            <Route
              path="/admin/companies"
              element={
                <RoleRoute allow={['admin', 'accountant']}>
                  <CompaniesPage />
                </RoleRoute>
              }
            />
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
