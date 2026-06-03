import type { Employee, UserRole } from '../types/api'
import { apiClient } from './client'

export interface EmployeeListParams {
  department_id?: number
  is_active?: boolean
  search?: string
}

export const listEmployees = (params?: EmployeeListParams) =>
  apiClient.get<Employee[]>('/api/employees', { params }).then((r) => r.data)

export const getEmployee = (id: number) =>
  apiClient.get<Employee>(`/api/employees/${id}`).then((r) => r.data)

export const createEmployee = (data: {
  tab_number?: string | null
  full_name: string
  position?: string | null
  department_id?: number | null
  schedule_id?: number | null
  default_company_id?: number | null
  rate?: string | null
  is_active?: boolean
  hire_date?: string | null
  dismissal_date?: string | null
  access?: { email: string; role: UserRole; initial_password: string } | null
}) => apiClient.post<Employee>('/api/employees', data).then((r) => r.data)

export const updateEmployee = (id: number, data: Partial<{
  tab_number: string | null
  full_name: string
  position: string | null
  department_id: number | null
  schedule_id: number | null
  default_company_id: number | null
  rate: string | null
  is_active: boolean
  hire_date: string | null
  dismissal_date: string | null
}>) => apiClient.patch<Employee>(`/api/employees/${id}`, data).then((r) => r.data)

export const deleteEmployee = (id: number) =>
  apiClient.delete(`/api/employees/${id}`)

export const grantAccess = (id: number, data: { email: string; role: UserRole; initial_password: string }) =>
  apiClient.post<Employee>(`/api/employees/${id}/access`, data).then((r) => r.data)

export const updateRole = (id: number, data: { role: UserRole }) =>
  apiClient.patch<Employee>(`/api/employees/${id}/access`, data).then((r) => r.data)

export const resetPassword = (id: number) =>
  apiClient.post<{ temp_password: string }>(`/api/employees/${id}/reset-password`).then((r) => r.data)

export const revokeAccess = (id: number) =>
  apiClient.delete(`/api/employees/${id}/access`)

export const dismissEmployee = (id: number, dismissal_date: string) =>
  apiClient.post<Employee>(`/api/employees/${id}/dismiss`, { dismissal_date }).then((r) => r.data)

export const rehireEmployee = (id: number) =>
  apiClient.post<Employee>(`/api/employees/${id}/rehire`).then((r) => r.data)
