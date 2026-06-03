import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import type { Company, Department, MonthSummary, TimesheetEntry, TimesheetMonthResponse } from '../types/api'
import { timesheetApi } from '../api/timesheet'
import { apiClient } from '../api/client'
import { ApiError } from '../api/client'

const MONTH_NAMES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const WEEKDAY_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']

const COMPANY_COLORS = [
  { bg: 'bg-blue-50', text: 'text-blue-700', cell: 'bg-blue-50/60' },
  { bg: 'bg-green-50', text: 'text-green-700', cell: 'bg-green-50/60' },
  { bg: 'bg-purple-50', text: 'text-purple-700', cell: 'bg-purple-50/60' },
  { bg: 'bg-orange-50', text: 'text-orange-700', cell: 'bg-orange-50/60' },
  { bg: 'bg-rose-50', text: 'text-rose-700', cell: 'bg-rose-50/60' },
  { bg: 'bg-teal-50', text: 'text-teal-700', cell: 'bg-teal-50/60' },
  { bg: 'bg-amber-50', text: 'text-amber-700', cell: 'bg-amber-50/60' },
  { bg: 'bg-indigo-50', text: 'text-indigo-700', cell: 'bg-indigo-50/60' },
]

function companyColor(companyId: number, companies: Company[]) {
  const idx = companies.findIndex((c) => c.id === companyId)
  return COMPANY_COLORS[Math.max(0, idx) % COMPANY_COLORS.length]
}

interface CellKey {
  employeeId: number
  workDate: string
  companyId: number
}

function makeCellKey(k: CellKey) {
  return `${k.employeeId}_${k.workDate}_${k.companyId}`
}

type EntryMap = Map<string, number>

function buildEntryMap(entries: TimesheetEntry[]): EntryMap {
  const m = new Map<string, number>()
  for (const e of entries) {
    m.set(makeCellKey({ employeeId: e.employee_id, workDate: e.work_date, companyId: e.company_id }), parseFloat(e.hours as unknown as string))
  }
  return m
}

function padDate(n: number) {
  return String(n).padStart(2, '0')
}

function toWorkDate(year: number, month: number, day: number): string {
  return `${year}-${padDate(month)}-${padDate(day)}`
}

interface SavingIndicatorProps {
  savingCount: number
}

function SavingIndicator({ savingCount }: SavingIndicatorProps) {
  if (savingCount === 0) return null
  return (
    <div className="fixed bottom-4 right-4 flex items-center gap-2 rounded-lg bg-white px-3 py-2 shadow-lg border border-gray-200 text-sm text-gray-600">
      <svg className="h-4 w-4 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
      </svg>
      Сохранение...
    </div>
  )
}

interface CellInputProps {
  value: number | undefined
  onSave: (v: number) => Promise<void>
  disabled?: boolean
  dayBgClass: string
}

function CellInput({ value, onSave, disabled, dayBgClass }: CellInputProps) {
  const [localVal, setLocalVal] = useState(value !== undefined ? String(value) : '')
  const prevRef = useRef(value !== undefined ? String(value) : '')

  useEffect(() => {
    const s = value !== undefined ? String(value) : ''
    setLocalVal(s)
    prevRef.current = s
  }, [value])

  const handleBlur = async () => {
    const num = localVal === '' ? 0 : parseFloat(localVal)
    if (isNaN(num)) {
      setLocalVal(prevRef.current)
      return
    }
    if (String(num) === prevRef.current || (num === 0 && prevRef.current === '')) return
    await onSave(num)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setLocalVal(prevRef.current)
      e.currentTarget.blur()
    } else if (e.key === 'Enter') {
      e.currentTarget.blur()
      // Move to next row same column
      const allInputs = Array.from(document.querySelectorAll<HTMLInputElement>('input[data-tabelcell]'))
      const idx = allInputs.indexOf(e.currentTarget)
      if (idx >= 0 && idx + 1 < allInputs.length) allInputs[idx + 1].focus()
    }
  }

  return (
    <input
      data-tabelcell
      type="number"
      min={0}
      max={24}
      step={0.5}
      disabled={disabled}
      value={localVal}
      onChange={(e) => setLocalVal(e.target.value)}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      className={`w-full h-full min-h-[2rem] px-1 text-center text-sm outline-none focus:ring-1 focus:ring-blue-400 focus:rounded ${dayBgClass} disabled:cursor-not-allowed`}
    />
  )
}

