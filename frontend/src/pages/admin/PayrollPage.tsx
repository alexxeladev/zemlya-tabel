import { useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type {
  CompanyShare, Department, DistributionSource, PayrollStatement, StatementRow,
} from '../../types/api'
import { timesheetApi } from '../../api/timesheet'
import { apiClient } from '../../api/client'
import { formatHours, formatMoney } from '../../utils/money'
import { distribute } from '../../utils/distribution'

const MONTH_NAMES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const num = (v: string | null | undefined): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

// Обоснования премии/KPI/удержаний — подсказкой к сумме. Записей за месяц может
// быть несколько, поэтому каждая своей строкой (те же тексты идут в Excel).
const reasonTitle = (reasons: string[] | undefined): string | undefined =>
  reasons && reasons.length ? reasons.join('\n') : undefined

// Ключ строки — РАБОЧЕЕ МЕСТО (task_positions): у совместителя строк несколько
// на одного человека, employee_id их не различает.
type Edits = Record<string, Record<number, string>> // rowKey → company_id → percent

const rowKey = (row: Pick<StatementRow, 'employee_id' | 'position_id'>): string =>
  `${row.employee_id}:${row.position_id ?? 0}`

// Откуда взято распределение по юрлицам (каскад task_distribution_v2 ч.3):
// месячная правка > карточка сотрудника > дефолт отдела > авто по часам.
const SOURCE_LABEL: Record<DistributionSource, string> = {
  month: 'правка на месяц',
  employee: 'из карточки',
  department: 'дефолт отдела',
  hours: 'авто по часам',
}
const SOURCE_STYLE: Record<DistributionSource, string> = {
  month: 'text-indigo-500',
  employee: 'text-gray-500',
  department: 'text-teal-600',
  hours: 'italic text-gray-400',
}

function buildEdits(stmt: PayrollStatement): Edits {
  const e: Edits = {}
  for (const row of stmt.rows) {
    const key = rowKey(row)
    e[key] = {}
    // Авто-распределённые строки (ручной % не задан) НЕ префиллим — поля остаются
    // пустыми (плейсхолдер), чтобы видна была разница «авто по часам» vs «ручной».
    if (row.is_auto_distributed) continue
    for (const d of row.distribution) {
      e[key][d.company_id] = d.percent
    }
  }
  return e
}

/**
 * Сколько подряд идущих строк принадлежат одному человеку — для merge ФИО.
 * Бэк отдаёт позиции сотрудника подряд, фильтры порядок не меняют.
 */
function employeeSpans(rows: StatementRow[]): number[] {
  const spans = new Array(rows.length).fill(0)
  let start = 0
  for (let i = 1; i <= rows.length; i++) {
    if (i === rows.length || rows[i].employee_id !== rows[start].employee_id) {
      spans[start] = i - start
      start = i
    }
  }
  return spans
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
  const [savingKey, setSavingKey] = useState<string | null>(null)

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

  const setPercent = (key: string, companyId: number, value: string) => {
    setEdits((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? {}), [companyId]: value },
    }))
  }

  const rowPercentSum = (key: string): number => {
    const e = edits[key] ?? {}
    return Object.values(e).reduce((s, v) => s + num(v), 0)
  }

  // Авто-строка: бэк распределил по часам (ручной % не задан) и пользователь
  // ещё ничего не ввёл вручную. Ввод любого % перекрывает авто.
  const isAutoRow = (row: StatementRow): boolean =>
    row.is_auto_distributed && rowPercentSum(rowKey(row)) === 0

  // Суммы распределения строки. Считаются ТЕМ ЖЕ алгоритмом, что на бэке
  // (utils/distribution ≡ services/distribution.py): сумма долей ровно равна
  // «Итого начислено», остаток — основной компании. Иначе экран и Excel
  // разъезжаются (350010 против 350000).
  const rowAmounts = (row: StatementRow): Record<number, number> => {
    if (isAutoRow(row)) {
      const m: Record<number, number> = {}
      for (const d of row.distribution) m[d.company_id] = num(d.amount)
      return m
    }
    const weights: Record<number, number> = {}
    for (const [cid, v] of Object.entries(edits[rowKey(row)] ?? {})) {
      if (num(v) > 0) weights[Number(cid)] = num(v)
    }
    return distribute(num(row.accrued_total), weights, row.main_company_id)
  }

  // Проценты сохраняются РАБОЧЕМУ МЕСТУ строки: у совместителя вторая позиция
  // разносится по своим юрлицам и правку первой не трогает.
  const saveRow = async (row: StatementRow) => {
    const key = rowKey(row)
    const e = edits[key] ?? {}
    const shares: CompanyShare[] = Object.entries(e)
      .filter(([, v]) => num(v) > 0)
      .map(([cid, v]) => ({ company_id: Number(cid), percent: String(num(v)) }))
    try {
      setSavingKey(key)
      await timesheetApi.setDistributionOverride({
        employee_id: row.employee_id, position_id: row.position_id, year, month, shares,
      })
      toast.success('Распределение сохранено на месяц')
      reload()
    } catch {
      toast.error('Не удалось сохранить распределение')
    } finally {
      setSavingKey(null)
    }
  }

  const resetRow = async (row: StatementRow) => {
    try {
      setSavingKey(rowKey(row))
      await timesheetApi.clearDistributionOverride(
        row.employee_id, year, month, row.position_id,
      )
      toast.success('Правка на месяц убрана — вернулся следующий уровень каскада')
      reload()
    } catch {
      toast.error('Не удалось сбросить переопределение')
    } finally {
      setSavingKey(null)
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

  // merge ФИО по строкам-позициям + сквозная нумерация ЛЮДЕЙ, а не строк
  const rowSpans = useMemo(() => employeeSpans(visibleRows), [visibleRows])
  const employeeSeq = useMemo(() => {
    let n = 0
    return rowSpans.map((span) => (span > 0 ? ++n : n))
  }, [rowSpans])

  const footer = useMemo(() => {
    const acc = {
      overtime: 0, base: 0, vacation: 0, sick: 0, vacationDays: 0, sickDays: 0,
      premium: 0, kpi: 0, accrued: 0, deductions: 0, net: 0,
      dist: {} as Record<number, number>,
    }
    for (const c of companies) acc.dist[c.id] = 0
    for (const row of visibleRows) {
      acc.overtime += num(row.overtime_amount)
      acc.base += num(row.base_salary)
      acc.vacation += num(row.vacation_amount)
      acc.sick += num(row.sick_amount)
      acc.vacationDays += row.vacation_days
      acc.sickDays += row.sick_days
      acc.premium += num(row.premium_amount)
      acc.kpi += num(row.kpi_amount)
      acc.accrued += num(row.accrued_total)
      acc.deductions += num(row.deductions)
      acc.net += num(row.net_payout)
      const amounts = rowAmounts(row)
      for (const [cid, amt] of Object.entries(amounts)) {
        if (Number(cid) in acc.dist) acc.dist[Number(cid)] += amt
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
          {/* Менеджеру с несколькими отделами селектор нужен так же, как
              бухгалтеру: /api/departments отдаёт ему только его отделы
              (task_org_structure ч.2). С одним отделом выбирать нечего. */}
          {departments.length > 1 && (
            <select
              value={departmentId ?? ''}
              onChange={(e) => setDepartmentId(e.target.value === '' ? undefined : Number(e.target.value))}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
            >
              <option value="">{isManager ? 'Все мои отделы' : 'Все отделы'}</option>
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
                <th className="px-2 py-2 text-center font-medium">Оклад / ставка</th>
                <th className="px-2 py-2 text-center font-medium">Норма</th>
                <th className="px-2 py-2 text-center font-medium">Факт</th>
                <th className="px-2 py-2 text-center font-medium" title="Коэффициент переработки">Коэф.</th>
                <th className="px-2 py-2 text-center font-medium" title="Кол-во часов переработки">Пер. ч</th>
                <th className="px-2 py-2 text-center font-medium">Сумма пер.</th>
                <th className="px-2 py-2 text-center font-medium">Начисл. оклад</th>
                <th className="px-2 py-2 text-center font-medium" title="Дней отпуска / больничного">Отп./Больн. дн.</th>
                <th className="px-2 py-2 text-center font-medium" title="Отпускные: оклад / норма × (дни × 8)">Отпускные</th>
                <th className="px-2 py-2 text-center font-medium" title="Больничные: оклад / норма × (дни × 8)">Больничные</th>
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
                <tr><td colSpan={23} className="px-4 py-8 text-center text-gray-400">Нет сотрудников</td></tr>
              )}
              {visibleRows.map((row, i) => {
                const key = rowKey(row)
                const accrued = num(row.accrued_total)
                const pctSum = rowPercentSum(key)
                const pctWarn = pctSum > 0 && Math.abs(pctSum - 100) > 0.5
                const e = edits[key] ?? {}
                const auto = isAutoRow(row)
                // Строка = рабочее место; № / Таб.№ / ФИО объединяются
                // на все позиции одного человека (task_positions ч.B).
                const span = rowSpans[i]
                const isFirstOfEmployee = span > 0
                const seq = employeeSeq[i]
                const autoByCompany: Record<number, { percent: string; amount: string }> = {}
                if (auto) for (const d of row.distribution) autoByCompany[d.company_id] = d
                const amounts = rowAmounts(row)
                // Ввод % вручную (ещё не сохранён) — это уже правка на месяц.
                const sourceKey: DistributionSource =
                  auto ? 'hours' : (pctSum > 0 && row.distribution_source === 'hours'
                    ? 'month' : row.distribution_source)
                let liveDistTotal = 0
                return (
                  <tr key={key} className="border-b border-gray-100 hover:bg-gray-50/60">
                    {isFirstOfEmployee && (
                      <>
                        <td rowSpan={span} className="px-2 py-1.5 align-top text-gray-500">{seq}</td>
                        <td rowSpan={span} className="px-2 py-1.5 align-top text-gray-600">
                          {row.tab_number ?? '—'}
                        </td>
                        <td rowSpan={span} className="px-2 py-1.5 align-top font-medium text-gray-800">
                          {row.employee_name}
                          {span > 1 && (
                            <div
                              className="text-[10px] font-normal text-gray-400"
                              title="Совместительство: «к выплате» по позициям не суммируется — платят разные компании"
                            >
                              совместительство: {span} места
                            </div>
                          )}
                        </td>
                      </>
                    )}
                    <td className="px-2 py-1.5 text-gray-600">{row.main_company_name ?? '—'}</td>
                    <td className="px-2 py-1.5 text-gray-600">{row.department_name ?? '—'}</td>
                    <td className="px-2 py-1.5 text-gray-600">
                      {row.position ?? '—'}
                      {span !== 1 && row.is_primary_position && (
                        <span className="ml-1 text-[9px] text-gray-400">осн.</span>
                      )}
                      {!row.is_calculable && (
                        <div className="text-[10px] text-gray-400 italic" title={row.note ?? ''}>
                          ({row.note})
                        </div>
                      )}
                    </td>
                    <td
                      className="px-2 py-1.5 text-center text-gray-700"
                      title={
                        row.pay_type === 'per_shift'
                          ? `Посменно: ${row.base_shifts} смен × ставку` +
                            (row.worked_shifts > row.base_shifts
                              ? `; ещё ${row.worked_shifts - row.base_shifts} смен в выходные/праздники — по коэффициенту`
                              : '') +
                            `; условный оклад для отсутствий ${formatMoney(row.rate)}`
                          : undefined
                      }
                    >
                      {row.pay_type === 'per_shift' ? (
                        <>
                          {formatMoney(row.shift_rate)}
                          <div className="text-[10px] text-gray-400 leading-tight">за смену</div>
                        </>
                      ) : (
                        formatMoney(row.rate)
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-center text-gray-600">{formatHours(row.norm_hours)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatHours(row.fact_hours)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-600">{num(row.overtime_coefficient)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-600">{formatHours(row.overtime_hours)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.overtime_amount)}</td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.base_salary)}</td>
                    <td
                      className="px-2 py-1.5 text-center text-gray-600"
                      title={`ОТ: ${row.vacation_days} · Б: ${row.sick_days} · ДО: ${row.unpaid_days} · Н: ${row.absent_days}`}
                    >
                      {row.vacation_days || row.sick_days
                        ? `${row.vacation_days} / ${row.sick_days}`
                        : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-center text-gray-700">{formatMoney(row.vacation_amount)}</td>
                    <td
                      className="px-2 py-1.5 text-center text-gray-700"
                      title={
                        `Годовой лимит: остаток ${row.sick_limit_remaining} из ${row.sick_limit_days} дн.` +
                        (row.sick_unpaid_days
                          ? `\nСверх лимита (за свой счёт): ${row.sick_unpaid_days} дн.`
                          : '')
                      }
                    >
                      {formatMoney(row.sick_amount)}
                      {row.sick_days > 0 && (
                        <div className="text-[10px] text-gray-400">
                          лимит {row.sick_limit_remaining}/{row.sick_limit_days}
                          {row.sick_unpaid_days > 0 && (
                            <span className="text-amber-600"> · {row.sick_unpaid_days} б/о</span>
                          )}
                        </div>
                      )}
                    </td>
                    {/* Обоснования — подсказкой к сумме; те же тексты уходят в Excel */}
                    <td className="px-2 py-1.5 text-center text-gray-700" title={reasonTitle(row.premium_reasons)}>
                      {formatMoney(row.premium_amount)}
                    </td>
                    <td className="px-2 py-1.5 text-center text-gray-700" title={reasonTitle(row.kpi_reasons)}>
                      {formatMoney(row.kpi_amount)}
                    </td>
                    <td className="px-2 py-1.5 text-center font-bold text-blue-700">{formatMoney(row.accrued_total, { showZero: true })}</td>
                    <td
                      className="px-2 py-1.5 text-center text-rose-600"
                      title={reasonTitle([
                        ...(row.advance_reasons ?? []),
                        ...(row.loan_note ? [row.loan_note] : []),
                      ])}
                    >
                      {formatMoney(row.deductions)}
                    </td>
                    <td
                      className="px-2 py-1.5 text-center font-bold text-emerald-700"
                      title={
                        num(row.rounding_tail) > 0
                          ? `Округлено вниз до 100 ₽: точно ${formatMoney(row.net_payout_exact, { showZero: true })}, округление −${formatMoney(row.rounding_tail)}`
                          : undefined
                      }
                    >
                      {formatMoney(row.net_payout, { showZero: true })}
                    </td>
                    {companies.map((c) => {
                      const pct = e[c.id] ?? ''
                      const autoEntry = auto ? autoByCompany[c.id] : undefined
                      const autoPctLabel = autoEntry
                        ? String(Math.round(num(autoEntry.percent) * 100) / 100)
                        : '0'
                      const amount = amounts[c.id] ?? 0
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
                              onChange={(ev) => setPercent(key, c.id, ev.target.value)}
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
                            disabled={savingKey === key}
                            onClick={() => saveRow(row)}
                            className="rounded bg-blue-600 px-2 py-0.5 text-[10px] text-white hover:bg-blue-700 disabled:opacity-50"
                          >
                            Сохр.
                          </button>
                          {row.is_overridden && (
                            <button
                              disabled={savingKey === key}
                              onClick={() => resetRow(row)}
                              title="Убрать правку на месяц (вернётся карточка → отдел → авто по часам)"
                              className="rounded bg-gray-200 px-1.5 py-0.5 text-[10px] text-gray-700 hover:bg-gray-300 disabled:opacity-50"
                            >
                              ↺
                            </button>
                          )}
                        </div>
                      )}
                      {/* Откуда взято распределение — каскад ч.3 */}
                      <div className={`mt-0.5 text-[9px] ${SOURCE_STYLE[sourceKey]}`}>
                        {SOURCE_LABEL[sourceKey]}
                      </div>
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
                <td className="px-2 py-2 text-center text-gray-600">
                  {footer.vacationDays || footer.sickDays ? `${footer.vacationDays} / ${footer.sickDays}` : '—'}
                </td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.vacation))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.sick))}</td>
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
