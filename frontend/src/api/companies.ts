import type { Company } from '../types/api'
import { apiClient } from './client'

export const listCompanies = () =>
  apiClient.get<Company[]>('/api/companies').then((r) => r.data)

export const getCompany = (id: number) =>
  apiClient.get<Company>(`/api/companies/${id}`).then((r) => r.data)

export const createCompany = (data: {
  code: string
  name: string
  inn?: string | null
  short_name?: string | null
}) =>
  apiClient.post<Company>('/api/companies', data).then((r) => r.data)

export const updateCompany = (
  id: number,
  data: Partial<{
    code: string
    name: string
    inn: string | null
    short_name: string | null
    sort_order: number
    is_active: boolean
  }>,
) =>
  apiClient.patch<Company>(`/api/companies/${id}`, data).then((r) => r.data)

export const deleteCompany = (id: number) =>
  apiClient.delete(`/api/companies/${id}`)

/** Порядок перечисления юрлиц целиком (стрелки ↑/↓ в «Оргструктуре»).
 *  Шлём ПОЛНЫЙ список id: порядок один на всю систему, частичная перестановка
 *  оставила бы совпадающие sort_order. */
export const reorderCompanies = (companyIds: number[]) =>
  apiClient.put<Company[]>('/api/companies/order', { company_ids: companyIds }).then((r) => r.data)
