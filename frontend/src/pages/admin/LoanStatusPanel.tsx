import { getLoanStatus } from '../../api/employees'
import { useApi } from '../../hooks/useApi'
import { formatMoney } from '../../utils/money'
import { MONTHS_RU_GEN } from '../../utils/ruDate'
import { paymentsLeftLabel, shortMonthLabel } from '../../utils/loan'

/**
 * Состояние займа в карточке сотрудника (п.5.1): остаток, сколько платежей
 * осталось и сколько было бы по исходному сроку, месяцы с недоудержанием.
 * Считает бэк (`GET /employees/{id}/loan-status`) тем же расчётом, что и
 * ведомость; здесь только показ. Нет прав или займа — ничего не рисуем.
 */
export function LoanStatusPanel({ employeeId }: { employeeId: number }) {
  const { data } = useApi(() => getLoanStatus(employeeId), [employeeId])
  if (!data) return null
  const longer = data.payments_left > data.payments_left_by_term
  return (
    <div
      className={`mt-3 rounded border px-3 py-2 text-xs ${
        longer ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-gray-200 bg-gray-50 text-gray-700'
      }`}
    >
      <p>
        Платёж по графику {formatMoney(data.share)} · остаток после{' '}
        {MONTHS_RU_GEN[data.month - 1]} {data.year}: {formatMoney(data.remaining_after, { showZero: true })}
      </p>
      <p className="mt-1 font-medium">{paymentsLeftLabel(data)}</p>
      {data.short_months.length > 0 && (
        <ul className="mt-1 list-disc pl-4">
          {data.short_months.map((m) => (
            <li key={`${m.year}-${m.month}`}>{shortMonthLabel(m)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
