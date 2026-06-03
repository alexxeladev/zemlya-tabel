import type { AuditLogEntry, TimesheetCellInput, TimesheetEntry, TimesheetMonthResponse, TimesheetPeriod } from '../types/api'
import { apiClient } from './client'

export const timesheetApi = {
  async getMonth(year: number, month: number, departmentId?: number): Promise<TimesheetMonthResponse> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<TimesheetMonthResponse>(`/api/timesheet/${year}/${month}`, { params })
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
}
