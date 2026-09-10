// Модуль «Вахта» (task_vahta): табель охраны на постах.
//
// Свой раздел, но данные общие: охранник — обычная позиция сотрудника, а
// результат расчёта попадает в общую ведомость «Расчёт ЗП».
import type {
  VahtaCandidate,
  VahtaCrew,
  VahtaDepartment,
  VahtaMonth,
  VahtaPost,
  VahtaSimilarEmployee,
  VahtaSite,
  VahtaZone,
} from '../types/api'
import { apiClient } from './client'

// ── Табель ────────────────────────────────────────────────────────────────
export const getVahtaMonth = (year: number, month: number, departmentId?: number | null) =>
  apiClient
    .get<VahtaMonth>(`/api/vahta/${year}/${month}`, {
      params: departmentId ? { department_id: departmentId } : undefined,
    })
    .then((r) => r.data)

export const getVahtaDepartments = () =>
  apiClient.get<VahtaDepartment[]>('/api/vahta/departments').then((r) => r.data)

// ── Зоны обслуживания: верхний уровень справочника ────────────────────────
export const listVahtaZones = () =>
  apiClient.get<VahtaZone[]>('/api/vahta/zones').then((r) => r.data)

export const createVahtaZone = (data: { name: string; department_id: number }) =>
  apiClient.post<VahtaZone>('/api/vahta/zones', data).then((r) => r.data)

export const updateVahtaZone = (
  id: number,
  data: Partial<{ name: string; sort_order: number; is_active: boolean }>,
) => apiClient.patch<VahtaZone>(`/api/vahta/zones/${id}`, data).then((r) => r.data)

export const deleteVahtaZone = (id: number) => apiClient.delete(`/api/vahta/zones/${id}`)

// ── Справочник ────────────────────────────────────────────────────────────
export const listVahtaCrews = () =>
  apiClient.get<VahtaCrew[]>('/api/vahta/crews').then((r) => r.data)

export const createVahtaCrew = (data: {
  name: string
  /** Зона экипажа: за её пределы он не выезжает. */
  zone_id: number
  shift_rate?: string
  shares?: { company_id: number; percent: string }[]
}) => apiClient.post<VahtaCrew>('/api/vahta/crews', data).then((r) => r.data)

export const updateVahtaCrew = (
  id: number,
  data: Partial<{
    name: string
    shift_rate: string
    sort_order: number
    is_active: boolean
    shares: { company_id: number; percent: string }[]
  }>,
) => apiClient.patch<VahtaCrew>(`/api/vahta/crews/${id}`, data).then((r) => r.data)

export const deleteVahtaCrew = (id: number) => apiClient.delete(`/api/vahta/crews/${id}`)

// ── Объекты: ставка по умолчанию и распределение по юрлицам ───────────────
export const listVahtaSites = () =>
  apiClient.get<VahtaSite[]>('/api/vahta/sites').then((r) => r.data)

export const createVahtaSite = (data: {
  name: string
  /** Зона обслуживания; отдел охраны берётся у неё. */
  zone_id: number
  shift_rate?: string
  shares?: { company_id: number; percent: string }[]
}) => apiClient.post<VahtaSite>('/api/vahta/sites', data).then((r) => r.data)

export const updateVahtaSite = (
  id: number,
  data: Partial<{
    name: string
    /** Перенос объекта в другую зону. */
    zone_id: number
    shift_rate: string
    is_active: boolean
    shares: { company_id: number; percent: string }[]
  }>,
) => apiClient.patch<VahtaSite>(`/api/vahta/sites/${id}`, data).then((r) => r.data)

export const deleteVahtaSite = (id: number) => apiClient.delete(`/api/vahta/sites/${id}`)

// ── Посты внутри объектов ─────────────────────────────────────────────────
export const listVahtaPosts = () =>
  apiClient.get<VahtaPost[]>('/api/vahta/posts').then((r) => r.data)

export const createVahtaPost = (data: {
  site_id: number
  name: string
  /** null — ставка берётся у объекта. */
  shift_rate?: string | null
}) => apiClient.post<VahtaPost>('/api/vahta/posts', data).then((r) => r.data)