export function TimesheetPage() {
  const user = useAuthStore((s) => s.user)
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [departmentId, setDepartmentId] = useState<number | undefined>(undefined)
  const [departments, setDepartments] = useState<Department[]>([])
  const [data, setData] = useState<TimesheetMonthResponse | null>(null)
  const [calendar, setCalendar] = useState<MonthSummary | null>(null)
  const [entryMap, setEntryMap] = useState<EntryMap>(new Map())
  const [savingCount, setSavingCount] = useState(0)
  const [loading, setLoading] = useState(true)

  const canFilterDept = user?.role === 'admin' || user?.role === 'accountant'

  useEffect(() => {
    if (!canFilterDept) return
    apiClient.get<Department[]>('/api/departments').then((r) => setDepartments(r.data)).catch(() => {})
  }, [canFilterDept])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [monthData, calData] = await Promise.all([
        timesheetApi.getMonth(year, month, canFilterDept ? departmentId : undefined),
        apiClient.get<MonthSummary>(`/api/calendar/${year}/${month}/summary`).then((r) => r.data).catch(() => null),
      ])
      setData(monthData)
      setCalendar(calData)
      setEntryMap(buildEntryMap(monthData.entries))
    } catch {
      toast.error('Не удалось загрузить табель')
    } finally {
      setLoading(false)
    }
  }, [year, month, departmentId, canFilterDept])

  useEffect(() => { loadData() }, [loadData])

  const handleSaveCell = useCallback(async (
    employeeId: number, workDate: string, companyId: number, hours: number
  ) => {
    const key = makeCellKey({ employeeId, workDate, companyId })
    const prevVal = entryMap.get(key)

    // Optimistic update
    setEntryMap((prev) => {
      const next = new Map(prev)
      if (hours === 0) next.delete(key)
      else next.set(key, hours)
      return next
    })

    setSavingCount((n) => n + 1)
    try {
      await timesheetApi.saveCell({ employee_id: employeeId, work_date: workDate, company_id: companyId, hours })
    } catch (err) {
      // Rollback
      setEntryMap((prev) => {
        const next = new Map(prev)
        if (prevVal !== undefined) next.set(key, prevVal)
        else next.delete(key)
        return next
      })
      const msg = err instanceof ApiError ? err.message : 'Ошибка сохранения'
      toast.error(msg)
    } finally {
      setSavingCount((n) => n - 1)
    }
  }, [entryMap])

  const prevMonth = () => {
    if (month === 1) { setYear((y) => y - 1); setMonth(12) }
    else setMonth((m) => m - 1)
  }
  const nextMonth = () => {
    if (month === 12) { setYear((y) => y + 1); setMonth(1) }
    else setMonth((m) => m + 1)
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-400">
        Загрузка...
      </div>
    )
  }

  if (!data) return null

  const { employees, companies } = data

  if (companies.length === 0) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-gray-500">
        Не настроены компании, обратитесь к админу
      </div>
    )
  }

  if (employees.length === 0) {
    return (
      <div className="space-y-4">
        <TimesheetHeader
          year={year} month={month} onPrev={prevMonth} onNext={nextMonth}
          canFilterDept={canFilterDept} departments={departments}
          departmentId={departmentId} onDeptChange={setDepartmentId}
        />
        <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-gray-500">
          Нет сотрудников в отделе
        </div>
      </div>
    )
  }

  // Build days array
  const daysInMonth = new Date(year, month, 0).getDate()
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1)

  // Day type helpers
  const getDayType = (day: number): 'holiday' | 'short' | 'work' => {
    if (!calendar) return 'work'
    const info = calendar.days.find((d) => d.day === day)
    return info?.type ?? 'work'
  }

  const dayHeaderClass = (day: number) => {
    const t = getDayType(day)
    if (t === 'holiday') return 'bg-red-50 text-red-700'
    if (t === 'short') return 'bg-yellow-50 text-yellow-700'
    return 'bg-gray-50 text-gray-600'
  }

  const dayCellBg = (day: number) => {
    const t = getDayType(day)
    if (t === 'holiday') return 'bg-red-50/40'
    if (t === 'short') return 'bg-yellow-50/40'
    return ''
  }

  // Employee totals
  const employeeTotal = (empId: number) => {
    let sum = 0
    for (const [key, hours] of entryMap.entries()) {
      if (key.startsWith(`${empId}_`)) sum += hours
    }
    return sum
  }

  const weekdayOf = (day: number) => {
    return WEEKDAY_SHORT[new Date(year, month - 1, day).getDay()]
  }

  return (
    <div className="space-y-4">
      <TimesheetHeader
        year={year} month={month} onPrev={prevMonth} onNext={nextMonth}
        canFilterDept={canFilterDept} departments={departments}
        departmentId={departmentId} onDeptChange={setDepartmentId}
      />

      {/* Scrollable grid */}
      <div className="overflow-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full border-collapse text-xs">
          <thead>
            <tr className="sticky top-0 z-10 bg-white shadow-sm">
              <th className="sticky left-0 z-20 bg-white px-3 py-2 text-left font-medium text-gray-500 whitespace-nowrap border-b border-r border-gray-200 min-w-[140px]">
                Сотрудник
              </th>
              <th className="sticky left-[140px] z-20 bg-white px-2 py-2 text-left font-medium text-gray-500 whitespace-nowrap border-b border-r border-gray-200 min-w-[80px]">
                Компания
              </th>
              {days.map((d) => (
                <th
                  key={d}
                  className={`border-b border-r border-gray-200 px-1 py-1 text-center font-medium whitespace-nowrap min-w-[42px] ${dayHeaderClass(d)}`}
                >
                  <div>{d}</div>
                  <div className="text-[10px] font-normal opacity-70">{weekdayOf(d)}</div>
                </th>
              ))}
              <th className="sticky right-0 z-20 bg-white border-b border-l border-gray-200 px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[52px]">
                Итого
              </th>
            </tr>
          </thead>
          <tbody>
            {employees.map((emp, empIdx) => {
              const total = employeeTotal(emp.id)
              const isLastEmp = empIdx === employees.length - 1
              return companies.map((company, cIdx) => {
                const isFirstRow = cIdx === 0
                const isLastRow = cIdx === companies.length - 1
                const rowBorderBottom = isLastRow && !isLastEmp ? 'border-b-2 border-gray-300' : 'border-b border-gray-100'
                const cc = companyColor(company.id, companies)

                return (
                  <tr key={`${emp.id}_${company.id}`} className={rowBorderBottom}>
                    {/* Employee name — only first row of employee block */}
                    <td className={`sticky left-0 z-10 bg-white border-r border-gray-200 px-3 py-1 whitespace-nowrap font-medium text-gray-800 ${isFirstRow ? '' : 'text-transparent select-none'}`}>
                      {isFirstRow ? emp.full_name : ''}
                    </td>

                    {/* Company name */}
                    <td className={`sticky left-[140px] z-10 border-r border-gray-200 px-2 py-1 whitespace-nowrap ${cc.bg} ${cc.text}`}>
                      {company.code}
                    </td>

                    {/* Day cells */}
                    {days.map((d) => {
                      const workDate = toWorkDate(year, month, d)
                      const key = makeCellKey({ employeeId: emp.id, workDate, companyId: company.id })
                      const val = entryMap.get(key)
                      return (
                        <td key={d} className={`border-r border-gray-100 p-0 ${dayCellBg(d)} ${cc.cell}`}>
                          <CellInput
                            value={val}
                            dayBgClass={dayCellBg(d)}
                            onSave={(hours) => handleSaveCell(emp.id, workDate, company.id, hours)}
                          />
                        </td>
                      )
                    })}

                    {/* Total — only first row */}
                    <td className={`sticky right-0 z-10 bg-white border-l border-gray-200 px-2 py-1 text-center font-semibold ${isFirstRow && total > 0 ? 'text-gray-800' : 'text-transparent select-none'}`}>
                      {isFirstRow && total > 0 ? total : ''}
                    </td>
                  </tr>
                )
              })
            })}
          </tbody>
        </table>
      </div>

      <SavingIndicator savingCount={savingCount} />
    </div>
  )
}

