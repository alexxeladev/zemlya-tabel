// Дашборд (задача 4.1): KPI-карточки + графики recharts.
// Данные — один запрос GET /api/dashboard/{year}/{month}, видимость по ролям на бэке:
//   admin    — вся компания, 4 блока
//   accountant — статусы периодов (главный) + ФОТ + динамика
//   manager  — те же блоки, но только свой отдел
//   employee — личный виджет часов, без финансов
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { getDashboard, type DashboardData, type PeriodStatusRow } from '../api/dashboard'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import { formatHours, formatMoney } from '../utils/money'
import { CHART, companyColorByIndex, PERIOD_STATUS } from '../utils/colors'

const MONTH_NAMES_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]
const MONTH_SHORT_RU = [
  'янв', 'фев', 'мар', 'апр', 'май', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
]

function num(v: string | null | undefined): number {
  if (v === null || v === undefined) return 0
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : 0
}

// ── Мелкие компоненты ─────────────────────────────────────────────────────────

function KpiCard({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${accent ?? 'text-gray-900'}`}>{value}</div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h2>
      {children}
    </section>
  )
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-2 text-sm font-medium text-gray-700">{title}</div>
      {children}
    </div>
  )
}

function StatusBadge({ status, overdue }: { status: PeriodStatusRow['status']; overdue?: boolean }) {
  const s = PERIOD_STATUS[status]
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${overdue ? 'bg-red-100 text-red-700' : s.badge}`}>
      {s.label}
      {overdue ? ' · просрочен' : ''}
    </span>
  )
}

// ── Страница ──────────────────────────────────────────────────────────────────

