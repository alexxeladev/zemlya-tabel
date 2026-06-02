import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppLayout } from '../layouts/AppLayout'
import { ChangePasswordPage } from '../pages/ChangePasswordPage'
import { DashboardPage } from '../pages/DashboardPage'
import { LoginPage } from '../pages/LoginPage'
import { DepartmentsPage } from '../pages/admin/DepartmentsPage'
import { CompaniesPage } from '../pages/admin/CompaniesPage'
import { SchedulesPage } from '../pages/admin/SchedulesPage'
import { EmployeesPage } from '../pages/admin/EmployeesPage'
import { UsersPage } from '../pages/admin/UsersPage'
import { PrivateRoute } from './PrivateRoute'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import type { UserRole } from '../types/api'

function RoleRoute({ allow, children }: { allow: UserRole[]; children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const location = useLocation()

  if (!user || !allow.includes(user.role)) {
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
            <Route path="/change-password" element={<ChangePasswordPage />} />

            <Route
              path="/admin/users"
              element={
                <RoleRoute allow={['admin']}>
                  <UsersPage />
                </RoleRoute>
              }
            />
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
          </Route>
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
