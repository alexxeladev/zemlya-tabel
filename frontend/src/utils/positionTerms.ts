/**
 * Условия труда рабочего места в карточке: подписи значений и групп (ADR-001).
 *
 * Условия меняются ГРУППАМИ и каждое со своей даты, поэтому карточка показывает
 * их строкой на группу: что действует сегодня и что запланировано дальше
 * («с 01.12.2026 будет 100 000 ₽»). Бэкенд отдаёт значения в том же виде, в
 * каком принимает: деньги и коэффициенты строкой, график идентификатором,
 * признак — булевым; здесь они превращаются в текст.
 *
 * Диффа условий здесь нет и быть не должно: до ADR-001 фронт сравнивал форму с
 * карточкой и по этому сравнению решал, спрашивать ли дату, — из-за чего правку
 * задним числом тем же значением было не внести вовсе.
 *
 * Модуль без JSX — проверяется `npm test`.
 */
import type { PayType, TermGroupKey, WeekendPayType } from '../types/api'

export const PAY_TYPE_LABELS: Record<PayType, string> = {
  salary: 'Окладная',
  per_shift: 'Посменная',
  hourly: 'Почасовая',
}

/** Тип оплаты → поле его базы и подписи. Поля взаимоисключающие. */
export const BASE_FIELD: Record<
  PayType, { key: 'rate' | 'shift_rate' | 'hour_rate'; label: string; unit: string; placeholder: string }
> = {
  salary: { key: 'rate', label: 'Оклад (₽/мес)', unit: '₽/мес', placeholder: '50000' },
  per_shift: { key: 'shift_rate', label: 'Ставка за смену (₽)', unit: '₽/смена', placeholder: '2500' },
  hourly: { key: 'hour_rate', label: 'Ставка за час (₽)', unit: '₽/час', placeholder: '450' },
}

export const NOT_SET = 'не задано'

export type TermValues = Record<string, unknown>

/** Число из строки бэка: «1.50» → «1.5», пусто → null. */
function num(value: unknown): number | null {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isNaN(n) ? null : n
}

/** Деньги с разделителями разрядов, без хвоста нулей: «90 000». */
export function money(value: unknown): string | null {
  const n = num(value)
  if (n == null) return null
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(n)
}

function coeffText(
  kind: unknown, coefficient: unknown, fixed: unknown,
): string {
  if (kind === 'fixed_rate') {
    const v = money(fixed)
    return v ? `${v} ₽/ч (фикс.)` : NOT_SET
  }
  const n = num(coefficient)
  return n == null ? NOT_SET : `×${n}`
}

function scheduleText(id: unknown, names: Record<string, string>): string {
  if (id == null || id === '') return 'не указан'
  return names[String(id)] ?? `#${id}`
}

/**
 * Группа условий одной строкой — то, что видно в карточке.
 *
 * Значения могут прийти неполными (изменение вахты правит только оф. зарплату),
 * поэтому группа всегда собирается ПОВЕРХ действующих значений.
 */
export function termGroupSummary(
  group: TermGroupKey, values: TermValues, scheduleNames: Record<string, string> = {},
): string {
  switch (group) {
    case 'pay': {
      const payType = (values.pay_type as PayType) ?? 'salary'
      const base = BASE_FIELD[payType]
      const amount = money(values[base.key])
      return `${PAY_TYPE_LABELS[payType]} · ${amount ? `${amount} ${base.unit}` : NOT_SET}`
    }
    case 'schedule':
      return scheduleText(values.schedule_id, scheduleNames)
    case 'weekend':
      return coeffText(
        values.weekend_pay_type, values.weekend_coefficient, values.weekend_fixed_rate,
      )
    case 'holiday':
      return coeffText(
        values.holiday_pay_type, values.holiday_coefficient, values.holiday_fixed_rate,
      )
    case 'overtime': {
      const n = num(values.overtime_coefficient)
      return n == null ? NOT_SET : `×${n}`
    }
    case 'official': {
      if (!values.is_official) return 'Неофициально'
      const salary = money(values.official_salary)
      return salary ? `Официально · ${salary} ₽ на руки` : `Официально · ${NOT_SET}`
    }
    default:
      return NOT_SET
  }
}

/** Подпись запланированного изменения: «с 01.12.2026 будет 100 000 ₽/мес». */
export function termPlannedText(
  group: TermGroupKey,
  current: TermValues,
  planned: { effective_label: string; values: TermValues },
  scheduleNames: Record<string, string> = {},
): string {
  const merged = { ...current, ...planned.values }
  return `${planned.effective_label} будет ${termGroupSummary(group, merged, scheduleNames)}`
}

/** Значение одного условия в истории: «90 000 ₽», «×1.5», «5/2», «да». */
export function termValueText(
  field: string, value: unknown, scheduleNames: Record<string, string> = {},
): string {
  if (field === 'schedule_id') return scheduleText(value, scheduleNames)
  if (field === 'pay_type') return PAY_TYPE_LABELS[value as PayType] ?? String(value)
  if (field === 'weekend_pay_type' || field === 'holiday_pay_type') {
    return (value as WeekendPayType) === 'fixed_rate' ? 'фикс. ставка за час' : 'коэффициент'
  }
  if (field === 'is_official') return value ? 'да' : 'нет'
  if (value == null || value === '') return NOT_SET
  if (field.endsWith('coefficient')) return `×${num(value)}`
  const amount = money(value)
  return amount == null ? String(value) : `${amount} ₽`
}

/** Дата по-русски: «01.12.2026». Пустая — null, а не «—»: подпись решает экран. */
export function dateRu(value?: string | null): string | null {
  if (!value) return null
  const [y, m, d] = value.split('-')
  return y && m && d ? `${d}.${m}.${y}` : value
}

/** Период работы на рабочем месте одной строкой; обе границы включительно. */
export function workplacePeriodText(
  hire?: string | null, dismissal?: string | null,
): string {
  const from = dateRu(hire)
  const to = dateRu(dismissal)
  if (from && to) return `с ${from} по ${to}`
  if (from) return `с ${from}`
  if (to) return `по ${to}`
  return 'без ограничения'
}
