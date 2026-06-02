import type { Schedule } from '../types/api'
import { apiClient } from './client'

export const listSchedules = () =>
  apiClient.get<Schedule[]>('/api/schedules').then((r) => r.data)

export const getSchedule = (id: number) =>
  apiClient.get<Schedule>(`/api/schedules/${id}`).then((r) => r.data)

export const createSchedule = (data: { name: string; hours_per_shift: number; description?: string | null }) =>
  apiClient.post<Schedule>('/api/schedules', data).then((r) => r.data)

export const updateSchedule = (id: number, data: Partial<{ name: string; hours_per_shift: number; description: string | null; is_active: boolean }>) =>
  apiClient.patch<Schedule>(`/api/schedules/${id}`, data).then((r) => r.data)

export const deleteSchedule = (id: number) =>
  apiClient.delete(`/api/schedules/${id}`)
