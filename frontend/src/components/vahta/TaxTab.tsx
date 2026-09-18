import { useEffect, useMemo, useState } from 'react'

import { getVahtaMonth, getVahtaSettings, updateVahtaSettings } from '../../api/vahta'
import { usePeriodStore } from '../../store/period'
import { toast } from '../../store/toasts'
import type { VahtaMonth } from '../../types/api'
import { formatMoney } from '../../utils/money'
import { MONTHS_RU_GEN, MONTHS_RU_PREP } from '../../utils/ruDate'
import { halfTaxKopecks, parseTaxPercent, taxForHalves } from '../../utils/vahtaTax'
import { DsButton } from '../ds/Button'
import { ConfirmDialog } from '../ds/ConfirmDialog'
import { TextField } from '../ds/fields'

/** Сумма рублей числом — в том же формате, что везде: «10 092 ₽». */
const rub = (n: number) => formatMoney(String(n), { showZero: true })

/**
 * Налог на официальную часть выплаты (task_vahta_taxes) — последняя вкладка
 * настроек (task_vahta_settings_staff, решение п.2).
 *
 * Меняют его раз в год, поэтому он больше не висит над всеми вкладками. Вместо
 * абзаца про «базу разнесения» — живой пример на строке месяца: при вводе ставки
 * видно, сколько станет налога у человека и у всей вахты. Считает пример зеркало
 * бэка (`utils/vahtaTax`); при сохранённой ставке он обязан совпасть с серверной
 * суммой — поэтому экран показывает разницу именно к ней.
 */
