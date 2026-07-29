export type UserRole = 'admin' | 'manager' | 'accountant' | 'employee'
export type WeekendPayType = 'coefficient' | 'fixed_rate'

export interface CompanyBreakdown {
  company_id: number
  company_code: string
  company_name: string
  hours: string
  percent: string
  base_amount: string
  overtime_amount: string
  off_schedule_amount: string
  holiday_amount: string
  total: string
}

export interface EmployeePayroll {
  employee_id: number
  employee_name: string
  /** у посменного здесь условный оклад = ставка × норма смен */
  rate: string | null
  schedule_name: string | null
  pay_type: PayType
  shift_rate: string | null
  worked_shifts: number
  norm_shifts: number | null
  total_hours: string
  norm_hours: string | null
  delta_hours: string | null
  overtime_hours: string
  /** выход в свой выходной по графику */
  off_schedule_hours: string
  /** работа в нерабочий праздничный день календаря */
  holiday_hours: string
  norm_days: number | null
  fact_days: number
  hourly_rate: string | null
  base_amount: string
  overtime_amount: string
  off_schedule_amount: string
  holiday_amount: string
  total_amount: string
  // Отсутствия: дни по видам и оплата ОТ/Б
  vacation_days: number
  unpaid_days: number
  sick_days: number
  absent_days: number
  vacation_paid_days: number
  sick_paid_days: number
  vacation_amount: string
  sick_amount: string
  // Годовой лимит больничного
  sick_limit_days: number
  sick_days_used_before: number
  sick_unpaid_days: number
  sick_limit_remaining: number
  weekend_pay_type: WeekendPayType | null
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  holiday_pay_type: WeekendPayType | null
  holiday_coefficient: string | null
  holiday_fixed_rate: string | null
  premium_amount: string
  kpi_amount: string
  advance_deduction: string
  loan_deduction: string
  loan_remaining: string
  loan_planned_deduction: string
  loan_is_manual: boolean
  total_deductions: string
  // net_payout округлён вниз до 100 ₽; exact/tail — справочно
  net_payout: string
  net_payout_exact: string
  rounding_tail: string
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
  total_off_schedule_amount: string
  total_holiday_amount: string
  total_vacation_amount: string
  total_sick_amount: string
  grand_total: string
  total_premium: string
  total_kpi: string
  total_deductions: string
  total_net_payout: string
  total_net_payout_exact: string
  total_rounding_tail: string
}

// ── Payroll statement (задача 3.11b) ──
export interface CompanyShare {
  company_id: number
  percent: string
}

export interface EmployeeShares {
  employee_id: number
  shares: CompanyShare[]
  percent_sum: string
  // Дефолт отдела — наследуется, если своего распределения нет (каскад, ч.3)
  department_id: number | null
  department_name: string | null
  department_shares: CompanyShare[]
  inherits_department: boolean
}

export interface DepartmentShares {
  department_id: number
  shares: CompanyShare[]
  percent_sum: string
}

/** Уровень каскада, откуда взято распределение по юрлицам. */
export type DistributionSource = 'month' | 'employee' | 'department' | 'hours'

export interface StatementCompanyRef {
  id: number
  code: string
  name: string
}

export interface StatementCompanyAmount {
  company_id: number
  percent: string
  amount: string
}

export interface StatementRow {
  employee_id: number
  tab_number: string | null
  employee_name: string
  main_company_id: number | null
  main_company_name: string | null
  department_name: string | null
  position: string | null
  schedule_name: string | null
  /** у посменного здесь условный оклад = ставка × норма смен */
  rate: string | null
  pay_type: PayType
  shift_rate: string | null
  worked_shifts: number
  norm_shifts: number | null
  norm_hours: string | null
  fact_hours: string
  overtime_coefficient: string
  overtime_hours: string
  overtime_amount: string
  base_salary: string
  premium_amount: string
  kpi_amount: string
  premium_extra_amount: string
  vacation_days: number
  sick_days: number
  unpaid_days: number
  absent_days: number
  vacation_amount: string
  sick_amount: string
  sick_limit_days: number
  sick_unpaid_days: number
  sick_limit_remaining: number
  accrued_total: string
  deductions: string
  net_payout: string        // округлено вниз до 100 ₽
  net_payout_exact: string
  rounding_tail: string
  is_overridden: boolean
  is_auto_distributed: boolean
  distribution_source: DistributionSource
  percent_sum: string
  distribution: StatementCompanyAmount[]
  distribution_total: string
  is_calculable: boolean
  note: string | null
}

