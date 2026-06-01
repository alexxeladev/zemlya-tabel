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
