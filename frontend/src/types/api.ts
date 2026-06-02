export type UserRole = 'admin' | 'manager' | 'accountant' | 'employee'

export interface User {
  id: number
  email: string
  full_name: string
  role: UserRole
  department_id: number | null
  employee_id: number | null
  is_active: boolean
  must_change_password: boolean
  last_login_at: string | null
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
  department_id: number
  schedule_id: number
  default_company_id: number
  rate: string
  is_active: boolean
  hire_date: string | null
  dismissal_date: string | null
  department: Department | null
  schedule: Schedule | null
  default_company: Company | null
}