export interface PayrollStatement {
  year: number
  month: number
  companies: StatementCompanyRef[]
  rows: StatementRow[]
  total_overtime_amount: string
  total_base_salary: string
  total_vacation_amount: string
  total_sick_amount: string
  total_premium: string
  total_kpi: string
  total_accrued: string
  total_deductions: string
  total_net_payout: string
  total_net_payout_exact: string
  total_rounding_tail: string
  distribution_totals: Record<number, string>
}

export type AdjustmentKind = 'premium' | 'kpi' | 'advance'

export interface Adjustment {
  id: number
  employee_id: number
  year: number
  month: number
  kind: AdjustmentKind
  amount: string
  reason: string
  created_by_id: number | null
  created_at: string | null
}

export interface TimesheetEntry {
  employee_id: number
  work_date: string  // YYYY-MM-DD
  company_id: number
  hours: number  // decimal as number
}

// ── Отсутствия: коды ОТ / ДО / Б / Н ──
export type AbsenceKind = 'vacation' | 'unpaid' | 'sick' | 'absent'

export interface Absence {
  employee_id: number
  work_date: string  // YYYY-MM-DD
  kind: AbsenceKind
  code: string       // ОТ / ДО / Б / Н
  // Больничный сверх годового лимита — отмечен, но не оплачивается
  over_limit: boolean
}

export interface TimesheetMonthResponse {
  year: number
  month: number
  employees: Employee[]
  companies: Company[]
  entries: TimesheetEntry[]
  periods: TimesheetPeriod[]
  extra_companies_by_employee: Record<string, number[]>
  absences: Absence[]
  payroll: PayrollSummary | null
  adjustments: Adjustment[]
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

export type PayType = 'salary' | 'per_shift'

export type ScheduleType = 'weekday' | 'cyclic'

export interface Schedule {
  id: number
  name: string
  hours_per_shift: number
  /** weekday — по дням недели (5/2, 6/1, вс–чт); cyclic — скользящий цикл (2/2, 3/3) */
  schedule_type: ScheduleType
  /** weekday: рабочие дни недели, 0=Пн … 6=Вс. null → выводятся из имени «N/M» */
  work_weekdays: number[] | null
  /** cyclic: анкер фазы цикла */
  cycle_start_date: string | null
  cycle_work_days: number | null
  cycle_off_days: number | null
  description: string | null
  is_active: boolean
}

export interface SchedulePreviewDay {
  day: number
  work_date: string
  weekday: number
  is_work_day: boolean
  hours: number
  is_holiday: boolean
  is_short_day: boolean
}

export interface SchedulePreview {
  year: number
  month: number
  days: SchedulePreviewDay[]
  work_days: number
  norm_hours: number
  has_calendar: boolean
  issue: string | null
}

export interface Employee {
  id: number
  tab_number: string | null
  full_name: string
  position: string | null
  department_id: number | null
  schedule_id: number | null
  default_company_id: number | null
  /** salary — месячный оклад; per_shift — смены × ставка */
  pay_type: PayType
  rate: string | null
  shift_rate: string | null
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  holiday_pay_type: WeekendPayType
  holiday_coefficient: string | null
  holiday_fixed_rate: string | null
  overtime_coefficient: string | null
  loan_amount: string | null
  loan_term_months: number | null
  loan_start_date: string | null
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
