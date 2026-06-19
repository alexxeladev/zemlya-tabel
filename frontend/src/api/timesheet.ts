import type { AuditLogEntry, AutofillPreview, PayrollSummary, TasksResponse, TimesheetCellInput, TimesheetEntry, TimesheetMonthResponse, TimesheetPeriod } from '../types/api'
import { apiClient } from './client'

export const timesheetApi = {
  async getTasks(): Promise<TasksResponse> {
    const { data } = await apiClient.get<TasksResponse>('/api/timesheet/tasks')
    return data
  },

  async getMonth(
    year: number,
    month: number,
    options?: { department_id?: number; include_payroll?: boolean },
  ): Promise<TimesheetMonthResponse> {
    const params: Record<string, unknown> = {}
    if (options?.department_id !== undefined) params.department_id = options.department_id
    if (options?.include_payroll) params.include_payroll = true
    const { data } = await apiClient.get<TimesheetMonthResponse>(`/api/timesheet/${year}/${month}`, { params })
    return data
  },

  async getPayroll(year: number, month: number, departmentId?: number): Promise<PayrollSummary> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<PayrollSummary>(`/api/timesheet/${year}/${month}/payroll`, { params })
    return data
  },

  async saveCell(input: TimesheetCellInput): Promise<TimesheetEntry | null> {
    const { data } = await apiClient.put<TimesheetEntry | null>('/api/timesheet/cell', input)
    return data
  },

  async saveCellsBatch(entries: TimesheetCellInput[]): Promise<(TimesheetEntry | null)[]> {
    const { data } = await apiClient.post<{ entries: (TimesheetEntry | null)[] }>('/api/timesheet/cells/batch', { entries })
    return data.entries
  },

  async submitPeriod(periodId: number): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/submit`)
    return data
  },

  async closePeriod(periodId: number): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/close`)
    return data
  },

  async returnPeriod(periodId: number, reason: string): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/return`, { reason })
    return data
  },

  async reopenPeriod(periodId: number, reason: string): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/reopen`, { reason })
    return data
  },

  async getPeriodHistory(periodId: number): Promise<AuditLogEntry[]> {
    const { data } = await apiClient.get<AuditLogEntry[]>(`/api/timesheet/periods/${periodId}/history`)
    return data
  },

  async autofillPreview(year: number, month: number, departmentId?: number): Promise<AutofillPreview> {
    const { data } = await apiClient.post<AutofillPreview>('/api/timesheet/autofill/preview', {
      year, month, department_id: departmentId ?? null,
    })
    return data
  },

  async autofillApply(year: number, month: number, departmentId?: number): Promise<{ entries_created: number; employees_count: number }> {
    const { data } = await apiClient.post<{ entries_created: number; employees_count: number }>('/api/timesheet/autofill/apply', {
      year, month, department_id: departmentId ?? null,
    })
    return data
  },

  async exportExcel(year: number, month: number, departmentId?: number): Promise<Blob> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<Blob>(`/api/timesheet/${year}/${month}/export/excel`, {
      params,
      responseType: 'blob',
    })
    return data
  },

  // ── Премии / KPI / аванс (задача 3.11a) ──
  async createAdjustment(input: {
    employee_id: number; year: number; month: number
    kind: 'premium' | 'kpi' | 'advance'; amount: string; reason: string
  }): Promise<unknown> {
    const { data } = await apiClient.post('/api/timesheet/adjustments', input)
    return data
  },

  async deleteAdjustment(id: number): Promise<void> {
    await apiClient.delete(`/api/timesheet/adjustments/${id}`)
  },

  // ── Ручная правка удержания по займу за месяц ──
  async setLoanOverride(input: {
    employee_id: number; year: number; month: number; actual_amount: string
  }): Promise<unknown> {
    const { data } = await apiClient.post('/api/timesheet/loan-override', input)
    return data
  },

  async clearLoanOverride(employeeId: number, year: number, month: number): Promise<void> {
    await apiClient.delete(`/api/timesheet/loan-override/${employeeId}/${year}/${month}`)
  },
}
