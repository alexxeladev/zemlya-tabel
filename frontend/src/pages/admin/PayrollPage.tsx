import { useEffect, useState } from 'react'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type { CompanyBreakdown, Department, EmployeePayroll, PayrollSummary } from '../../types/api'
import { timesheetApi } from '../../api/timesheet'
import { apiClient } from '../../api/client'
import { formatDelta, formatHours, formatMoney } from '../../utils/money'

const MONTH_NAMES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

function formatPercent(value: string): string {
  const n = Number(value)
  if (Number.isNaN(n)) return ''
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

function BreakdownRow({ bd }: { bd: CompanyBreakdown }) {
  return (
    <tr className="text-[11px] bg-gray-50 text-gray-500">
      <td className="pl-10 pr-3 py-1 whitespace-nowrap">{bd.company_code} — {bd.company_name}</td>
      <td className="px-2 py-1 text-center whitespace-nowrap">{formatHours(bd.hours)} <span className="text-gray-400">({formatPercent(bd.percent)}%)</span></td>
      <td className="px-2 py-1 text-center" />
      <td className="px-2 py-1 text-center" />
      <td className="px-2 py-1 text-center" />
      <td className="px-2 py-1 text-center" />
      <td className="px-2 py-1 text-center">{formatMoney(bd.base_amount)}</td>
      <td className="px-2 py-1 text-center">{formatMoney(bd.overtime_amount)}</td>
      <td className="px-2 py-1 text-center">{formatMoney(bd.holiday_amount)}</td>
      <td className="px-2 py-1 text-center font-medium text-blue-700">{formatMoney(bd.total)}</td>
    </tr>
  )
}

function EmployeeRow({ ep }: { ep: EmployeePayroll }) {
  const [expanded, setExpanded] = useState(false)
  const delta = formatDelta(ep.delta_hours)

  return (
    <>
      <tr
        className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="px-3 py-2 whitespace-nowrap text-sm font-medium text-gray-800">
          <span className="mr-1 text-gray-400 text-xs">{expanded ? '▼' : '▶'}</span>
          {ep.employee_name}
          {!ep.is_calculable && (
            <span className="ml-2 text-[10px] text-gray-400 italic" title={ep.reason_if_not_calculable ?? ''}>
              ({ep.reason_if_not_calculable})
            </span>
          )}
        </td>
        <td className="px-2 py-2 text-center text-sm text-gray-700">{formatHours(ep.total_hours)}</td>
        <td className="px-2 py-2 text-center text-sm text-gray-600">{formatHours(ep.norm_hours)}</td>
        <td className={`px-2 py-2 text-center text-sm ${delta.className}`}>{delta.text}</td>
        <td className="px-2 py-2 text-center text-sm text-gray-600">{ep.norm_days ?? '—'}</td>
        <td className="px-2 py-2 text-center text-sm text-gray-700">{ep.fact_days}</td>
        <td className="px-2 py-2 text-center text-sm text-gray-700">
          {ep.is_calculable ? formatMoney(ep.base_amount) : <span className="text-gray-400">—</span>}
        </td>
        <td className="px-2 py-2 text-center text-sm text-gray-700">
          {ep.is_calculable ? formatMoney(ep.overtime_amount) : <span className="text-gray-400">—</span>}
        </td>
        <td className="px-2 py-2 text-center text-sm text-gray-700">
          {ep.is_calculable ? formatMoney(ep.holiday_amount) : <span className="text-gray-400">—</span>}
        </td>
        <td className="px-2 py-2 text-center text-sm font-bold text-blue-700">
          {ep.is_calculable ? formatMoney(ep.total_amount) : <span className="text-gray-400 font-normal">—</span>}
        </td>
      </tr>
      {expanded && ep.breakdown_by_company.map((bd) => (
        <BreakdownRow key={bd.company_id} bd={bd} />
      ))}
    </>
  )
}

export function PayrollPage() {
  const user = useAuthStore((s) => s.user)
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [departmentId, setDepartmentId] = useState<number | undefined>(undefined)
  const [departments, setDepartments] = useState<Department[]>([])
  const [data, setData] = useState<PayrollSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiClient.get<Department[]>('/api/departments').then((r) => setDepartments(r.data)).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    timesheetApi.getPayroll(year, month, departmentId)
      .then(setData)
      .catch(() => toast.error('Не удалось загрузить расчёт ЗП'))
      .finally(() => setLoading(false))
  }, [year, month, departmentId])

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

  const isManager = user?.role === 'manager'

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-3 shadow-sm">
        <h1 className="text-lg font-bold text-gray-900">Расчёт ЗП</h1>
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
        </div>
      </div>

      {loading && (
        <div className="flex h-32 items-center justify-center text-gray-400">Загрузка...</div>
      )}

      {!loading && data && (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
              <p className="text-xs text-gray-500">Сотрудников</p>
              <p className="text-xl font-bold text-gray-800">{data.total_employees}</p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
              <p className="text-xs text-gray-500">Часов отработано</p>
              <p className="text-xl font-bold text-gray-800">{formatHours(data.total_hours)}</p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
              <p className="text-xs text-gray-500">Сверхурочные</p>
              <p className="text-xl font-bold text-amber-600">{formatMoney(data.total_overtime_amount, { showZero: true })}</p>
            </div>
            <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 shadow-sm">
              <p className="text-xs text-blue-600">Итого к выплате</p>
              <p className="text-xl font-bold text-blue-700">{formatMoney(data.grand_total, { showZero: true })}</p>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-auto rounded-xl border border-gray-200 bg-white shadow-sm">
            <table className="min-w-full border-collapse text-xs">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-3 py-2 text-left font-medium text-gray-500 whitespace-nowrap min-w-[200px]">Сотрудник</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[60px]">Часов</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[60px]">Норма</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[44px]">Δ</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[60px]" title="Рабочих дней по производственному календарю">Норма дн.</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[60px]" title="Дней, в которые есть отметки часов">Факт дн.</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[80px]">Оклад</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[80px]">Сверхур.</th>
                  <th className="px-2 py-2 text-center font-medium text-gray-500 whitespace-nowrap min-w-[80px]">Праздн.</th>
                  <th className="px-2 py-2 text-center font-semibold text-blue-700 whitespace-nowrap min-w-[90px]">Итого ₽</th>
                </tr>
              </thead>
              <tbody>
                {data.employees.length === 0 && (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-gray-400">
                      Нет сотрудников
                    </td>
                  </tr>
                )}
                {data.employees.map((ep) => (
                  <EmployeeRow key={ep.employee_id} ep={ep} />
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-gray-300 bg-gray-50 font-semibold text-xs">
                  <td className="px-3 py-2 text-gray-700">Итого</td>
                  <td className="px-2 py-2 text-center text-gray-700">{formatHours(data.total_hours)}</td>
                  <td className="px-2 py-2 text-center" />
                  <td className="px-2 py-2 text-center" />
                  <td className="px-2 py-2 text-center" />
                  <td className="px-2 py-2 text-center" />
                  <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_base_amount)}</td>
                  <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_overtime_amount)}</td>
                  <td className="px-2 py-2 text-center text-gray-700">{formatMoney(data.total_holiday_amount)}</td>
                  <td className="px-2 py-2 text-center font-bold text-blue-700">{formatMoney(data.grand_total, { showZero: true })}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
