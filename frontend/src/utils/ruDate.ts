/**
 * Дата и месяц по-русски (дизайн-система: `DateField`, `MonthPicker`).
 *
 * Отдельным модулем без JSX — чтобы проверяться `npm test` (node:test).
 */

/** ISO `2026-08-14` → `14.08.2026`; пусто → пусто. */
export function isoToRu(iso: string | null | undefined): string {
  if (!iso) return ''
  const [y, m, d] = iso.split('-')
  return y && m && d ? `${d}.${m}.${y}` : ''
}

/**
 * `14.08.2026` (и `14.8.26`, `14/08/2026`) → ISO. `null` — не распознано,
 * `''` — поле пустое (законное «без ограничения»).
 */
export function ruToIso(text: string): string | null {
  const t = text.trim()
  if (!t) return ''
  const m = /^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})$/.exec(t)
  if (!m) return null
  const day = Number(m[1])
  const month = Number(m[2])
  const year = m[3].length === 2 ? 2000 + Number(m[3]) : Number(m[3])
  const date = new Date(Date.UTC(year, month - 1, day))
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null
  }
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

// ── Месяцы ─────────────────────────────────────────────────────

export const MONTHS_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

/** Родительный падеж: «14 августа», «в августе» строится отдельно. */
export const MONTHS_RU_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

/** Предложный падеж: «стоит на посту в августе». */
export const MONTHS_RU_PREP = [
  'январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
  'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре',
]

export function shiftMonth(year: number, month: number, delta: number): { year: number; month: number } {
  const i = year * 12 + (month - 1) + delta
  return { year: Math.floor(i / 12), month: (i % 12) + 1 }
}
