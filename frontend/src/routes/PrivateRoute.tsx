import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

function Spinner() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <svg className="h-8 w-8 animate-spin text-brand" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
    </div>
  )
}

export function PrivateRoute() {
  const user = useAuthStore((s) => s.user)
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword)
  const isInitialized = useAuthStore((s) => s.isInitialized)
  const location = useLocation()

  if (!isInitialized) return <Spinner />

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (mustChangePassword && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }

  if (!mustChangePassword && location.pathname === '/change-password') {
    return <Navigate to="/dashboard" replace />
  }

  return <Outlet />
}
