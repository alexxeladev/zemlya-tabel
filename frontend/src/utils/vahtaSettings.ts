/**
 * Адреса настроек вахты (task_vahta_settings_staff).
 *
 * Всё, что в вахте настраивается и ведётся как справочник, — вкладки одного
 * экрана «Настройки». Вкладка, месяц и открытое рабочее место живут в АДРЕСЕ:
 * ссылка открывает ровно то же, перезагрузка ничего не теряет (правило
 * редизайна «состояние адресуемо»). Собирать эти адреса по месту нельзя —
 * только здесь, иначе ссылки из карточки сотрудника, табеля и редиректов со
 * старых путей разъедутся.
 *
 * Без JSX — проверяется `npm test`.
 */

export const VAHTA_SETTINGS_BASE = '/vahta/settings'

/** Порядок — по частоте использования (решение заказчика). */
export const VAHTA_SETTINGS_TABS = ['staff', 'zones', 'titles', 'tax'] as const
export type VahtaSettingsTab = (typeof VAHTA_SETTINGS_TABS)[number]

export const VAHTA_SETTINGS_TAB_LABELS: Record<VahtaSettingsTab, string> = {
  staff: 'Сотрудники охраны',
  zones: 'Зоны, объекты и посты',
  titles: 'Должности',
  tax: 'Налог',
}

/** Вкладки, содержимое которых зависит от месяца: только им нужен выбор месяца. */
export const MONTH_DEPENDENT_TABS: readonly VahtaSettingsTab[] = ['staff', 'zones']

export function parseSettingsTab(value: string | undefined): VahtaSettingsTab | null {
  return (VAHTA_SETTINGS_TABS as readonly string[]).includes(value ?? '')
    ? (value as VahtaSettingsTab)
    : null
}

/** `2026-08` → {2026, 8}; всё прочее — null (месяц тогда берётся из общего периода). */
export function parseMonthParam(value: string | null): { year: number; month: number } | null {
  const m = /^(\d{4})-(\d{2})$/.exec(value ?? '')
  if (!m) return null
  const year = Number(m[1])
  const month = Number(m[2])
  return month >= 1 && month <= 12 ? { year, month } : null
}

export function formatMonthParam(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}

export function vahtaSettingsPath(
  tab: VahtaSettingsTab,
  opts: { year?: number; month?: number; positionId?: number | null } = {},
): string {
  const params = new URLSearchParams()
  if (MONTH_DEPENDENT_TABS.includes(tab) && opts.year && opts.month) {
    params.set('month', formatMonthParam(opts.year, opts.month))
  }
  if (tab === 'staff' && opts.positionId) params.set('position_id', String(opts.positionId))
  const query = params.toString()
  return `${VAHTA_SETTINGS_BASE}/${tab}${query ? `?${query}` : ''}`
}

/**
 * Старые адреса → вкладки. `/vahta/staff` был отдельной страницей сотрудников,
 * `/vahta/posts` — настройками с вкладкой зон по умолчанию. Параметры запроса
 * сохраняются: ссылка с `?position_id=` должна открыть то же рабочее место.
 */
export const LEGACY_VAHTA_SETTINGS_REDIRECTS: Record<string, VahtaSettingsTab> = {
  '/vahta/staff': 'staff',
  '/vahta/posts': 'zones',
}
