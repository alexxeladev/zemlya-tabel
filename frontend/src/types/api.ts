export type UserRole = 'admin' | 'manager' | 'accountant' | 'employee'
export type WeekendPayType = 'coefficient' | 'fixed_rate'

export interface CompanyBreakdown {
  company_id: number
  company_code: string
  company_name: string
  hours: string
  base_amount: string
  overtime_amount: string
  holiday_amount: string
  total: string
}

export interface EmployeePayroll {
  employee_id: number
  employee_name: string
  rate: string | null
  schedule_name: string | null
  total_hours: string
  norm_hours: string | null
  delta_hours: string | null
  overtime_hours: string
  holiday_hours: string
  norm_days: number | null
  fact_days: number
  hourly_rate: string | null
  base_amount: string
  overtime_amount: string
  holiday_amount: string
  total_amount: string
  breakdown_by_company: CompanyBreakdown[]
  is_calculable: boolean
  reason_if_not_calculable: string | null
}

export interface PayrollSummary {
  year: number
  month: number
  employees: EmployeePayroll[]
  total_employees: number
  total_hours: string
  total_base_amount: string
  total_overtime_amount: string
  total_holiday_amount: string
  grand_total: string
}

export interface TimesheetEntry {
  employee_id: number
  work_date: string  // YYYY-MM-DD
  company_id: number
  hours: number  // decimal as number
}

export interface TimesheetMonthResponse {
  year: number
  month: number
  employees: Employee[]
  companies: Company[]
  entries: TimesheetEntry[]
  periods: TimesheetPeriod[]
  extra_companies_by_employee: Record<string, number[]>
  payroll: PayrollSummary | null
}

export interface AutofillSkippedEmployee {
  employee_id: number
  employee_name: string
  reason: string
}

export interface AutofillPreview {
  year: number
  month: number
  entries_to_create: TimesheetCellInput[]
  cells_skipped: number
  employees_processed: number
  employees_skipped: AutofillSkippedEmployee[]
}

export interface TimesheetCellInput {
  employee_id: number
  work_date: string  // YYYY-MM-DD
  company_id: number
  hours: number
}

export type PeriodStatus = 'draft' | 'pending_review' | 'closed'

export interface TimesheetPeriod {
  id: number
  department_id: number | null
  department_name: string | null
  year: number
  month: number
  status: PeriodStatus
  submitted_at: string | null
  submitted_by_name: string | null
  reviewed_at: string | null
  reviewed_by_name: string | null
  closed_at: string | null
  closed_by_name: string | null
  can_edit: boolean
  can_submit: boolean
  can_close: boolean
  can_return: boolean
  can_reopen: boolean
}

export interface PeriodTask {
  period_id: number
  department_id: number | null
  department_name: string
  year: number
  month: number
  status: PeriodStatus
  submitted_by_name: string | null
  submitted_at: string | null
  closed_by_name: string | null
  closed_at: string | null
  total_hours: number
}

export interface TasksResponse {
  pending_review: PeriodTask[]
  recently_closed: PeriodTask[]
}

export interface AuditLogEntry {
  id: number
  actor_id: number
  actor_name: string | null
  entity_type: string
  entity_id: number | null
  action: string
  before: unknown
  after: unknown
  reason: string | null
  created_at: string
}

export interface MonthData {
  month: number
  days: string
}

export interface ProductionCalendar {
  id: number
  year: number
  months: MonthData[]
  source: 'remote' | 'manual'
  loaded_at: string
  workdays_total: number
  short_days_total: number
}

export type DayType = 'work' | 'short' | 'holiday'

export interface DayInfo {
  day: number
  type: DayType
  weekday: number
}

export interface MonthSummary {
  year: number
  month: number
  workdays: number
  short_days: number
  norm_hours_8h: number
  days: DayInfo[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
  must_change_password: boolean
}

export interface Department {
  id: number
  name: string
  code: string
  is_active: boolean
}

export interface Company {
  id: number
  code: string
  name: string
  inn: string | null
  is_active: boolean
}

export interface Schedule {
  id: number
  name: string
  hours_per_shift: number
  description: string | null
  is_active: boolean
}

export interface Employee {
  id: number
  tab_number: string | null
  full_name: string
  position: string | null
  department_id: number | null
  schedule_id: number | null
  default_company_id: number | null
  rate: string | null
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  is_active: boolean
  status: 'active' | 'dismissed'
  hire_date: string | null
  dismissal_date: string | null
  // auth fields
  email: string | null
  role: UserRole | null
  has_access: boolean
  must_change_password: boolean
  last_login_at: string | null
  is_system_admin: boolean
  // nested
  department: Department | null
  schedule: Schedule | null
  default_company: Company | null
}
