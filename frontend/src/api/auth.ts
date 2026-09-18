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

/** Смена своего пароля отзывает ВСЕ выданные токены, включая текущий
 *  (task_stage2_access п.2.4) — сервер отвечает новым, его надо сохранить. */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/api/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
  return data
}