export function DashboardPage() {
  const user = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getDashboard(year, month)
      .then((d) => { if (!cancelled) setData(d) })
      .catch((err) => {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        toast.error('Ошибка загрузки дашборда: ' + msg)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [year, month, reloadKey])

  const prevMonth = () => (month === 1 ? (setMonth(12), setYear(year - 1)) : setMonth(month - 1))
  const nextMonth = () => (month === 12 ? (setMonth(1), setYear(year + 1)) : setMonth(month + 1))

  const gotoTimesheet = (deptId: number | null, y = year, m = month) => {
    const dept = deptId !== null ? `&department_id=${deptId}` : ''
    navigate(`/timesheet?year=${y}&month=${m}${dept}`)
  }

  if (!user) return null
  const role = data?.role ?? user.role

  return (
    <div className="space-y-6">
      {/* Шапка: заголовок + переключатель месяца */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Дашборд</h1>
        <div className="flex items-center gap-2">
          <button onClick={prevMonth} className="rounded p-2 hover:bg-gray-100" aria-label="Предыдущий месяц">←</button>
          <div className="min-w-[160px] text-center text-base font-semibold">
            {MONTH_NAMES_RU[month - 1]} {year}
          </div>
          <button onClick={nextMonth} className="rounded p-2 hover:bg-gray-100" aria-label="Следующий месяц">→</button>
        </div>
      </div>

      {loading && !data && <div className="p-8 text-gray-500">Загрузка…</div>}

      {error && !loading && !data && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700">
          Не удалось загрузить дашборд: {error}
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="ml-3 rounded border border-red-300 px-2 py-1 text-xs hover:bg-red-100"
          >
            Повторить
          </button>
        </div>
      )}

      {data && (
        <div className={`space-y-8 ${loading ? 'opacity-60' : ''}`}>
          {role === 'employee' ? (
            <EmployeeView data={data} />
          ) : (
            <>
              {role !== 'accountant' && <HoursBlock data={data} onDeptClick={gotoTimesheet} />}
              {data.periods && role === 'accountant' && (
                <PeriodsBlockView data={data} onRowClick={gotoTimesheet} />
              )}
              {data.payroll && <PayrollBlock data={data} />}
              {data.periods && role !== 'accountant' && (
                <PeriodsBlockView data={data} onRowClick={gotoTimesheet} />
              )}
              <TrendBlock data={data} />
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Блок 1: часы ──────────────────────────────────────────────────────────────

function HoursBlock({ data, onDeptClick }: {
  data: DashboardData
  onDeptClick: (deptId: number | null) => void
}) {
  const h = data.hours
  const chartData = data.hours_by_department.map((d) => ({
    name: d.department_name,
    deptId: d.department_id,
    Норма: num(d.norm_hours),
    Отработано: num(d.total_hours),
    Переработка: num(d.overtime_hours),
  }))

  return (
    <Section title="Часы">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Отработано" value={`${formatHours(h.total_hours)} ч`} />
        <KpiCard label="Норма" value={h.norm_hours !== null ? `${formatHours(h.norm_hours)} ч` : '—'} />
        <KpiCard label="Переработка" value={`${formatHours(h.overtime_hours)} ч`}
                 accent={num(h.overtime_hours) > 0 ? 'text-amber-600' : undefined} />
        <KpiCard label="Выполнение нормы" value={h.percent_of_norm !== null ? `${h.percent_of_norm}%` : '—'} />
      </div>

      {chartData.length > 0 && (
        <ChartCard title="Часы по отделам (клик — в табель отдела)">
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={chartData} onClick={(e) => {
              const idx = e?.activeTooltipIndex
              if (typeof idx === 'number' && chartData[idx]) onDeptClick(chartData[idx].deptId)
            }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip formatter={(v) => `${v} ч`} />
              <Legend />
              <Bar dataKey="Норма" fill={CHART.norm} radius={[3, 3, 0, 0]} cursor="pointer" />
              <Bar dataKey="Отработано" fill={CHART.worked} radius={[3, 3, 0, 0]} cursor="pointer" />
              <Bar dataKey="Переработка" fill={CHART.overtime} radius={[3, 3, 0, 0]} cursor="pointer" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      )}
    </Section>
  )
}

// ── Блок 2: ФОТ ───────────────────────────────────────────────────────────────

function PayrollBlock({ data }: { data: DashboardData }) {
  const p = data.payroll!
  const byDept = data.payroll_by_department.map((d) => ({
    name: d.department_name,
    ФОТ: num(d.total),
  }))
  const byCompany = data.payroll_by_company
    .filter((c) => num(c.total) > 0)
    .map((c, i) => ({
      name: c.company_name,
      code: c.company_code,
      value: num(c.total),
      fill: companyColorByIndex(i).color,
    }))

  return (
    <Section title="ФОТ (брутто к начислению)">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Всего начислено" value={formatMoney(p.total, { showZero: true })} accent="text-blue-700" />
        <KpiCard label="Оклады" value={formatMoney(p.base, { showZero: true })} />
        <KpiCard label="Переработка" value={formatMoney(p.overtime, { showZero: true })} />
        <KpiCard label="Праздничные" value={formatMoney(p.holiday, { showZero: true })} />
      </div>

      {p.non_calculable_employees > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {p.non_calculable_employees} сотр. не в расчёте (нет оклада/графика или сменный график) — ФОТ по ним не учтён
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {byDept.length > 1 && (
          <ChartCard title="ФОТ по отделам">
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={byDept}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => `${Math.round(v / 1000)}т`} />
                <Tooltip formatter={(v) => formatMoney(String(v))} />
                <Bar dataKey="ФОТ" fill={CHART.payroll} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {byCompany.length > 0 && (
          <ChartCard title="ФОТ по компаниям (юрлицам)">
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie data={byCompany} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90}>
                  {byCompany.map((c) => <Cell key={c.name} fill={c.fill} />)}
                </Pie>
                <Tooltip formatter={(v) => formatMoney(String(v))} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>
        )}
      </div>
    </Section>
  )
}

// ── Блок 3: статусы периодов ──────────────────────────────────────────────────

function PeriodsBlockView({ data, onRowClick }: {
  data: DashboardData
  onRowClick: (deptId: number | null, y?: number, m?: number) => void
}) {
  const pb = data.periods!
  const allRows = [...pb.overdue_rows, ...pb.rows]

  return (
    <Section title="Статусы периодов">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Закрыто" value={String(pb.counts.closed)} accent="text-green-700" />
        <KpiCard label="На проверке" value={String(pb.counts.pending_review)} accent="text-yellow-700" />
        <KpiCard label="В черновике" value={String(pb.counts.draft)} />
        <KpiCard label="Просрочено" value={String(pb.counts.overdue)}
                 accent={pb.counts.overdue > 0 ? 'text-red-600' : 'text-gray-400'} />
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="px-4 py-2">Отдел</th>
              <th className="px-4 py-2">Период</th>
              <th className="px-4 py-2">Статус</th>
              <th className="px-4 py-2">Отправил / закрыл</th>
              <th className="px-4 py-2 text-right">Действие</th>
            </tr>
          </thead>
          <tbody>
            {allRows.map((r) => (
              <tr
                key={`${r.department_id ?? 'null'}-${r.year}-${r.month}`}
                onClick={() => onRowClick(r.department_id, r.year, r.month)}
                className={`cursor-pointer border-b border-gray-100 last:border-0 hover:bg-blue-50/40 ${r.is_overdue ? 'bg-red-50/60' : ''}`}
              >
                <td className="px-4 py-2 font-medium text-gray-900">{r.department_name}</td>
                <td className="px-4 py-2 text-gray-600">{MONTH_SHORT_RU[r.month - 1]} {r.year}</td>
                <td className="px-4 py-2"><StatusBadge status={r.status} overdue={r.is_overdue} /></td>
                <td className="px-4 py-2 text-gray-600">{r.closed_by_name ?? r.submitted_by_name ?? '—'}</td>
                <td className="px-4 py-2 text-right text-xs text-blue-600">Открыть табель →</td>
              </tr>
            ))}
            {allRows.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-400">Нет отделов</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </Section>
  )
}

// ── Блок 4: динамика ──────────────────────────────────────────────────────────

function TrendBlock({ data }: { data: DashboardData }) {
  const points = data.trend.map((t) => ({
    name: `${MONTH_SHORT_RU[t.month - 1]} ${String(t.year).slice(2)}`,
    Часы: num(t.total_hours),
    Переработка: num(t.overtime_hours),
    ФОТ: t.payroll_total !== null ? num(t.payroll_total) : undefined,
  }))
  const hasMoney = data.trend.some((t) => t.payroll_total !== null)
  const hasData = points.some((p) => p.Часы > 0)

  return (
    <Section title="Динамика по месяцам">
      {!hasData || points.length < 2 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 shadow-sm">
          Недостаточно данных для динамики — график накопится со временем.
        </div>
      ) : (
        <ChartCard title={hasMoney ? 'Часы и ФОТ' : 'Часы'}>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={points}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis yAxisId="hours" tick={{ fontSize: 12 }} />
              {hasMoney && (
                <YAxis yAxisId="money" orientation="right" tick={{ fontSize: 12 }}
                       tickFormatter={(v) => `${Math.round(v / 1000)}т`} />
              )}
              <Tooltip formatter={(v, name) => (name === 'ФОТ' ? formatMoney(String(v)) : `${v} ч`)} />
              <Legend />
              <Line yAxisId="hours" type="monotone" dataKey="Часы" stroke={CHART.hours} strokeWidth={2} dot />
              <Line yAxisId="hours" type="monotone" dataKey="Переработка" stroke={CHART.overtime} strokeWidth={2} dot />
              {hasMoney && (
                <Line yAxisId="money" type="monotone" dataKey="ФОТ" stroke={CHART.payroll} strokeWidth={2} dot />
              )}
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      )}
    </Section>
  )
}

// ── Employee: личный виджет ───────────────────────────────────────────────────

function EmployeeView({ data }: { data: DashboardData }) {
  const h = data.hours
  return (
    <div className="space-y-8">
      <Section title="Мои часы">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard label="Отработано" value={`${formatHours(h.total_hours)} ч`} />
          <KpiCard label="Норма" value={h.norm_hours !== null ? `${formatHours(h.norm_hours)} ч` : '—'} />
          <KpiCard label="Переработка" value={`${formatHours(h.overtime_hours)} ч`}
                   accent={num(h.overtime_hours) > 0 ? 'text-amber-600' : undefined} />
          <KpiCard label="Выполнение нормы" value={h.percent_of_norm !== null ? `${h.percent_of_norm}%` : '—'} />
        </div>
      </Section>
      <TrendBlock data={data} />
    </div>
  )
}
