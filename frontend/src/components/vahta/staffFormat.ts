/**
 * Подписи вкладки «Сотрудники охраны» (task_vahta_settings_staff, шаг 4).
 *
 * Пустое и «не задано» — словами, а не прочерком (правила интерфейса CLAUDE.md).
 * Без JSX — проверяется `npm test`.
 */

/** Русские числительные: «1 человек», «2 человека», «5 человек». */
export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return `${n} ${many}`
  if (b > 1 && b < 5) return `${n} ${few}`
  return `${n} ${b === 1 ? one : many}`
}

/** ISO → ДД.ММ.ГГГГ. */
export function ruDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const [y, m, d] = iso.split('-')
  return y && m && d ? `${d}.${m}.${y}` : ''
}

/** Период на месте: «с 01.03.2026 по 30.09.2026», «с 01.03.2026», «по …», «без ограничений». */
export function placePeriod(hire: string | null, dismissal: string | null): string {
  if (!hire && !dismissal) return 'без ограничений'
  return [hire ? `с ${ruDate(hire)}` : '', dismissal ? `по ${ruDate(dismissal)}` : '']
    .filter(Boolean)
    .join(' ')
}

/** «Официально» за месяц: у человека не на посту признака нет. */
export function officialLabel(value: boolean): string {
  return value ? 'да' : 'нет'
}

/** Каких полей коснулась правка — словами для подписи «Изменено: …». */
export function changedFields<T extends Record<string, string | boolean>>(
  before: T,
  after: T,
  labels: Partial<Record<keyof T, string>>,
): string[] {
  return (Object.keys(labels) as (keyof T)[])
    .filter((k) => (before[k] ?? '') !== (after[k] ?? ''))
    .map((k) => labels[k] as string)
}

// ── Факт месяца по рабочему месту ─────────────────────────────

/** Смены и зарплата за них по рабочему месту за месяц — из строк табеля вахты. */
export type MonthFact = { shifts: number; pay: number }

/**
 * Строки табеля → факт по рабочему месту. Зарплата — `salary` СТРОКИ: её
 * посчитал сервер по ставке строки (своей или от поста/экипажа), а не по ставке
 * рабочего места. У человека на двух строках (замена, два поста) суммируется.
 */
export function monthFactsByPosition(
  rows: { position_id: number | null; days: number[]; salary: string | null }[],
): Map<number, MonthFact> {
  const map = new Map<number, MonthFact>()
  for (const r of rows) {
    if (!r.position_id) continue
    const prev = map.get(r.position_id) ?? { shifts: 0, pay: 0 }
    map.set(r.position_id, {
      shifts: prev.shifts + r.days.length,
      pay: prev.pay + (parseFloat(r.salary ?? '0') || 0),
    })
  }
  return map
}

/** «15 750 ₽» без копеек — как везде в табеле вахты. */
function rub(n: number): string {
  return `${Math.round(n).toLocaleString('ru-RU').replace(/\u00a0/g, ' ')} ₽`
}

/**
 * Подсказка под ставкой: ФАКТ месяца, а не «смены × введённая ставка» — вахта
 * платит по ставке каждой строки табеля, и произведение с полем формы разошлось
 * бы с табелем и ведомостью. Пока грузится — ничего; не загрузилось — так и
 * сказать, а не «смен нет».
 */
export function monthFactHint(fact: MonthFact | null | 'error', monthPrep: string): string | undefined {
  if (fact === null) return undefined
  if (fact === 'error') return 'Смены месяца не загрузились — обновите страницу'
  if (!fact.shifts) return `В ${monthPrep} смен нет`
  return `В ${monthPrep} ${plural(fact.shifts, 'смена', 'смены', 'смен')} · ${rub(fact.pay)} по ставкам строк табеля`
}
