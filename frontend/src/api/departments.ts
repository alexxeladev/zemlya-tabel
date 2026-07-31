import type {
  CompanyShare,
  Department,
  DepartmentManagers,
  DepartmentShares,
} from '../types/api'
import { apiClient } from './client'

export const listDepartments = () =>
  apiClient.get<Department[]>('/api/departments').then((r) => r.data)

export const getDepartment = (id: number) =>
  apiClient.get<Department>(`/api/departments/${id}`).then((r) => r.data)

export const createDepartment = (data: {
  name: string
  code: string
  head_company_id?: number | null
}) => apiClient.post<Department>('/api/departments', data).then((r) => r.data)

export const updateDepartment = (
  id: number,
  data: Partial<{ name: string; code: string; head_company_id: number | null; is_active: boolean }>,
) => apiClient.patch<Department>(`/api/departments/${id}`, data).then((r) => r.data)

export const deleteDepartment = (id: number) =>
  apiClient.delete(`/api/departments/${id}`)

// ── Менеджеры отдела: many-to-many, управляется со стороны отдела (ч.2) ──
export const getDepartmentManagers = (id: number) =>
  apiClient.get<DepartmentManagers>(`/api/departments/${id}/managers`).then((r) => r.data)

export const setDepartmentManagers = (id: number, employeeIds: number[]) =>
  apiClient
    .put<DepartmentManagers>(`/api/departments/${id}/managers`, { employee_ids: employeeIds })
    .then((r) => r.data)

// ── Дефолт распределения по юрлицам на уровне отдела (task_distribution_v2 ч.3) ──
export const getDepartmentShares = (id: number) =>
  apiClient.get<DepartmentShares>(`/api/departments/${id}/company-shares`).then((r) => r.data)

export const setDepartmentShares = (id: number, shares: CompanyShare[]) =>
  apiClient.put<DepartmentShares>(`/api/departments/${id}/company-shares`, { shares }).then((r) => r.data)
