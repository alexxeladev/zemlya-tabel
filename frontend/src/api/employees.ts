import type { Employee } from '../types/api'
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
  department_id: number
  schedule_id: number
  default_company_id: number
  rate: string
  is_active?: boolean
  hire_date?: string | null
  dismissal_date?: string | null
}) => apiClient.post<Employee>('/api/employees', data).then((r) => r.data)

export const updateEmployee = (id: number, data: Partial<{
  tab_number: string | null
  full_name: string
  position: string | null
  department_id: number
  schedule_id: number
  default_company_id: number
  rate: string
  is_active: boolean
  hire_date: string | null
  dismissal_date: string | null
}>) => apiClient.patch<Employee>(`/api/employees/${id}`, data).then((r) => r.data)

export const deleteEmployee = (id: number) =>
  apiClient.delete(`/api/employees/${id}`)
