import type { MonthData, MonthSummary, ProductionCalendar } from '../types/api'
import { apiClient } from './client'

export const getCalendar = (year: number): Promise<ProductionCalendar> =>
  apiClient.get<ProductionCalendar>(`/api/calendar/${year}`).then((r) => r.data)

export const loadCalendar = (year: number): Promise<ProductionCalendar> =>
  apiClient.post<ProductionCalendar>(`/api/calendar/${year}/load`, {}).then((r) => r.data)

export const importCalendar = (payload: {
  year: number
  months: MonthData[]
}): Promise<ProductionCalendar> =>
  apiClient.post<ProductionCalendar>('/api/calendar/import', payload).then((r) => r.data)

export const getMonthSummary = (year: number, month: number): Promise<MonthSummary> =>
  apiClient.get<MonthSummary>(`/api/calendar/${year}/${month}/summary`).then((r) => r.data)
