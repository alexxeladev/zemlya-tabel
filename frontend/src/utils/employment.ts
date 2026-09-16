/**
 * Период работы рабочего места — ОДНО место на весь фронт (task_employment_period).
 *
 * Зеркало `backend/app/services/employment_period.py`. Правишь одно — правь
 * второе: расхождение означает, что табель разрешает клик там, где бэк ответит
 * 422, или наоборот красит серым день, который заполнить можно.
 *
 * Правило: табель заполняется от даты приёма до даты увольнения ВКЛЮЧИТЕЛЬНО.
 * Границы позиции — ПЕРЕСЕЧЕНИЕ её собственных дат с датами человека: даты
 * человека всегда внешняя граница, поэтому увольнение кадровиком закрывает все
 * рабочие места разом. Пустая дата означает отсутствие границы с этой стороны,
 * поэтому сотрудник без дат (таких большинство) ведёт себя ровно как раньше.
 *
 * Запрет на клиенте — только удобство: источник правды остаётся серверным,
 * прямой запрос отклоняется независимо от того, что нарисовано на экране.
 */

/** Даты есть и у человека, и у позиции — форма у них одна. */
export type EmploymentDates = {
  hire_date?: string | null
  dismissal_date?: string | null
}

/**
 * Границы рабочего места: [с какого дня, по какой ВКЛЮЧИТЕЛЬНО].
 * `null` с любой стороны — границы нет.
 *
 * Даты приходят строками ISO (`YYYY-MM-DD`), в таком виде и сравниваются:
 * лексикографический порядок ISO-дат совпадает с хронологическим, а разбор в
 * `Date` втащил бы часовые пояса — день границы уезжал бы на сутки.
 */
export function employmentBounds(
  employee: EmploymentDates | null | undefined,
  position: EmploymentDates | null | undefined,
): [string | null, string | null] {
  const starts = [employee?.hire_date, position?.hire_date].filter(Boolean) as string[]
  const ends = [employee?.dismissal_date, position?.dismissal_date].filter(Boolean) as string[]
  return [
    starts.length ? starts.reduce((a, b) => (a > b ? a : b)) : null,
    ends.length ? ends.reduce((a, b) => (a < b ? a : b)) : null,
  ]
}

/** Входит ли день (ISO `YYYY-MM-DD`) в период работы рабочего места. */
export function isWithinEmployment(
  employee: EmploymentDates | null | undefined,
  position: EmploymentDates | null | undefined,
  isoDate: string,
): boolean {
  const [start, end] = employmentBounds(employee, position)
  if (start && isoDate < start) return false
  if (end && isoDate > end) return false
  return true
}

/** Работает ли человек в этот день ХОТЬ ГДЕ-ТО — код отсутствия ставится на
 * человека целиком, поэтому и граница у него общая по всем рабочим местам. */
export function isWithinAnyEmployment(
  employee: EmploymentDates | null | undefined,
  positions: EmploymentDates[],
  isoDate: string,
): boolean {
  if (!positions.length) return isWithinEmployment(employee, null, isoDate)
  return positions.some((p) => isWithinEmployment(employee, p, isoDate))
}

const ru = (iso: string) => iso.split('-').reverse().join('.')

/**
 * Подсказка «почему нельзя» — или `null`, если день в периоде.
 * Тот же смысл, что у `employment_reason` на бэке: день вне периода должен
 * читаться как «нельзя», а не как «можно, но не заполнено».
 */
export function employmentHint(
  employee: EmploymentDates | null | undefined,
  position: EmploymentDates | null | undefined,
  isoDate: string,
): string | null {
  const [start, end] = employmentBounds(employee, position)
  if (start && isoDate < start) {
    return `Вне периода работы: принят ${ru(start)}`
  }
  if (end && isoDate > end) {
    return `Вне периода работы: уволен ${ru(end)}`
  }
  return null
}

/** Собрать ISO-дату дня месяца — как её ждут остальные функции этого модуля. */
export function isoDay(year: number, month: number, day: number): string {
  const mm = String(month).padStart(2, '0')
  const dd = String(day).padStart(2, '0')
  return `${year}-${mm}-${dd}`
}

