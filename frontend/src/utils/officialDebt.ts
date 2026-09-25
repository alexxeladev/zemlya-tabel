/**
 * Тексты переплаты по официальной выплате вахты (task_official_payout_debt).
 *
 * Чисел фронт не считает — они приходят с бэка (`GET /employees/{id}/official-debt`),
 * здесь только подписи. То же разделение, что у займа (`utils/loan.ts`).
 */
import type { OfficialDebtStatus } from '../types/api'
import { formatMoney } from './money.ts'
import { MONTHS_RU_GEN } from './ruDate.ts'

/** Есть ли что показывать: нулевой долг без движения в месяце не рисуем. */
export function hasDebtToShow(row: OfficialDebtStatus): boolean {
  return (
    parseFloat(row.debt) > 0 ||
    parseFloat(row.repaid_in_month) > 0 ||
    parseFloat(row.debt_before_month) > 0
  )
}

/** «Переплата на конец сентября 2026: 15 000 ₽». */
export function debtLabel(row: OfficialDebtStatus): string {
  const month = MONTHS_RU_GEN[row.month - 1]
  return `Переплата на конец ${month} ${row.year}: ${formatMoney(row.debt, { showZero: true })}`
}

/** Что произошло за месяц: погашено, выросло или без движения. */
export function debtMovementLabel(row: OfficialDebtStatus): string {
  const repaid = parseFloat(row.repaid_in_month)
  const before = parseFloat(row.debt_before_month)
  const now = parseFloat(row.debt)
  if (repaid > 0 && now === 0) {
    return `Погашено полностью: ${formatMoney(row.repaid_in_month)}`
  }
  if (repaid > 0) return `Погашено за месяц: ${formatMoney(row.repaid_in_month)}`
  if (now > before) {
    // Сумму роста не считаем: фронт чисел не считает, а на float копейки
    // поехали бы. Обе величины и так видны в подписи выше.
    return 'Выросла за месяц: банк платил, а смен не было'
  }
  return 'За месяц без движения'
}

/**
 * Уволенное место: долг больше не гасится — гасить нечем, выплат нет.
 * Показываем дату, на которую он зафиксирован (взыскивают вне системы).
 */
export function finalDebtLabel(row: OfficialDebtStatus): string | null {
  if (!row.is_final || !row.closed_on) return null
  const [y, m, d] = row.closed_on.split('-')
  return `Место закрыто ${d}.${m}.${y} — остаток зафиксирован как задолженность`
}
