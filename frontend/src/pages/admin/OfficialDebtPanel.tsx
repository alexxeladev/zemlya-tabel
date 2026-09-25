import { getOfficialDebt } from '../../api/employees'
import { useApi } from '../../hooks/useApi'
import {
  debtLabel,
  debtMovementLabel,
  finalDebtLabel,
  hasDebtToShow,
} from '../../utils/officialDebt'

/**
 * Переплата по официальной выплате вахты в карточке сотрудника
 * (task_official_payout_debt): сколько долга осталось, что было за месяц и
 * зафиксирован ли остаток при увольнении.
 *
 * Считает бэк (`GET /employees/{id}/official-debt`) тем же пересчётом истории,
 * что и ведомость; здесь только показ — как у панели займа. Постов не было или
 * долга нет — ничего не рисуем.
 */
export function OfficialDebtPanel({ employeeId }: { employeeId: number }) {
  const { data } = useApi(() => getOfficialDebt(employeeId), [employeeId])
  const rows = (data ?? []).filter(hasDebtToShow)
  if (rows.length === 0) return null
  return (
    <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
      <p className="font-medium">Официальная выплата: переплата</p>
      {rows.map((row) => {
        const frozen = finalDebtLabel(row)
        return (
          <div key={row.position_id} className="mt-1">
            {row.position_title && (
              <p className="text-amber-800">{row.position_title}</p>
            )}
            <p>{debtLabel(row)}</p>
            <p className="text-amber-800">{debtMovementLabel(row)}</p>
            {frozen && <p className="mt-0.5 font-medium">{frozen}</p>}
          </div>
        )
      })}
      <p className="mt-1.5 text-amber-800">
        Банк платит половину официальной зарплаты в каждую половину месяца
        независимо от смен. В нерабочую половину платить её нечем — переплата
        гасится из следующих кассовых выплат.
      </p>
    </div>
  )
}