// ── Очистка часов при смене дат ──────────────────────────────────────────────
//
// Сдвиг даты приёма/увольнения выкидывает часы за новую границу. Бэк не удаляет
// их молча: без `?confirm=true` он отвечает 409 с числами, а сохранение
// откатывает. Здесь эти числа превращаются в текст вопроса — очистка
// необратима, и пользователь должен видеть, что именно теряет.

/** Разбор ответа 409. Форму задаёт `ClearingReport.as_dict()` на бэке. */
export type ClearingDetail = {
  error?: string
  days?: number
  hours?: string
  amount?: string
  locked_days?: number
  locked_hours?: string
  locked_months?: string[]
}

const CLEARING_ERROR = 'employment_period_clearing_required'

const money = (v: string) =>
  new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(v))

const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

/**
 * Текст подтверждения или `null`, если ошибка не про очистку периода.
 *
 * Обязательно называет ЧИСЛА: сколько дней и на какую сумму. Отдельной строкой —
 * часы в закрытых периодах: их не тронут ни при каком подтверждении, и человек
 * должен узнать об этом до сохранения, а не обнаружить потом в ведомости.
 */
export function employmentClearingMessage(detail: unknown): string | null {
  if (typeof detail !== 'object' || detail === null) return null
  const d = detail as ClearingDetail
  if (d.error !== CLEARING_ERROR) return null

  const lines: string[] = []
  const days = d.days ?? 0
  if (days > 0) {
    const hours = d.hours ? Number(d.hours) : 0
    lines.push(
      `Будут удалены часы за ${days} ${plural(days, 'день', 'дня', 'дней')}` +
        ` (${hours} ч) на сумму ${money(d.amount ?? '0')} ₽.`,
    )
  }
  const locked = d.locked_days ?? 0
  if (locked > 0) {
    const months = d.locked_months?.length ? ` (${d.locked_months.join(', ')})` : ''
    lines.push(
      `Ещё ${locked} ${plural(locked, 'день', 'дня', 'дней')} остаётся` +
        ` в закрытых периодах${months} — их правка запрещена, они сохранятся как есть.`,
    )
  }
  if (!lines.length) return null
  lines.push('Действие необратимо. Продолжить?')
  return lines.join('\n\n')
}

/** Пользователь отказался подтвердить очистку — сохранение не состоялось. */
export const CLEARING_CANCELLED = Symbol('clearing-cancelled')

/**
 * Выполнить сохранение, которое может упереться в очистку часов.
 *
 * `run(false)` — обычная попытка. Бэк отвечает 409 с числами → спрашиваем.
 * «Да» — повтор `run(true)`. «Нет» — возвращаем `CLEARING_CANCELLED`, и это
 * НЕ ошибка: бэк правку уже откатил, ничего не сохранено и ничего не удалено.
 * Пробросить исходный 409 дальше значило бы показать отказ красным тостом с
 * сырым JSON — так уже было.
 *
 * Любая другая ошибка пробрасывается как есть. Ошибка распознаётся по полю
 * `detail` (его несёт `ApiError`), без импорта клиента API: модуль остаётся
 * чистым и тестируется без axios и окружения Vite.
 *
 * `ask` передаёт вызывающий экран (`window.confirm`): модуль собирается и в
 * Node-конфиге тестов, где DOM нет, поэтому к `window` он не обращается сам.
 */
export async function withClearingConfirm<T>(
  run: (confirm: boolean) => Promise<T>,
  ask: (message: string) => boolean,
): Promise<T | typeof CLEARING_CANCELLED> {
  try {
    return await run(false)
  } catch (e) {
    const detail =
      typeof e === 'object' && e !== null && 'detail' in e
        ? (e as { detail?: unknown }).detail
        : undefined
    const message = employmentClearingMessage(detail)
    if (!message) throw e
    if (!ask(message)) return CLEARING_CANCELLED
    return run(true)
  }
}
