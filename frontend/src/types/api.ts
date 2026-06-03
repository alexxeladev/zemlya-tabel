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
}

export interface TimesheetCellInput {
  employee_id: number
  work_date: string  // YYYY-MM-DD
  company_id: number
  hours: number
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
