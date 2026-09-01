import { useEffect, useMemo, useState } from 'react'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type {
  CompanyShare, Department, DistributionSource, PayrollStatement, StatementRow,
} from '../../types/api'
import { timesheetApi } from '../../api/timesheet'
import { apiClient } from '../../api/client'
import { formatHours, formatMoney, payoutRoundingHint } from '../../utils/money'
import { distributeToThousands } from '../../utils/distribution'
import { companyLabel } from '../../utils/companies'
import { usePeriodStore } from '../../store/period'
import { usePersistentState } from '../../hooks/usePersistentState'
import { UI_KEYS } from '../../utils/persist'

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
// quantity — вне каскада: отдел с флагом «распределение по количественному
// показателю» (заявки у HR, АРМ у ИТ) делится по нему, каскад не применяется.
const SOURCE_LABEL: Record<DistributionSource, string> = {
  month: 'правка на месяц',
  employee: 'из карточки',
  department: 'дефолт отдела',
  hours: 'авто по часам',
  quantity: 'по показателю отдела',
}
const SOURCE_STYLE: Record<DistributionSource, string> = {
  month: 'text-indigo-500',
  employee: 'text-gray-500',
  department: 'text-teal-600',
  hours: 'italic text-gray-400',
  quantity: 'font-medium text-emerald-600',
}

/**
 * Строка отдела, который делится по количественному показателю (заявки у HR,
 * АРМ у ИТ). Проценты приходят из показателя месяца, вводятся в табеле отдела и
 * каскад заменяют — поэтому здесь такая строка только показывается.
 */
function isQuantityRow(row: StatementRow): boolean {
  return row.distribution_source === 'quantity'
}

/**
 * Строка ОТДЕЛА, делящегося по количественному показателю, — независимо от
 * того, чем она в итоге распределена. У части рабочих мест распределение
 * задано в карточке и показатель их не касается (task_card_priority), но
 * ручная правка процентов в ведомости заблокирована для ВСЕГО такого отдела:
 * исключения задаются только в карточке сотрудника.
 */
function isQuantityDeptRow(row: StatementRow): boolean {
  return !!row.quantity_metric_name
}

/** Процент компании из показателя — плейсхолдер, править нельзя. */
function quantityPct(row: StatementRow, companyId: number): string {
  const found = row.distribution.find((d) => d.company_id === companyId)
  return found ? String(Math.round(num(found.percent) * 100) / 100) : '0'
}

/**
 * Целевые премии/KPI строки (task_funding_source): {company_id: сумма}.
 * Они относятся на своё юрлицо целиком и УМЕНЬШАЮТ базу каскада — прибавить их
 * сверх распределённого «Итого начислено» нельзя, иначе экран покажет больше,
 * чем начислено (и разойдётся с бэком, где база уменьшается).
 */
function targetedAmounts(row: StatementRow): Record<number, number> {
  const out: Record<number, number> = {}
  for (const [cid, amount] of Object.entries(row.targeted_amounts ?? {})) {
    out[Number(cid)] = num(amount)
  }
  return out
}

/**
 * БАЗА распределения — «Итого начислено», и только оно
 * (task_distribution_base_fix). Распределение отражает ЗАТРАТЫ компании, а они
 * возникают при начислении: удержания (займ, аванс) их не уменьшают, округление
 * «К выплате» на них не влияет. Зеркало `distribution_base` из
 * services/payroll_statement.py.
 */
function distributionBase(row: StatementRow): number {
  return num(row.accrued_total)
}

/** Сколько из базы вообще можно разнести круглыми тысячами (округление ВНИЗ). */
function distributable(base: number): number {
  return Math.floor(base / 1000) * 1000
}

/** База каскада = база распределения − целевые, но не меньше нуля. */
function cascadeBase(row: StatementRow): number {
  return Math.max(0, distributionBase(row) - num(row.targeted_total))
}

