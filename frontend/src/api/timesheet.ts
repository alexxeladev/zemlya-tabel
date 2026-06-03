import type { TimesheetCellInput, TimesheetEntry, TimesheetMonthResponse } from '../types/api'
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
}
