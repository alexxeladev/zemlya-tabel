import type { CompanyShare, Department, DepartmentShares } from '../types/api'
import { apiClient } from './client'

export const listDepartments = () =>
  apiClient.get<Department[]>('/api/departments').then((r) => r.data)

export const getDepartment = (id: number) =>
  apiClient.get<Department>(`/api/departments/${id}`).then((r) => r.data)

export const createDepartment = (data: { name: string; code: string }) =>
  apiClient.post<Department>('/api/departments', data).then((r) => r.data)

export const updateDepartment = (id: number, data: Partial<{ name: string; code: string; is_active: boolean }>) =>
  apiClient.patch<Department>(`/api/departments/${id}`, data).then((r) => r.data)

export const deleteDepartment = (id: number) =>
  apiClient.delete(`/api/departments/${id}`)

// ── Дефолт распределения по юрлицам на уровне отдела (task_distribution_v2 ч.3) ──
export const getDepartmentShares = (id: number) =>
  apiClient.get<DepartmentShares>(`/api/departments/${id}/company-shares`).then((r) => r.data)

export const setDepartmentShares = (id: number, shares: CompanyShare[]) =>
  apiClient.put<DepartmentShares>(`/api/departments/${id}/company-shares`, { shares }).then((r) => r.data)
