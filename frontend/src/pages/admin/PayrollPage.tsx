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
    // Авто-распределённые строки (ручной % не задан) НЕ префиллим — поля остаются
    // пустыми (плейсхолдер), чтобы видна была разница «авто по часам» vs «ручной».
    if (row.is_auto_distributed) continue
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
  const [query, setQuery] = useState('')
  const [companyFilter, setCompanyFilter] = useState<number | undefined>(undefined)
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

  // Авто-строка: бэк распределил по часам (ручной % не задан) и пользователь
  // ещё ничего не ввёл вручную. Ввод любого % перекрывает авто.
  const isAutoRow = (row: StatementRow): boolean =>
    row.is_auto_distributed && rowPercentSum(row.employee_id) === 0

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

  // Клиентские фильтры: ФИО / таб.№ (поиск) и компания (где у сотрудника есть доля).
  const visibleRows = useMemo(() => {
    const rows = data?.rows ?? []
    const q = query.trim().toLowerCase()
    return rows.filter((r) => {
      if (q) {
        const hay = `${r.employee_name} ${r.tab_number ?? ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      if (companyFilter !== undefined) {
        const hasShare = r.distribution.some((d) => d.company_id === companyFilter && num(d.percent) > 0)
        if (!hasShare && r.main_company_id !== companyFilter) return false
      }
      return true
    })
  }, [data, query, companyFilter])

  const footer = useMemo(() => {
    const acc = {
      overtime: 0, base: 0, premium: 0, kpi: 0, accrued: 0, deductions: 0, net: 0,
      dist: {} as Record<number, number>,
    }
    for (const c of companies) acc.dist[c.id] = 0
    for (const row of visibleRows) {
      acc.overtime += num(row.overtime_amount)
      acc.base += num(row.base_salary)
      acc.premium += num(row.premium_amount)
      acc.kpi += num(row.kpi_amount)
      acc.accrued += num(row.accrued_total)
      acc.deductions += num(row.deductions)
      acc.net += num(row.net_payout)
      const accrued = num(row.accrued_total)
      if (isAutoRow(row)) {
        // Авто-распределение по часам — берём суммы, посчитанные бэком.
        for (const d of row.distribution) {
          if (d.company_id in acc.dist) acc.dist[d.company_id] += num(d.amount)
        }
      } else {
        const e = edits[row.employee_id] ?? {}
        for (const c of companies) {
          const pct = num(e[c.id])
          if (pct > 0) acc.dist[c.id] += Math.round((accrued * pct) / 100)
        }
      }
    }
    return acc
  }, [visibleRows, edits, companies])

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
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск: ФИО или таб.№"
            className="w-48 rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
          <select
            value={companyFilter ?? ''}
            onChange={(e) => setCompanyFilter(e.target.value === '' ? undefined : Number(e.target.value))}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
          >
            <option value="">Все компании</option>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>{c.code} — {c.name}</option>
            ))}
          </select>
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
          {(query || companyFilter !== undefined) && (
            <button
              onClick={() => { setQuery(''); setCompanyFilter(undefined) }}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-500 hover:bg-gray-100"
            >
              Сброс
            </button>
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
              {visibleRows.length === 0 && (
                <tr><td colSpan={20} className="px-4 py-8 text-center text-gray-400">Нет сотрудников</td></tr>
              )}
              {visibleRows.map((row, i) => {
                const accrued = num(row.accrued_total)
                const pctSum = rowPercentSum(row.employee_id)
                const pctWarn = pctSum > 0 && Math.abs(pctSum - 100) > 0.5
                const e = edits[row.employee_id] ?? {}
                const auto = isAutoRow(row)
                const autoByCompany: Record<number, { percent: string; amount: string }> = {}
                if (auto) for (const d of row.distribution) autoByCompany[d.company_id] = d
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
                      const autoEntry = auto ? autoByCompany[c.id] : undefined
                      const autoPctLabel = autoEntry
                        ? String(Math.round(num(autoEntry.percent) * 100) / 100)
                        : '0'
                      const amount = num(pct) > 0
                        ? Math.round((accrued * num(pct)) / 100)
                        : (autoEntry ? num(autoEntry.amount) : 0)
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
                              className={`w-12 rounded border px-1 py-0.5 text-right text-[11px] ${pctWarn ? 'border-amber-400 bg-amber-50' : 'border-gray-300'} ${autoEntry ? 'border-dashed text-gray-400 placeholder:text-gray-400' : ''} disabled:bg-gray-100`}
                              placeholder={autoPctLabel}
                              title={autoEntry ? 'Авто по часам — введите % чтобы задать вручную' : undefined}
                            />
                            <span className="text-gray-400">%</span>
                          </div>
                          <div className={`mt-0.5 text-[10px] ${autoEntry ? 'italic text-gray-400' : 'text-gray-500'}`}>
                            {amount > 0 ? formatMoney(String(amount)) : '—'}
                          </div>
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
                      {auto && (
                        <div className="mt-0.5 text-[9px] italic text-gray-400">авто по часам</div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
                <td className="px-2 py-2 text-gray-700" colSpan={11}>Итого{visibleRows.length !== (data.rows.length) ? ` (отфильтровано: ${visibleRows.length})` : ''}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.overtime))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.base))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.premium))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.kpi))}</td>
                <td className="px-2 py-2 text-center font-bold text-blue-700">{formatMoney(String(footer.accrued), { showZero: true })}</td>
                <td className="px-2 py-2 text-center text-rose-600">{formatMoney(String(footer.deductions))}</td>
                <td className="px-2 py-2 text-center font-bold text-emerald-700">{formatMoney(String(footer.net), { showZero: true })}</td>
                {companies.map((c) => (
                  <td key={c.id} className="px-2 py-2 text-center text-gray-700 bg-indigo-50/40">{formatMoney(String(footer.dist[c.id] ?? 0))}</td>
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
