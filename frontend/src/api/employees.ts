import type {
  CompanyShare, Employee, EmployeeImportResult, EmployeePosition, EmployeePositionInput,
  EmployeeShares, PayType, UserRole, WeekendPayType,
} from '../types/api'
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
  pay_type?: PayType
  rate?: string | null
  shift_rate?: string | null
  hour_rate?: string | null
  weekend_pay_type?: WeekendPayType
  weekend_coefficient?: string | null
  weekend_fixed_rate?: string | null
  holiday_pay_type?: WeekendPayType
  holiday_coefficient?: string | null
  holiday_fixed_rate?: string | null
  overtime_coefficient?: string | null
  loan_amount?: string | null
  loan_term_months?: number | null
  loan_start_date?: string | null
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
  pay_type: PayType
  rate: string | null
  shift_rate: string | null
  hour_rate: string | null
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  holiday_pay_type: WeekendPayType
  holiday_coefficient: string | null
  holiday_fixed_rate: string | null
  overtime_coefficient: string | null
  loan_amount: string | null
  loan_term_months: number | null
  loan_start_date: string | null
  is_active: boolean
  hire_date: string | null
  dismissal_date: string | null
  is_system_admin: boolean
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

// ── Импорт из Excel (task_employee_import) ──

export const downloadImportTemplate = () =>
  apiClient
    .get<Blob>('/api/employees/import/template', { responseType: 'blob' })
    .then((r) => r.data)

/** confirm=false — превью со статусами строк; confirm=true — создать валидные.
 *  Подтверждение шлёт тот же файл: сервер валидирует его заново, а не верит превью. */
export const importEmployees = (file: File, confirm = false) => {
  const form = new FormData()
  form.append('file', file)
  return apiClient
    .post<EmployeeImportResult>('/api/employees/import', form, { params: { confirm } })
    .then((r) => r.data)
}

// ── Позиции (рабочие места) сотрудника — task_positions ч.B ──
// Совместитель = несколько позиций. Читать может любой, кто видит карточку;
// править — только admin.

export const listPositions = (employeeId: number) =>
  apiClient.get<EmployeePosition[]>(`/api/employees/${employeeId}/positions`).then((r) => r.data)

export const createPosition = (employeeId: number, data: EmployeePositionInput) =>
  apiClient.post<EmployeePosition>(`/api/employees/${employeeId}/positions`, data).then((r) => r.data)

export const updatePosition = (
  employeeId: number, positionId: number, data: EmployeePositionInput,
) =>
  apiClient
    .patch<EmployeePosition>(`/api/employees/${employeeId}/positions/${positionId}`, data)
    .then((r) => r.data)

/** Переназначить основную позицию — возвращает весь список в новом порядке. */
export const makePositionPrimary = (employeeId: number, positionId: number) =>
  apiClient
    .post<EmployeePosition[]>(`/api/employees/${employeeId}/positions/${positionId}/make-primary`)
    .then((r) => r.data)

/** «deleted» — позиция удалена; «deactivated» — на ней есть часы/начисления. */
export const deletePosition = (employeeId: number, positionId: number) =>
  apiClient
    .delete<{ result: 'deleted' | 'deactivated' }>(`/api/employees/${employeeId}/positions/${positionId}`)
    .then((r) => r.data)

// ── Распределение по компаниям по умолчанию (задача 3.11b) ──
// Проценты задаются РАБОЧЕМУ МЕСТУ: у совместителя каждое разносится отдельно.
// Без position_id — основная позиция (как было до совместительства).
export const getCompanyShares = (id: number, positionId?: number | null) =>
  apiClient
    .get<EmployeeShares>(`/api/employees/${id}/company-shares`, {
      params: positionId != null ? { position_id: positionId } : undefined,
    })
    .then((r) => r.data)

export const setCompanyShares = (id: number, shares: CompanyShare[], positionId?: number | null) =>
  apiClient
    .put<EmployeeShares>(`/api/employees/${id}/company-shares`, {
      shares, position_id: positionId ?? null,
    })
    .then((r) => r.data)
