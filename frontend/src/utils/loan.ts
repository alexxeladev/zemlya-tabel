/**
 * Подписи займа (п.5.1: заём при нулевом начислении).
 *
 * Правило живёт на бэке (`loan_month_state` / `build_payroll_summary`): удержать
 * можно не больше «Итого начислено» позиции займа за вычетом аванса; нечего —
 * месяц пропускается, остаток не уменьшается, срок растягивается. Здесь только
 * тексты — чисел фронт не считает.
 */
import { MONTHS_RU } from './ruDate.ts'

export interface LoanShortMonthLike {
  year: number
  month: number
  planned: string
  actual: string
}

export interface LoanStatusLike {
  payments_left: number
  payments_left_by_term: number
  short_months: LoanShortMonthLike[]
}

const rub = (value: string) =>
  `${Math.round(Number(value)).toLocaleString('ru-RU').replace(/ /g, ' ')} ₽`

/** «5 платежей», «1 платёж», «2 платежа». */
export function paymentsWord(n: number): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return `${n} платёж`
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} платежа`
  return `${n} платежей`
}

/** Строка карточки: сколько осталось и сколько было бы по исходному сроку. */
export function paymentsLeftLabel(s: LoanStatusLike): string {
  const left = `Осталось ${paymentsWord(s.payments_left)}`
  if (s.payments_left > s.payments_left_by_term) {
    return `${left} — больше, чем по исходному сроку (${paymentsWord(s.payments_left_by_term)}): ` +
      'часть платежей не удержана'
  }
  return left
}

/** «Июль 2026: удержано 0 ₽ из 5 250 ₽». */
export function shortMonthLabel(m: LoanShortMonthLike): string {
  return `${MONTHS_RU[m.month - 1]} ${m.year}: удержано ${rub(m.actual)} из ${rub(m.planned)}`
}

/** Подсказка в окне займа табеля, когда в месяце удержано меньше плана. */
export function loanShortfallHint(actual: string, planned: string): string {
  return Number(actual) > 0
    ? `Начисления не хватило: удержано ${rub(actual)} из ${rub(planned)}. Остаток долга переносится, срок займа растягивается.`
    : `Начислений в месяце нет — заём не удерживается. Остаток долга не меняется, срок займа растягивается.`
}
