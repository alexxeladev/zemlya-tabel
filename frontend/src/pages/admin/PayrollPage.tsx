import { useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type { CompanyShare, Department, PayrollStatement, StatementRow } from '../../types/api'
import { timesheetApi } from '../../api/timesheet'
import { apiClient } from '../../api/client'
import { formatHours, formatMoney } from '../../utils/money'

const MONTH_NAMES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const num = (v: string | null | undefined): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

type Edits = Record<number, Record<number, string>> // employee_id → company_id → percent

function buildEdits(stmt: PayrollStatement): Edits {
  const e: Edits = {}
  for (const row of stmt.rows) {
    e[row.employee_id] = {}
    for (const d of row.distribution) {
      e[row.employee_id][d.company_id] = d.percent
    }
  }
  return e
}

export function PayrollPage() {
  const user = useAuthStore((s) => s.user)
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [departmentId, setDepartmentId] = useState<number | undefined>(undefined)
  const [departments, setDepartments] = useState<Department[]>([])
  const [data, setData] = useState<PayrollStatement | null>(null)
  const [edits, setEdits] = useState<Edits>({})
  const [loading, setLoading] = useState(true)
  const [savingId, setSavingId] = useState<number | null>(null)

  useEffect(() => {
    apiClient.get<Department[]>('/api/departments').then((r) => setDepartments(r.data)).catch(() => {})
  }, [])

  const reload = () => {
    setLoading(true)
    timesheetApi.getStatement(year, month, departmentId)
      .then((d) => { setData(d); setEdits(buildEdits(d)) })
      .catch(() => toast.error('Не удалось загрузить ведомость'))
      .finally(() => setLoading(false))
  }

  useEffect(reload, [year, month, departmentId])

  const prevMonth = () => {
    if (month === 1) { setYear((y) => y - 1); setMonth(12) }
    else setMonth((m) => m - 1)
  }
  const nextMonth = () => {
    if (month === 12) { setYear((y) => y + 1); setMonth(1) }
    else setMonth((m) => m + 1)
  }

  if (user?.role !== 'admin' && user?.role !== 'accountant' && user?.role !== 'manager') {
    return <div className="p-8 text-center text-red-500">Нет доступа</div>
  }
  const canEdit = user?.role === 'admin' || user?.role === 'accountant'
  const isManager = user?.role === 'manager'

  const setPercent = (empId: number, companyId: number, value: string) => {
    setEdits((prev) => ({
      ...prev,
      [empId]: { ...(prev[empId] ?? {}), [companyId]: value },
    }))
  }

  const rowPercentSum = (empId: number): number => {
    const e = edits[empId] ?? {}
    return Object.values(e).reduce((s, v) => s + num(v), 0)
  }

  const saveRow = async (row: StatementRow) => {
    const e = edits[row.employee_id] ?? {}
    const shares: CompanyShare[] = Object.entries(e)
      .filter(([, v]) => num(v) > 0)
      .map(([cid, v]) => ({ company_id: Number(cid), percent: String(num(v)) }))
    try {
      setSavingId(row.employee_id)
      await timesheetApi.setDistributionOverride({
        employee_id: row.employee_id, year, month, shares,
      })
      toast.success('Распределение сохранено на месяц')
      reload()
    } catch {
      toast.error('Не удалось сохранить распределение')
    } finally {
      setSavingId(null)
    }
  }

  const resetRow = async (row: StatementRow) => {
    try {
      setSavingId(row.employee_id)
      await timesheetApi.clearDistributionOverride(row.employee_id, year, month)
      toast.success('Возвращены проценты из карточки')
      reload()
    } catch {
      toast.error('Не удалось сбросить переопределение')
    } finally {
      setSavingId(null)
    }
  }

  const download = async () => {
    try {
      const blob = await timesheetApi.exportStatementExcel(year, month, departmentId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `vedomost_${year}_${String(month).padStart(2, '0')}.xlsx`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error('Не удалось выгрузить ведомость')
    }
  }

  const companies = data?.companies ?? []
  const distTotals = useMemo(() => {
    // Живой пересчёт итогов распределения по компаниям из текущих правок.
    const totals: Record<number, number> = {}
    for (const c of companies) totals[c.id] = 0
    if (data) {
      for (const row of data.rows) {
        const accrued = num(row.accrued_total)
        const e = edits[row.employee_id] ?? {}
        for (const c of companies) {
          const pct = num(e[c.id])
          if (pct > 0) totals[c.id] += Math.round((accrued * pct) / 100)
        }
      }
    }
    return totals
  }, [data, edits, companies])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-3 shadow-sm">
        <h1 className="text-lg font-bold text-gray-900">Расчёт ЗП — ведомость</h1>
        <div className="flex items-center gap-2">
          <button onClick={prevMonth} className="rounded-md p-1 text-gray-500 hover:bg-gray-100">←</button>
          <span className="min-w-[120px] text-center text-sm font-medium text-gray-700">
            {MONTH_NAMES[month - 1]} {year}
          </span>
          <button onClick={nextMonth} className="rounded-md p-1 text-gray-500 hover:bg-gray-100">→</button>
        </div>
        <div className="flex items-center gap-2">
          {!isManager && departments.length > 0 && (
            <select
              value={departmentId ?? ''}
              onChange={(e) => setDepartmentId(e.target.value === '' ? undefined : Number(e.target.value))}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
            >
              <option value="">Все отделы</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          )}
          <button
            onClick={download}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
          >
            Скачать ведомость (Excel)
          </button>
        </div>
      </div>

      {loading && <div className="flex h-32 items-center justify-center text-gray-400">Загрузка...</div>}

      {!loading && data && (
        <div className="overflow-auto rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="min-w-full border-collapse text-[11px]">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200 text-gray-500">
                <th className="px-2 py-2 text-left font-medium">№</th>
                <th className="px-2 py-2 text-left font-medium">Таб.№</th>
                <th className="px-2 py-2 text-left font-medium min-w-[160px]">ФИО</th>
                <th className="px-2 py-2 text-left font-medium">Компания</th>
                <th className="px-2 py-2 text-left font-medium">Отдел</th>
                <th className="px-2 py-2 text-left font-medium">Должность</th>
                <th className="px-2 py-2 text-center font-medium">Оклад</th>
                <th className="px-2 py-2 text-center font-medium">Норма</th>
                <th className="px-2 py-2 text-center font-medium">Факт</th>
                <th className="px-2 py-2 text-center font-medium" title="Коэффициент переработки">Коэф.</th>
                <th className="px-2 py-2 text-center font-medium" title="Кол-во часов переработки">Пер. ч</th>
                <th className="px-2 py-2 text-center font-medium">Сумма пер.</th>
                <th className="px-2 py-2 text-center font-medium">Начисл. оклад</th>
                <th className="px-2 py-2 text-center font-medium">Премия</th>
                <th className="px-2 py-2 text-center font-medium">KPI</th>
                <th className="px-2 py-2 text-center font-semibold text-blue-700 min-w-[90px]">Итого начисл.</th>
                <th className="px-2 py-2 text-center font-medium">Удержано</th>
                <th className="px-2 py-2 text-center font-semibold text-emerald-700 min-w-[90px]">К выплате</th>
                {companies.map((c) => (
                  <th key={c.id} className="px-2 py-2 text-center font-medium bg-indigo-50 min-w-[110px]" title={c.name}>
                    {c.code} %/₽
                  </th>
                ))}
                <th className="px-2 py-2 text-center font-medium">Σ распред.</th>
                <th className="px-2 py-2 text-center font-medium">Примечание</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.length === 0 && (
                <tr><td colSpan={20} className="px-4 py-8 text-center text-gray-400">Нет сотрудников</td></tr>
              )}
              {data.rows.map((row, i) => {
                const accrued = num(row.accrued_total)
                const pctSum = rowPercentSum(row.employee_id)
                const pctWarn = pctSum > 0 && Math.abs(pctSum - 100) > 0.5
                const e = edits[row.employee_id] ?? {}
                let liveDistTotal = 0
                return (
                  <tr key={row.employee_id} className="border-b border-gray-100 hover:bg-gray-50/60">
                    <td className="px-2 py-1.5 text-gray-500">{i + 1}</td>
                    <td className="px-2 py-1.5 text-gray-600">{row.tab_number ?? '—'}</td>
                    <td className="px-2 py-1.5 font-medium text-gray-800">
                      {row.employee_name}
                      {!row.is_calculable && (
                        <span className="ml-1 text-[10px] text-gray-400 italic" title={row.note ?? ''}>
                          ({row.note})
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-gray-600">{row.main_company_name ?? '—'}</td>
                    <td className="px-2 py-1.5 text-gray-600">{row.department_name ?? '—'}</td>
                    <td className="px-2 py-1.5 text-gray-600">{row.position ?? '—'}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.rate)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-600">{formatHours(row.norm_hours)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatHours(row.fact_hours)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-600">{num(row.overtime_coefficient)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-600">{formatHours(row.overtime_hours)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.overtime_amount)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.base_salary)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.premium_amount)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.kpi_amount)}</td>
                    <td className="px-2 py-1.5 text-center font-bold text-blue-700">{formatMoney(row.accrued_total, { showZero: true })}</td>
                    <td className="px-2 py-1.5 text-center text-rose-600">{formatMoney(row.deductions)}</td>
                    <td className="px-2 py-1.5 text-center font-bold text-emerald-700">{formatMoney(row.net_payout, { showZero: true })}</td>
                    {companies.map((c) => {
                      const pct = e[c.id] ?? ''
                      const amount = num(pct) > 0 ? Math.round((accrued * num(pct)) / 100) : 0
                      liveDistTotal += amount
                      return (
                        <td key={c.id} className="px-1.5 py-1 text-center bg-indigo-50/40">
                          <div className="flex items-center justify-center gap-1">
                            <input
                              type="number"
                              min={0}
                              max={100}
                              step="0.1"
                              disabled={!canEdit}
                              value={pct}
                              onChange={(ev) => setPercent(row.employee_id, c.id, ev.target.value)}
                              className={`w-12 rounded border px-1 py-0.5 text-right text-[11px] ${pctWarn ? 'border-amber-400 bg-amber-50' : 'border-gray-300'} disabled:bg-gray-100`}
                              placeholder="0"
                            />
                            <span className="text-gray-400">%</span>
                          </div>
                          <div className="mt-0.5 text-[10px] text-gray-500">{amount > 0 ? formatMoney(String(amount)) : '—'}</div>
                        </td>
                      )
                    })}
                    <td className={`px-2 py-1.5 text-center font-medium ${liveDistTotal === Math.round(accrued) ? 'text-gray-600' : 'text-amber-600'}`}>
                      {formatMoney(String(liveDistTotal))}
                      {pctWarn && <div className="text-[10px] text-amber-600">Σ%={pctSum}</div>}
                    </td>
                    <td className="px-2 py-1.5 text-center whitespace-nowrap">
                      {canEdit && (
                        <div className="flex items-center justify-center gap-1">
                          <button
                            disabled={savingId === row.employee_id}
                            onClick={() => saveRow(row)}
                            className="rounded bg-blue-600 px-2 py-0.5 text-[10px] text-white hover:bg-blue-700 disabled:opacity-50"
                          >
                            Сохр.
                          </button>
                          {row.is_overridden && (
                            <button
                              disabled={savingId === row.employee_id}
                              onClick={() => resetRow(row)}
                              title="Вернуть проценты из карточки"
                              className="rounded bg-gray-200 px-1.5 py-0.5 text-[10px] text-gray-700 hover:bg-gray-300 disabled:opacity-50"
                            >
                              ↺
                            </button>
                          )}
                        </div>
                      )}
                      {row.is_overridden && (
                        <div className="mt-0.5 text-[9px] text-indigo-500">правка на месяц</div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
                <td className="px-2 py-2 text-gray-700" colSpan={11}>Итого</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_overtime_amount)}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_base_salary)}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_premium)}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_kpi)}</td>
                <td className="px-2 py-2 text-center font-bold text-blue-700">{formatMoney(data.total_accrued, { showZero: true })}</td>
                <td className="px-2 py-2 text-center text-rose-600">{formatMoney(data.total_deductions)}</td>
                <td className="px-2 py-2 text-center font-bold text-emerald-700">{formatMoney(data.total_net_payout, { showZero: true })}</td>
                {companies.map((c) => (
                  <td key={c.id} className="px-2 py-2 text-center text-gray-700 bg-indigo-50/40">{formatMoney(String(distTotals[c.id] ?? 0))}</td>
                ))}
                <td className="px-2 py-2" />
                <td className="px-2 py-2" />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  )
}
