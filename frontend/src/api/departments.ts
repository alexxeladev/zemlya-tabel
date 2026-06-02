import type { Department } from '../types/api'
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
