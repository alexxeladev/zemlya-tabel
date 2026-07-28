import type { Schedule, SchedulePreview, ScheduleType } from '../types/api'
import { apiClient } from './client'

/** Поля графика, общие для создания и правки (task_shift_schedules). */
export interface ScheduleInput {
  name: string
  hours_per_shift: number
  schedule_type: ScheduleType
  work_weekdays?: number[] | null
  cycle_start_date?: string | null
  cycle_work_days?: number | null
  cycle_off_days?: number | null
  description?: string | null
}

/** Тело превью: график ещё не сохранён, поэтому передаются поля формы. */
export interface SchedulePreviewInput extends Partial<ScheduleInput> {
  year: number
  month: number
}

export const listSchedules = () =>
  apiClient.get<Schedule[]>('/api/schedules').then((r) => r.data)

export const getSchedule = (id: number) =>
  apiClient.get<Schedule>(`/api/schedules/${id}`).then((r) => r.data)

export const createSchedule = (data: ScheduleInput) =>
  apiClient.post<Schedule>('/api/schedules', data).then((r) => r.data)

export const updateSchedule = (id: number, data: Partial<ScheduleInput> & { is_active?: boolean }) =>
  apiClient.patch<Schedule>(`/api/schedules/${id}`, data).then((r) => r.data)

export const deleteSchedule = (id: number) =>
  apiClient.delete(`/api/schedules/${id}`)

export const previewSchedule = (data: SchedulePreviewInput) =>
  apiClient.post<SchedulePreview>('/api/schedules/preview', data).then((r) => r.data)
