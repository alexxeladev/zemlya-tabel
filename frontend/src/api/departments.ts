import type {
  CompanyShare,
  Department,
  DepartmentManagers,
  DepartmentMovePreview,
  DepartmentMoveResult,
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
  /** фонд ночных смен на месяц; не задан — дефолт 100 000 */
  night_shift_fund?: string | null
  /** делить зарплату отдела по заявкам на подбор вместо каскада процентов */
  uses_applications_distribution?: boolean
}) => apiClient.post<Department>('/api/departments', data).then((r) => r.data)

export const updateDepartment = (
  id: number,
  data: Partial<{
    name: string
    code: string
    head_company_id: number | null
    night_shift_fund: string
    uses_applications_distribution: boolean
    is_active: boolean
  }>,
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


// ── Перенос отдела в другую компанию (task_move_department) ──────────────────
/** Что будет затронуто переносом. Ничего не меняет — только для диалога. */
export const previewDepartmentMove = (id: number, targetCompanyId: number) =>
  apiClient
    .get<DepartmentMovePreview>(`/api/departments/${id}/move-preview`, {
      params: { target_company_id: targetCompanyId },
    })
    .then((r) => r.data)

/** Перенести отдел: головная компания + компании рабочих мест ЭТОГО отдела. */
export const moveDepartment = (id: number, targetCompanyId: number) =>
  apiClient
    .post<DepartmentMoveResult>(`/api/departments/${id}/move`, {
      target_company_id: targetCompanyId,
    })
    .then((r) => r.data)
