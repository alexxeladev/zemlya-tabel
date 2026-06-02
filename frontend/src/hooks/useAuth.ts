import { useAuthStore } from '../store/auth'

export function useAuth() {
  const user = useAuthStore((s) => s.user)
  const role = user?.role ?? null

  return {
    user,
    role,
    canAdmin: () => role === 'admin',
    canManage: () => role === 'admin' || role === 'manager',
    isManager: () => role === 'manager',
  }
}