export function TaxTab({ editable }: { editable: boolean }) {
  const { year, month } = usePeriodStore()
  const [saved, setSaved] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [monthData, setMonthData] = useState<VahtaMonth | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getVahtaSettings()
      .then((data) => {
        const value = parseFloat(data.employer_tax_percent)
        setSaved(value)
        setDraft(String(value))
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Не удалось загрузить ставку налога'))
  }, [])

  // Пример — на месяце табеля: те же строки, что человек видит в вахте.
  useEffect(() => {
    getVahtaMonth(year, month).then(setMonthData).catch(() => setMonthData(null))
  }, [year, month])

  const rate = parseTaxPercent(draft)
  const dirty = rate !== null && saved !== null && rate !== saved

  const example = useMemo(() => {
    const rows = monthData?.zones.flatMap((z) => z.cards.flatMap((c) => c.rows)) ?? []
    // Строка с официальной выплатой — на ней налог и виден. Нет такой — примера нет.
    const row = rows.find((r) => parseFloat(r.official_payout ?? '0') > 0) ?? null
    const halves = rows.flatMap((r) => r.halves.map((h) => h.official_payout ?? '0'))
    return { row, halves }
  }, [monthData])

  const shown = rate ?? saved ?? 0
  const rowTax = example.row
    ? example.row.halves.reduce((sum, h) => sum + halfTaxKopecks(h.official_payout ?? '0', shown), 0) / 100
    : 0
  const monthTax = taxForHalves(example.halves, shown)
  const serverMonthTax = monthData ? parseFloat(monthData.total_tax ?? '0') : null

  const save = async () => {
    if (rate === null) return
    setSaving(true)
    try {
      const data = await updateVahtaSettings({ employer_tax_percent: String(rate) })
      const value = parseFloat(data.employer_tax_percent)
      setSaved(value)
      setDraft(String(value))
      setConfirming(false)
      toast.success(`Ставка налога сохранена: ${value} %`)
      getVahtaMonth(year, month).then(setMonthData).catch(() => undefined)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
    } finally {
      setSaving(false)
    }
  }

  if (saved === null) return <p className="text-[13px] text-ds-muted">Загрузка…</p>

  return (
    <div className="max-w-[760px] rounded-ds-lg border border-ds-line bg-ds-surface p-5 shadow-ds">
      <h2 className="m-0 text-[15px] font-semibold">Налог на официальную выплату</h2>
      <p className="mb-4 mt-1 max-w-[70ch] text-[13px] text-ds-muted">
        Затрата компании сверх начисленного: входит в разнесение по юрлицам, но не в
        «начислено» и не в выплату.
      </p>

      <div className="flex items-center gap-3">
        <label htmlFor="tax-rate" className="w-[140px] text-[12.5px] text-ds-muted">
          Ставка
        </label>
        {editable ? (
          <span className="flex items-center gap-1.5">
            <TextField
              id="tax-rate"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && dirty) setConfirming(true)
              }}
              inputMode="decimal"
              aria-invalid={rate === null || undefined}
              aria-describedby="tax-rate-error"
              className="!w-20 text-right font-ds-mono"
            />
            <span className="text-ds-ink-2">%</span>
          </span>
        ) : (
          <b className="tabular-nums">{saved} %</b>
        )}
      </div>
      {rate === null && (
        <p id="tax-rate-error" className="ml-[152px] mt-1 text-[12px] text-ds-danger">
          Ставка — число от 0 до 100, например 40 или 40,5
        </p>
      )}

      <div
        className="mt-4 rounded-ds-md border border-ds-line bg-ds-surface-2 px-4 py-3 text-[13px] text-ds-ink-2"
        aria-live="polite"
      >
        {example.row ? (
          <>
            <p className="m-0 mb-1.5 text-[12px] text-ds-muted">
              Как считается — на строке {MONTHS_RU_GEN[month - 1]} {year}
            </p>
            <table className="text-[13px]">
              <tbody>
                <tr>
                  <td className="py-0.5 pr-6">Официальная выплата</td>
                  <td className="text-right tabular-nums">{formatMoney(example.row.official_payout)}</td>
                </tr>
                <tr>
                  <td className="py-0.5 pr-6">× ставка</td>
                  <td className="text-right tabular-nums">{shown} %</td>
                </tr>
                <tr className="font-semibold text-ds-ink">
                  <td className="border-t border-ds-line-strong py-0.5 pr-6">Налог</td>
                  <td className="border-t border-ds-line-strong text-right tabular-nums">
                    {rub(rowTax)}
                  </td>
                </tr>
                <tr>
                  <td className="py-0.5 pr-6">Начислено</td>
                  <td className="text-right tabular-nums">{formatMoney(example.row.accrued)}</td>
                </tr>
                <tr className="font-semibold text-ds-ink">
                  <td className="border-t border-ds-line-strong py-0.5 pr-6">Разносится по юрлицам</td>
                  <td className="border-t border-ds-line-strong text-right tabular-nums">
                    {rub(parseFloat(example.row.accrued ?? '0') + rowTax)}
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="m-0 mt-2.5 text-[12.5px]">
              Вся вахта в {MONTHS_RU_PREP[month - 1]}: налог{' '}
              <b className="tabular-nums">{rub(monthTax)}</b>
              {dirty && serverMonthTax !== null && (
                <span className="text-ds-muted">
                  {' '}
                  (сейчас {rub(serverMonthTax)},{' '}
                  {monthTax >= serverMonthTax ? '+' : '−'}
                  {rub(Math.abs(monthTax - serverMonthTax))})
                </span>
              )}
            </p>
          </>
        ) : (
          <p className="m-0 text-ds-muted">
            Налог = официальная выплата × ставка. В {MONTHS_RU_PREP[month - 1]} {year} официальных
            выплат нет — пример появится, когда в табеле вахты отметят официальную часть.
          </p>
        )}
      </div>

      {editable && (
        <>
          <p className="mt-3 rounded-ds-md border border-ds-warn-line bg-ds-warn-soft px-3 py-2.5 text-[12.5px] text-ds-warn">
            Ставка одна на все месяцы: сохранение пересчитает разнесение и прошлых месяцев,
            включая закрытые.
          </p>
          <div className="mt-3.5 flex gap-2">
            <DsButton variant="primary" disabled={!dirty} onClick={() => setConfirming(true)}>
              Сохранить ставку
            </DsButton>
            {dirty && (
              <DsButton variant="ghost" onClick={() => setDraft(String(saved))}>
                Вернуть {saved} %
              </DsButton>
            )}
          </div>
        </>
      )}

      {confirming && rate !== null && (
        <ConfirmDialog
          title="Сменить ставку налога?"
          confirmLabel={`Сохранить ${rate} %`}
          cancelLabel={`Оставить ${saved} %`}
          busy={saving}
          onConfirm={() => void save()}
          onCancel={() => setConfirming(false)}
        >
          Ставка изменится с {saved} % на {rate} %. Разнесение по юрлицам пересчитается во
          всех месяцах, включая прошлые и закрытые.
        </ConfirmDialog>
      )}
    </div>
  )
}