export const updateVahtaPost = (
  id: number,
  data: Partial<{ name: string; shift_rate: string | null; is_active: boolean }>,
) => apiClient.patch<VahtaPost>(`/api/vahta/posts/${id}`, data).then((r) => r.data)

export const deleteVahtaPost = (id: number) => apiClient.delete(`/api/vahta/posts/${id}`)

// ── Строки табеля ─────────────────────────────────────────────────────────
export const createVahtaAssignment = (data: {
  year: number
  month: number
  /** Место работы: пост объекта ЛИБО выездной экипаж — ровно одно из двух. */
  post_id?: number | null
  crew_id?: number | null
  position_id?: number | null
  employee_id?: number | null
  /** Несколько человек на ОДИН пост сразу — создаётся одной транзакцией. */
  employee_ids?: number[]
  rate?: string | null
  /** Должность строки; не задана — обычная для места. */
  kind?: string | null
}) =>
  apiClient
    .post<{ id?: number; ids: number[]; created: number }>(
      '/api/vahta/assignments',
      data,
    )
    .then((r) => r.data)

export const updateVahtaAssignment = (
  id: number,
  data: Partial<{
    /** Должность строки — правится прямо в табеле. */
    kind: string
    rate: string
    is_official: boolean
    note: string | null
    premium_h1: string
    premium_h2: string
    penalty_h1: string
    penalty_h2: string
    official_payout_h1: string
    official_payout_h2: string
  }>,
) => apiClient.patch(`/api/vahta/assignments/${id}`, data).then((r) => r.data)

export const deleteVahtaAssignment = (id: number) =>
  apiClient.delete(`/api/vahta/assignments/${id}`)

/** Один день выхода: поставить или снять. */
export const setVahtaDay = (assignmentId: number, day: number, value: boolean) =>
  apiClient
    .put('/api/vahta/day', { assignment_id: assignmentId, day, value })
    .then((r) => r.data)

/** «Отметить все» / «снять все» — набор дней строки целиком. */
export const setVahtaDays = (assignmentId: number, days: number[]) =>
  apiClient
    .put('/api/vahta/days', { assignment_id: assignmentId, days })
    .then((r) => r.data)

/** Замена на посту: дни с указанного числа уходят сменщику отдельной строкой. */
export const replaceOnPost = (data: {
  assignment_id: number
  position_id?: number | null
  employee_id?: number | null
  from_day: number
  rate?: string | null
}) =>
  apiClient
    .post<{ assignment_id: number; successor_id: number | null }>('/api/vahta/replace', data)
    .then((r) => r.data)

export const copyPreviousVahtaPeriod = (year: number, month: number) =>
  apiClient
    .post<{ copied: number }>(`/api/vahta/${year}/${month}/copy-previous`)
    .then((r) => r.data)

// ── Быстрый найм ──────────────────────────────────────────────────────────
/** Похожие сотрудники по ФИО — чтобы срочный найм не плодил дубли. */
export const findSimilarEmployees = (fullName: string) =>
  apiClient
    .get<VahtaSimilarEmployee[]>('/api/vahta/similar', { params: { full_name: fullName } })
    .then((r) => r.data)

export const quickHireGuard = (data: {
  full_name: string
  post_id?: number | null
  crew_id?: number | null
  rate?: string | null
  kind?: string | null
  year?: number
  month?: number
  assign?: boolean
}) =>
  apiClient
    .post<{
      employee_id: number
      position_id: number
      tab_number: string | null
      assignment_id: number | null
    }>('/api/vahta/quick-hire', data)
    .then((r) => r.data)

/** Кандидаты окна «Кто сменит на посту»: из других экипажей и свободные. */
export const listVahtaCandidates = (year: number, month: number) =>
  apiClient
    .get<VahtaCandidate[]>(`/api/vahta/${year}/${month}/candidates`)
    .then((r) => r.data)

export const vahtaExcelUrl = (year: number, month: number) =>
  `/api/vahta/${year}/${month}/export/excel`