interface TimesheetHeaderProps {
  year: number
  month: number
  onPrev: () => void
  onNext: () => void
  canFilterDept: boolean
  departments: Department[]
  departmentId: number | undefined
  onDeptChange: (id: number | undefined) => void
}

function TimesheetHeader({
  year, month, onPrev, onNext,
  canFilterDept, departments, departmentId, onDeptChange,
}: TimesheetHeaderProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-3 shadow-sm">
      <h1 className="text-lg font-bold text-gray-900">Табель</h1>

      <div className="flex items-center gap-2">
        <button
          onClick={onPrev}
          className="rounded-md p-1 text-gray-500 hover:bg-gray-100 transition-colors"
          aria-label="Предыдущий месяц"
        >
          ←
        </button>
        <span className="min-w-[120px] text-center text-sm font-medium text-gray-700">
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button
          onClick={onNext}
          className="rounded-md p-1 text-gray-500 hover:bg-gray-100 transition-colors"
          aria-label="Следующий месяц"
        >
          →
        </button>
      </div>

      {canFilterDept && departments.length > 0 && (
        <select
          value={departmentId ?? ''}
          onChange={(e) => onDeptChange(e.target.value === '' ? undefined : Number(e.target.value))}
          className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
        >
          <option value="">Все отделы</option>
          {departments.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
      )}
    </div>
  )
}
