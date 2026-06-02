import type { Employee, TokenResponse } from '../types/api'
import { apiClient } from './client'

export async function login(email: string, password: string): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/auth/login', { email, password })
  return data
}

export async function getMe(): Promise<Employee> {
  const { data } = await apiClient.get<Employee>('/api/auth/me')
  return data
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await apiClient.post('/api/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}
