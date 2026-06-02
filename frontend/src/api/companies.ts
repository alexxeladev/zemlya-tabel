import type { Company } from '../types/api'
import { apiClient } from './client'

export const listCompanies = () =>
  apiClient.get<Company[]>('/api/companies').then((r) => r.data)

export const getCompany = (id: number) =>
  apiClient.get<Company>(`/api/companies/${id}`).then((r) => r.data)

export const createCompany = (data: { code: string; name: string; inn?: string | null }) =>
  apiClient.post<Company>('/api/companies', data).then((r) => r.data)

export const updateCompany = (id: number, data: Partial<{ code: string; name: string; inn: string | null; is_active: boolean }>) =>
  apiClient.patch<Company>(`/api/companies/${id}`, data).then((r) => r.data)

export const deleteCompany = (id: number) =>
  apiClient.delete(`/api/companies/${id}`)
