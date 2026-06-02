import type { User, UserRole } from '../types/api'
import { apiClient } from './client'

export interface UserListParams {
  role?: UserRole
  department_id?: number
  is_active?: boolean
}

export const listUsers = (params?: UserListParams) =>
  apiClient.get<User[]>('/api/users', { params }).then((r) => r.data)

export const getUser = (id: number) =>
  apiClient.get<User>(`/api/users/${id}`).then((r) => r.data)

export const createUser = (data: {
  email: string
  full_name: string
  role: UserRole
  password: string
  department_id?: number | null
  employee_id?: number | null
  is_active?: boolean
}) => apiClient.post<User>('/api/users', data).then((r) => r.data)

export const updateUser = (id: number, data: Partial<{
  email: string
  full_name: string
  role: UserRole
  department_id: number | null
  employee_id: number | null
  is_active: boolean
}>) => apiClient.patch<User>(`/api/users/${id}`, data).then((r) => r.data)

export const resetPassword = (id: number) =>
  apiClient.post<{ temp_password: string }>(`/api/users/${id}/reset-password`).then((r) => r.data)

export const deleteUser = (id: number) =>
  apiClient.delete(`/api/users/${id}`)
