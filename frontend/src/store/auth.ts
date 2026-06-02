import { create } from 'zustand'
import type { Employee } from '../types/api'
import { TOKEN_KEY } from '../api/client'
import { login as apiLogin, getMe } from '../api/auth'

interface AuthState {
  user: Employee | null
  token: string | null
  mustChangePassword: boolean
  isInitialized: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  loadUserFromToken: () => Promise<void>
  refreshUser: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: localStorage.getItem(TOKEN_KEY),
  mustChangePassword: false,
  isInitialized: false,

  login: async (email: string, password: string) => {
    const response = await apiLogin(email, password)
    localStorage.setItem(TOKEN_KEY, response.access_token)
    set({ token: response.access_token, mustChangePassword: response.must_change_password })
    const user = await getMe()
    set({ user })
  },

  logout: () => {
    localStorage.removeItem(TOKEN_KEY)
    set({ user: null, token: null, mustChangePassword: false })
  },

  loadUserFromToken: async () => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (!token) {
      set({ isInitialized: true })
      return
    }
    try {
      set({ token })
      const user = await getMe()
      set({ user, mustChangePassword: user.must_change_password, isInitialized: true })
    } catch {
      localStorage.removeItem(TOKEN_KEY)
      set({ user: null, token: null, mustChangePassword: false, isInitialized: true })
    }
  },

  refreshUser: async () => {
    const user = await getMe()
    set({ user, mustChangePassword: user.must_change_password })
  },
}))
