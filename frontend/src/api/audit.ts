import { apiClient } from './client'

/**
 * Журнал изменений справочных данных (task_audit_log).
 *
 * Подписи (`field_label`, `entity_type_label`, `source_label`) приходят С БЭКА
 * готовыми: словарь полей живёт в `app/services/reference_audit.py`, и вторая
 * его копия здесь неминуемо разошлась бы с первой при добавлении поля.
 */

export type AuditAction = 'create' | 'update' | 'delete'

export interface ReferenceChange {
  id: number
  created_at: string
  actor_id: number | null
  actor_name: string | null
  source: string
  source_label: string
  /** общий id массовой операции — по нему открывается весь перенос/импорт */
  operation_id: string | null
  entity_type: string
  entity_type_label: string
  entity_id: number | null
  entity_label: string | null
  employee_id: number | null
  action: AuditAction
  field: string | null
  field_label: string | null
  old_value: string | null
  new_value: string | null
}

export interface ReferenceChangePage {
  items: ReferenceChange[]
  total: number
  limit: number
  offset: number
}

export interface AuditFilterOption {
  value: string
  label: string
}

export interface AuditFilters {
  entity_types: AuditFilterOption[]
  sources: AuditFilterOption[]
  actors: AuditFilterOption[]
}

export interface AuditQuery {
  employee_id?: number
  entity_type?: string
  entity_id?: number
  actor_id?: number
  source?: string
  operation_id?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}

/** Журнал ВСЕГДА постраничный: он растёт быстро, «отдай всё» положит экран. */
export const fetchAudit = (query: AuditQuery = {}) =>
  apiClient
    .get<ReferenceChangePage>('/api/audit', { params: query })
    .then((r) => r.data)

export const fetchAuditFilters = () =>
  apiClient.get<AuditFilters>('/api/audit/filters').then((r) => r.data)
