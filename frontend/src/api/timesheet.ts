import type { Absence, AbsenceKind, Adjustment, AuditLogEntry, AutofillPreview, CompanyShare, DepartmentApplications, NightShift, PayrollStatement, PayrollSummary, TasksResponse, TimesheetCellInput, TimesheetEntry, TimesheetMonthResponse, TimesheetPeriod } from '../types/api'
import { apiClient } from './client'

export const timesheetApi = {
  async getTasks(): Promise<TasksResponse> {
    const { data } = await apiClient.get<TasksResponse>('/api/timesheet/tasks')
    return data
  },

  async getMonth(
    year: number,
    month: number,
    options?: { department_id?: number; include_payroll?: boolean },
  ): Promise<TimesheetMonthResponse> {
    const params: Record<string, unknown> = {}
    if (options?.department_id !== undefined) params.department_id = options.department_id
    if (options?.include_payroll) params.include_payroll = true
    const { data } = await apiClient.get<TimesheetMonthResponse>(`/api/timesheet/${year}/${month}`, { params })
    return data
  },

  async getPayroll(year: number, month: number, departmentId?: number): Promise<PayrollSummary> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<PayrollSummary>(`/api/timesheet/${year}/${month}/payroll`, { params })
    return data
  },

  async saveCell(input: TimesheetCellInput): Promise<TimesheetEntry | null> {
    const { data } = await apiClient.put<TimesheetEntry | null>('/api/timesheet/cell', input)
    return data
  },

  async saveCellsBatch(entries: TimesheetCellInput[]): Promise<(TimesheetEntry | null)[]> {
    const { data } = await apiClient.post<{ entries: (TimesheetEntry | null)[] }>('/api/timesheet/cells/batch', { entries })
    return data.entries
  },

  // ── Отсутствия: код ОТ/ДО/Б/Н на день (kind=null — снять отметку) ──
  async setAbsence(input: {
    employee_id: number; work_date: string; kind: AbsenceKind | null
  }): Promise<Absence | null> {
    const { data } = await apiClient.put<Absence | null>('/api/timesheet/absence', input)
    return data
  },

  // ── Ночные смены: отметка выхода в ночь (value=false — снять) ──
  // Ночная смена не привязана к графику и сосуществует с дневными часами.
  // Превышение фонда отдела бэк блокирует 409-м — сообщение показываем как есть.
  async setNightShift(input: {
    employee_id: number; position_id?: number | null
    work_date: string; value: boolean
  }): Promise<NightShift | null> {
    const { data } = await apiClient.put<NightShift | null>('/api/timesheet/night-shift', input)
    return data
  },

  // ── Личная отметка «строку проверил» (task_pilot_ux ч.3) ──
  // Минимальный запрос: одна отметка, без пересчёта. Фронт правит строку
  // оптимистично и месяц НЕ перезапрашивает.
  async setRowCheck(input: {
    position_id: number; year: number; month: number; value: boolean
  }): Promise<{ position_id: number; year: number; month: number; checked: boolean }> {
    const { data } = await apiClient.put<{
      position_id: number; year: number; month: number; checked: boolean
    }>('/api/timesheet/row-check', input)
    return data
  },

  async submitPeriod(periodId: number): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/submit`)
    return data
  },

  async closePeriod(periodId: number): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/close`)
    return data
  },

  async returnPeriod(periodId: number, reason: string): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/return`, { reason })
    return data
  },

  async reopenPeriod(periodId: number, reason: string): Promise<TimesheetPeriod> {
    const { data } = await apiClient.post<TimesheetPeriod>(`/api/timesheet/periods/${periodId}/reopen`, { reason })
    return data
  },

  async getPeriodHistory(periodId: number): Promise<AuditLogEntry[]> {
    const { data } = await apiClient.get<AuditLogEntry[]>(`/api/timesheet/periods/${periodId}/history`)
    return data
  },

  async autofillPreview(year: number, month: number, departmentId?: number): Promise<AutofillPreview> {
    const { data } = await apiClient.post<AutofillPreview>('/api/timesheet/autofill/preview', {
      year, month, department_id: departmentId ?? null,
    })
    return data
  },

  async autofillApply(year: number, month: number, departmentId?: number): Promise<{ entries_created: number; employees_count: number }> {
    const { data } = await apiClient.post<{ entries_created: number; employees_count: number }>('/api/timesheet/autofill/apply', {
      year, month, department_id: departmentId ?? null,
    })
    return data
  },

  async exportExcel(year: number, month: number, departmentId?: number): Promise<Blob> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<Blob>(`/api/timesheet/${year}/${month}/export/excel`, {
      params,
      responseType: 'blob',
    })
    return data
  },

  // ── Премии / KPI / аванс (задача 3.11a) ──
  // Премии/KPI/аванс за месяц. Нужен отдельно от месяца: после начисления
  // премии перечитывать весь табель (400+ КБ) незачем — меняются только
  // adjustments и суммы расчёта.
  async getAdjustments(year: number, month: number, departmentId?: number): Promise<Adjustment[]> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<Adjustment[]>(
      `/api/timesheet/${year}/${month}/adjustments`, { params },
    )
    return data
  },

  async createAdjustment(input: {
    employee_id: number
    /** рабочее место, на котором заработано; не задано — основное */
    position_id?: number | null
    year: number; month: number
    kind: 'premium' | 'kpi' | 'advance'; amount: string; reason: string
    /**
     * Источник финансирования (task_funding_source): юрлицо, которое эту
     * премию/KPI оплачивает. Сумма уйдёт на его затраты целиком, а каскад
     * распределения поделит остаток начисления. Не задан — как раньше.
     */
    funding_company_id?: number | null
  }): Promise<unknown> {
    const { data } = await apiClient.post('/api/timesheet/adjustments', input)
    return data
  },

  async deleteAdjustment(id: number): Promise<void> {
    await apiClient.delete(`/api/timesheet/adjustments/${id}`)
  },

  // ── Ручная правка удержания по займу за месяц ──
  async setLoanOverride(input: {
    employee_id: number; year: number; month: number; actual_amount: string
  }): Promise<unknown> {
    const { data } = await apiClient.post('/api/timesheet/loan-override', input)
    return data
  },

  async clearLoanOverride(employeeId: number, year: number, month: number): Promise<void> {
    await apiClient.delete(`/api/timesheet/loan-override/${employeeId}/${year}/${month}`)
  },

  // ── Ведомость «Расчёт ЗП» (задача 3.11b) ──
  async getStatement(year: number, month: number, departmentId?: number): Promise<PayrollStatement> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<PayrollStatement>(
      `/api/timesheet/${year}/${month}/statement`, { params },
    )
    return data
  },

  // Распределение задаётся РАБОЧЕМУ МЕСТУ: у совместителя каждое разносится
  // по юрлицам отдельно. Без position_id — основное (как было до позиций).
  async setDistributionOverride(input: {
    employee_id: number; position_id?: number | null
    year: number; month: number; shares: CompanyShare[]
  }): Promise<unknown> {
    const { data } = await apiClient.put('/api/timesheet/distribution', input)
    return data
  },

  async clearDistributionOverride(
    employeeId: number, year: number, month: number, positionId?: number | null,
  ): Promise<void> {
    await apiClient.delete(`/api/timesheet/distribution/${employeeId}/${year}/${month}`, {
      params: positionId != null ? { position_id: positionId } : undefined,
    })
  },

  // ── Заявки на подбор (task_hr_applications) ──
  // Только для отделов с флагом «распределение по заявкам»: их зарплата делится
  // по числу заявок вместо каскада процентов. Набор помесячный и заменяется
  // целиком — что отправили, то и будет.
  async getApplications(
    year: number, month: number, departmentId?: number,
  ): Promise<DepartmentApplications[]> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<DepartmentApplications[]>(
      `/api/timesheet/${year}/${month}/applications`, { params },
    )
    return data
  },

  async setApplications(input: {
    department_id: number; year: number; month: number
    applications: Array<{ company_id: number; in_progress: number; closed: number }>
  }): Promise<DepartmentApplications> {
    const { data } = await apiClient.put<DepartmentApplications>(
      '/api/timesheet/applications', input,
    )
    return data
  },

  async exportStatementExcel(year: number, month: number, departmentId?: number): Promise<Blob> {
    const params: Record<string, unknown> = {}
    if (departmentId !== undefined) params.department_id = departmentId
    const { data } = await apiClient.get<Blob>(
      `/api/timesheet/${year}/${month}/statement/export/excel`,
      { params, responseType: 'blob' },
    )
    return data
  },
}
