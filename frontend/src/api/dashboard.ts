import { apiClient } from './client'
import type { PeriodStatus } from '../types/api'

// Decimal с бэка приходят строками (как в payroll)

export interface HoursSummary {
  total_hours: string
  norm_hours: string | null
  overtime_hours: string
  percent_of_norm: string | null
}

export interface DepartmentHours {
  department_id: number | null
  department_name: string
  total_hours: string
  norm_hours: string | null
  overtime_hours: string
}

export interface PayrollTotals {
  total: string
  base: string
  overtime: string
  off_schedule: string
  holiday: string
  // Σ хвостов округления «к выплате» вниз до 100 ₽ за период
  rounding_effect: string
  non_calculable_employees: number
}

export interface DepartmentPayroll {
  department_id: number | null
  department_name: string
  total: string
}

export interface CompanyPayroll {
  company_id: number
  company_code: string
  company_name: string
  total: string
}

export interface PeriodCounts {
  closed: number
  pending_review: number
  draft: number
  overdue: number
}

export interface PeriodStatusRow {
  period_id: number | null
  department_id: number | null
  department_name: string
  year: number
  month: number
  status: PeriodStatus
  submitted_by_name: string | null
  closed_by_name: string | null
  is_overdue: boolean
}

export interface PeriodsBlock {
  counts: PeriodCounts
  rows: PeriodStatusRow[]
  overdue_rows: PeriodStatusRow[]
}

export interface TrendPoint {
  year: number
  month: number
  total_hours: string
  overtime_hours: string
  payroll_total: string | null
}

export interface DashboardData {
  /** начало периода; конец — to_year/to_month (включительно) */
  year: number
  month: number
  to_year: number
  to_month: number
  months_count: number
  role: string
  hours: HoursSummary
  hours_by_department: DepartmentHours[]
  payroll: PayrollTotals | null
  payroll_by_department: DepartmentPayroll[]
  payroll_by_company: CompanyPayroll[]
  periods: PeriodsBlock | null
  trend: TrendPoint[]
}

/**
 * Дашборд за месяц или за диапазон месяцев.
 *
 * (year, month) — начало периода. Конец (toYear/toMonth) передаётся только если
 * период длиннее месяца: без него бэк отвечает ровно как раньше.
 */
export async function getDashboard(
  year: number,
  month: number,
  toYear?: number,
  toMonth?: number,
): Promise<DashboardData> {
  const range =
    toYear !== undefined && toMonth !== undefined && (toYear !== year || toMonth !== month)
      ? { to_year: toYear, to_month: toMonth }
      : undefined
  const { data } = await apiClient.get<DashboardData>(
    `/api/dashboard/${year}/${month}`, { params: range },
  )
  return data
}
