export type UserRole = 'admin' | 'manager' | 'accountant' | 'employee'

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