function buildEdits(stmt: PayrollStatement): Edits {
  const e: Edits = {}
  for (const row of stmt.rows) {
    const key = rowKey(row)
    e[key] = {}
    // Авто-распределённые строки (ручной % не задан) НЕ префиллим — поля остаются
    // пустыми (плейсхолдер), чтобы видна была разница «авто по часам» vs «ручной».
    if (row.is_auto_distributed) continue
    // Строки отдела «по количественному показателю» тоже: их проценты приходят
    // из показателя месяца и правятся в табеле отдела, а не здесь (правка тут
    // молча ничего не дала бы — показатель перекрывает каскад).
    if (isQuantityRow(row)) continue
    for (const d of row.distribution) {
      // Нулевой % — компания попала в разбивку только целевой премией
      // (task_funding_source), каскадом ей ничего не задано: инпут остаётся
      // пустым, иначе там висел бы бессмысленный ноль.
      if (num(d.percent) > 0) e[key][d.company_id] = d.percent
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
  // Период — общий с табелем и сохранённый (task_ux_improvements ч.3):
  // выбрал май в табеле — ведомость открылась за май.
  const year = usePeriodStore((s) => s.year)
  const month = usePeriodStore((s) => s.month)
  const setYear = usePeriodStore((s) => s.setYear)
  const setMonth = usePeriodStore((s) => s.setMonth)
  const [departments, setDepartments] = useState<Department[]>([])
  // Отдел/поиск/компания — фильтры этого экрана, живут своим ключом.
  const [filters, setFilters] = usePersistentState(
    UI_KEYS.payrollFilters,
    { departmentId: undefined as number | undefined, query: '', companyId: undefined as number | undefined },
    (v) => typeof v === 'object' && v !== null && 'query' in v,
  )
  const { departmentId, query } = filters
  const companyFilter = filters.companyId
  const setDepartmentId = (value: number | undefined) =>
    setFilters((f) => ({ ...f, departmentId: value }))
  const setQuery = (value: string) => setFilters((f) => ({ ...f, query: value }))
  const setCompanyFilter = (value: number | undefined) =>
    setFilters((f) => ({ ...f, companyId: value }))
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
    if (month === 1) { setYear(year - 1); setMonth(12) }
    else setMonth(month - 1)
  }
  const nextMonth = () => {
    if (month === 12) { setYear(year + 1); setMonth(1) }
    else setMonth(month + 1)
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
  // (utils/distribution ≡ services/distribution.py): доли округляются до
  // ТЫСЯЧИ методом floor + раздача недостающих тысяч по наибольшим хвостам, и
  // их сумма ровно равна «К выплате». Иначе экран и Excel разъезжаются.
  const companyOrder: Record<number, number> = {}
  ;(data?.companies ?? []).forEach((c, i) => { companyOrder[c.id] = i })

  const rowAmounts = (row: StatementRow): Record<number, number> => {
    if (isAutoRow(row) || isQuantityDeptRow(row)) {
      const m: Record<number, number> = {}
      for (const d of row.distribution) m[d.company_id] = num(d.amount)
      return m
    }
    const weights: Record<number, number> = {}
    for (const [cid, v] of Object.entries(edits[rowKey(row)] ?? {})) {
      if (num(v) > 0) weights[Number(cid)] = num(v)
    }
    const targeted = targetedAmounts(row)
    const base = distributionBase(row)
    // Без целевых сумм округляется сразу распределение базы по весам. С ними
    // сначала считаются ТОЧНЫЕ доли каскада от базы без целевых, к ним
    // прибавляются целевые, и до тысячи округляется уже весь набор — тот же
    // порядок, что в services/payroll_statement.py (finalize_distribution).
    if (Object.keys(targeted).length === 0) {
      return distributeToThousands(base, weights, companyOrder)
    }
    const cascade = cascadeBase(row)
    const weightSum = Object.values(weights).reduce((s, w) => s + w, 0)
    const exact: Record<number, number> = {}
    if (weightSum > 0) {
      for (const [cid, w] of Object.entries(weights)) {
        exact[Number(cid)] = (cascade * w) / weightSum
      }
    }
    for (const [cid, amount] of Object.entries(targeted)) {
      exact[Number(cid)] = (exact[Number(cid)] ?? 0) + amount
    }
    return distributeToThousands(base, exact, companyOrder)
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
      night: 0, premium: 0, kpi: 0, accrued: 0, deductions: 0, net: 0,
      dist: {} as Record<number, number>,
      distTotal: 0, unallocated: 0,
    }
    for (const c of companies) acc.dist[c.id] = 0
    for (const row of visibleRows) {
      acc.overtime += num(row.overtime_amount)
      acc.base += num(row.base_salary)
      acc.vacation += num(row.vacation_amount)
      acc.sick += num(row.sick_amount)
      acc.night += num(row.night_amount)
      acc.vacationDays += row.vacation_days
      acc.sickDays += row.sick_days
      acc.premium += num(row.premium_amount)
      acc.kpi += num(row.kpi_amount)
      acc.accrued += num(row.accrued_total)
      acc.deductions += num(row.deductions)
      acc.net += num(row.net_payout)
      const amounts = rowAmounts(row)
      let rowTotal = 0
      for (const [cid, amt] of Object.entries(amounts)) {
        if (Number(cid) in acc.dist) acc.dist[Number(cid)] += amt
        rowTotal += amt
      }
      acc.distTotal += rowTotal
      // Остаток складывается ПО СТРОКАМ (у каждой свой 0…999 ₽), а не берётся
      // как «начислено − разнесённое» от итогов: так он сходится с колонкой.
      acc.unallocated += distributionBase(row) - rowTotal
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
              <option key={c.id} value={c.id} title={c.name}>{companyLabel(c)}</option>
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
                <th
                  className="px-2 py-2 text-center font-medium"
                  title="Надбавка за ночные смены: число смен × (фонд отдела ÷ календарные дни месяца)"
                >
                  Ночные
                </th>
                <th className="px-2 py-2 text-center font-medium">Премия</th>
                <th className="px-2 py-2 text-center font-medium">KPI</th>
                <th className="px-2 py-2 text-center font-semibold text-blue-700 min-w-[90px]">Итого начисл.</th>
                <th className="px-2 py-2 text-center font-medium">Удержано</th>
                <th className="px-2 py-2 text-center font-semibold text-emerald-700 min-w-[90px]">К выплате</th>
                {companies.map((c) => (
                  <th key={c.id} className="px-2 py-2 text-center font-medium bg-indigo-50 min-w-[110px]" title={c.name}>
                    {companyLabel(c)} %/₽
                  </th>
                ))}
                <th
                  className="px-2 py-2 text-center font-medium"
                  title={
                    'Сумма распределения по юрлицам: «Итого начислено», округлённое ' +
                    'ВНИЗ до 1000 ₽. Разница 0…999 ₽ («ост.») — нераспределённый ' +
                    'остаток, он не приписывается юрлицам. Удержания базу не уменьшают.'
                  }
                >
                  Σ распред.
                </th>
                <th className="px-2 py-2 text-center font-medium">Примечание</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.length === 0 && (
                <tr><td colSpan={23} className="px-4 py-8 text-center text-gray-400">Нет сотрудников</td></tr>
              )}
              {visibleRows.map((row, i) => {
                const key = rowKey(row)
                // Фактические проценты и Σ распред. — от БАЗЫ распределения
                // («Итого начислено»): затраты возникают при начислении.
                const distBase = distributionBase(row)
                // Есть целевые премии/KPI (task_funding_source) → показываем
                // ФАКТИЧЕСКИЕ проценты: заданный каскадом их уже не описывает.
                const hasTargeted = num(row.targeted_total) > 0
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
                const byQuantity = isQuantityRow(row)
                // Правка заблокирована у всего отдела с показателем, включая
                // строки, ушедшие на распределение из карточки.
                const lockedByQuantity = isQuantityDeptRow(row)
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
                    {/* Ночные: надбавка = смены × ставка фонда отдела (входит в «Итого начислено») */}
                    <td
                      className="px-2 py-1.5 text-center text-gray-700"
                      title={
                        row.night_shifts
                          ? `${row.night_shifts} ночных смен × ${formatMoney(row.night_rate)}`
                          : 'Надбавка за ночные смены'
                      }
                    >
                      {formatMoney(row.night_amount)}
                      {row.night_shifts > 0 && (
                        <div className="text-[10px] text-gray-400">{row.night_shifts} см.</div>
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
                      title={payoutRoundingHint(row.net_payout_exact, row.rounding_tail)}
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
                      // Целевая премия/KPI (task_funding_source) «утяжеляет»
                      // своё юрлицо: фактический % расходится с заданным в
                      // каскаде (50/50 → 40/60), и без этой цифры расхождение
                      // выглядит ошибкой расчёта.
                      const targeted = targetedAmounts(row)[c.id] ?? 0
                      const effPct = hasTargeted && distBase > 0
                        ? Math.round((amount / distBase) * 10000) / 100
                        : null
                      return (
                        <td key={c.id} className="px-1.5 py-1 text-center bg-indigo-50/40">
                          <div className="flex items-center justify-center gap-1">
                            <input
                              type="number"
                              min={0}
                              max={100}
                              step="0.1"
                              disabled={!canEdit || lockedByQuantity}
                              value={byQuantity ? '' : pct}
                              onChange={(ev) => setPercent(key, c.id, ev.target.value)}
                              className={`w-12 rounded border px-1 py-0.5 text-right text-[11px] ${pctWarn ? 'border-amber-400 bg-amber-50' : 'border-gray-300'} ${autoEntry ? 'border-dashed text-gray-400 placeholder:text-gray-400' : ''} disabled:bg-gray-100`}
                              placeholder={byQuantity ? quantityPct(row, c.id) : autoPctLabel}
                              title={
                                byQuantity
                                  ? 'Процент из количественного показателя отдела — правится в его табеле'
                                  : lockedByQuantity
                                    ? 'Процент из карточки сотрудника — правится в его карточке'
                                    : autoEntry ? 'Авто по часам — введите % чтобы задать вручную' : undefined
                              }
                            />
                            <span className="text-gray-400">%</span>
                          </div>
                          <div className={`mt-0.5 text-[10px] ${autoEntry ? 'italic text-gray-400' : 'text-gray-500'}`}>
                            {amount > 0 ? formatMoney(String(amount)) : '—'}
                          </div>
                          {effPct !== null && amount > 0 && (
                            <div
                              className="text-[9px] text-violet-600"
                              title={
                                targeted > 0
                                  ? `Фактически ${effPct}% от «Итого начислено» (включая целевые ${formatMoney(String(targeted))})`
                                  : `Фактически ${effPct}% от «Итого начислено»`
                              }
                            >
                              факт. {effPct}%{targeted > 0 ? ' 🎯' : ''}
                            </div>
                          )}
                        </td>
                      )
                    })}
                    {/* Σ распред. = «Итого начислено», округлённое ВНИЗ до
                        тысячи. Разница (0…999 ₽) — нераспределённый остаток: он
                        не приписывается юрлицам, иначе их затраты оказались бы
                        больше начисленного. Показываем его прямо под суммой,
                        иначе расхождение с «Итого начислено» выглядит ошибкой. */}
                    <td className={`px-2 py-1.5 text-center font-medium ${liveDistTotal === distributable(distBase) ? 'text-gray-600' : 'text-amber-600'}`}>
                      {formatMoney(String(liveDistTotal))}
                      {distBase - liveDistTotal > 0 && (
                        <div
                          className="text-[10px] text-gray-400"
                          title={
                            'Нераспределённый остаток: «Итого начислено» минус разнесённое. ' +
                            'Суммы по юрлицам кратны 1000 ₽ и округляются вниз, ' +
                            'поэтому остаётся 0…999 ₽ — они не приписываются никому.'
                          }
                        >
                          ост. {formatMoney(String(Math.round((distBase - liveDistTotal) * 100) / 100))}
                        </div>
                      )}
                      {pctWarn && <div className="text-[10px] text-amber-600">Σ%={pctSum}</div>}
                    </td>
                    <td className="px-2 py-1.5 text-center whitespace-nowrap">
                      {canEdit && !lockedByQuantity && (
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
                      {/* Откуда взято распределение — каскад ч.3 (или заявки) */}
                      <div className={`mt-0.5 text-[9px] ${SOURCE_STYLE[sourceKey]}`}>
                        {SOURCE_LABEL[sourceKey]}
                      </div>
                      {/* Почему проценты не правятся: отдел делится по своему
                          показателю, а исключения задаются в карточке
                          (task_card_priority) */}
                      {lockedByQuantity && (
                        <div
                          className="mt-0.5 text-[9px] leading-tight text-gray-500"
                          title={`Распределение по показателю «${row.quantity_metric_name}»; индивидуальные исключения задаются в карточке сотрудника`}
                        >
                          распределение по «{row.quantity_metric_name}»;
                          <br />
                          исключения — в карточке сотрудника
                        </div>
                      )}
                      {/* Отдел «по заявкам», но заявок за месяц нет → каскад */}
                      {row.distribution_note && (
                        <div className="mt-0.5 text-[9px] text-amber-600" title={row.distribution_note}>
                          ⚠ заявки не заданы
                        </div>
                      )}
                      {/* Целевые премии/KPI: объясняют, почему фактический %
                          юрлица отличается от заданного в каскаде */}
                      {row.targeted_note && (
                        <div className="mt-0.5 text-[9px] text-violet-600" title={row.targeted_note}>
                          🎯 {row.targeted_note}
                        </div>
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
                <td className="px-2 py-2 text-center text-gray-600">
                  {footer.vacationDays || footer.sickDays ? `${footer.vacationDays} / ${footer.sickDays}` : '—'}
                </td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.vacation))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.sick))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.night))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.premium))}</td>
                <td className="px-2 py-2 text-center text-gray-700">{formatMoney(String(footer.kpi))}</td>
                <td className="px-2 py-2 text-center font-bold text-blue-700">{formatMoney(String(footer.accrued), { showZero: true })}</td>
                <td className="px-2 py-2 text-center text-rose-600">{formatMoney(String(footer.deductions))}</td>
                <td className="px-2 py-2 text-center font-bold text-emerald-700">{formatMoney(String(footer.net), { showZero: true })}</td>
                {companies.map((c) => (
                  <td key={c.id} className="px-2 py-2 text-center text-gray-700 bg-indigo-50/40">{formatMoney(String(footer.dist[c.id] ?? 0))}</td>
                ))}
                <td className="px-2 py-2 text-center text-gray-700">
                  {formatMoney(String(footer.distTotal), { showZero: true })}
                  {footer.unallocated > 0 && (
                    <div
                      className="text-[10px] font-normal text-gray-400"
                      title={
                        'Нераспределённый остаток за месяц: «Итого начислено» минус ' +
                        'разнесённое по юрлицам. Следствие округления сумм вниз до 1000 ₽; ' +
                        'не путать с «Эффектом округления» на дашборде — тот про выплату.'
                      }
                    >
                      ост. {formatMoney(String(Math.round(footer.unallocated * 100) / 100))}
                    </div>
                  )}
                </td>
                <td className="px-2 py-2" />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  )
}
