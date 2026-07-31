import type { OrgTree } from '../types/api'
import { apiClient } from './client'

/** Дерево Компания → Отдел → Сотрудники (task_org_structure ч.3). Только admin. */
export const getOrgTree = (includeInactive = false) =>
  apiClient
    .get<OrgTree>('/api/org/tree', { params: { include_inactive: includeInactive } })
    .then((r) => r.data)
