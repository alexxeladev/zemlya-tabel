import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import type {
  AuditLogEntry,
  AutofillPreview,
  Company,
  Department,
  Employee,
  EmployeePayroll,
  MonthSummary,
  PayrollSummary,
  TimesheetEntry,
  TimesheetMonthResponse,
  TimesheetPeriod,
} from '../types/api'
import { timesheetApi } from '../api/timesheet'
import { apiClient, ApiError } from '../api/client'
import { formatDelta, formatHours, formatMoney } from '../utils/money'

// ── Constants ─────────────────────────────────────────────────────────────────

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

const STATUS_LABELS: Record<string, string> = {
  draft: 'Черновик',
  pending_review: 'На проверке',
  closed: 'Закрыт',
}

const STATUS_BADGE: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700',
  pending_review: 'bg-yellow-100 text-yellow-800',
  closed: 'bg-green-100 text-green-800',
}

const ACTION_LABELS: Record<string, string> = {
  period_submitted: 'отправил на проверку',
  period_returned: 'вернул на доработку',
  period_closed: 'закрыл период',
  period_reopened: 'переоткрыл период',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

type EntryMap = Map<string, number>

function makeCellKey(employeeId: number, workDate: string, companyId: number) {
  return `${employeeId}_${workDate}_${companyId}`
}

function buildEntryMap(entries: TimesheetEntry[]): EntryMap {
  const m = new Map<string, number>()
  for (const e of entries) {
    m.set(makeCellKey(e.employee_id, e.work_date, e.company_id), parseFloat(e.hours as unknown as string))
  }
  return m
}

function padDate(n: number) { return String(n).padStart(2, '0') }
function toWorkDate(year: number, month: number, day: number) {
  return `${year}-${padDate(month)}-${padDate(day)}`
}

function formatDateTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getDate()} ${MONTH_NAMES[d.getMonth()].toLowerCase()} ${d.getFullYear()}, ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
}

// ── ReasonModal ───────────────────────────────────────────────────────────────

interface ReasonModalProps {
  title: string
  onConfirm: (reason: string) => Promise<void>
  onClose: () => void
}

function ReasonModal({ title, onConfirm, onClose }: ReasonModalProps) {
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (reason.trim().length < 3) return
    setLoading(true)
    try {
      await onConfirm(reason.trim())
      onClose()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">{title}</h2>
        <form onSubmit={handleSubmit}>
          <textarea
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Укажите причину (минимум 3 символа)"
            className="w-full rounded-lg border border-gray-300 p-3 text-sm outline-none focus:ring-2 focus:ring-blue-400 resize-none"
            rows={4}
            maxLength={500}
          />
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
            >
              Отмена
            </button>
            <button
              type="submit"
              disabled={reason.trim().length < 3 || loading}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Подтвердить
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── PeriodHistory ─────────────────────────────────────────────────────────────

function PeriodHistory({ periodId }: { periodId: number }) {
  const [open, setOpen] = useState(false)
  const [history, setHistory] = useState<AuditLogEntry[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    if (history.length > 0) return
    setLoading(true)
    try {
      setHistory(await timesheetApi.getPeriodHistory(periodId))
    } catch {
      toast.error('Не удалось загрузить историю')
    } finally {
      setLoading(false)
    }
  }

  const toggle = () => {
    if (!open) load()
    setOpen((v) => !v)
  }

  return (
    <div className="mt-2 border-t border-gray-100 pt-2">
      <button onClick={toggle} className="text-xs text-blue-600 hover:underline">
        {open ? 'Скрыть историю' : 'История'}
      </button>
      {open && (
        <div className="mt-2 space-y-1">
          {loading && <p className="text-xs text-gray-400">Загрузка...</p>}
          {!loading && history.length === 0 && (
            <p className="text-xs text-gray-400">История пуста</p>
          )}
          {history.map((h) => (
            <div key={h.id} className="text-xs text-gray-600">
              <span className="text-gray-400">{formatDateTime(h.created_at)}</span>
              {' — '}
              <span className="font-medium">{h.actor_name ?? `#${h.actor_id}`}</span>
              {' '}
              {ACTION_LABELS[h.action] ?? h.action}
              {h.reason ? <span className="italic text-gray-500">: «{h.reason}»</span> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── PeriodCard ────────────────────────────────────────────────────────────────

interface PeriodCardProps {
  period: TimesheetPeriod
  onAction: (period: TimesheetPeriod) => void
  onFilterDept?: (deptId: number | null) => void
  showFilterBtn?: boolean
}

function PeriodCard({ period, onAction, onFilterDept, showFilterBtn }: PeriodCardProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-gray-900 text-sm truncate">
              {period.department_name ?? 'Без отдела'}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[period.status]}`}>
              {STATUS_LABELS[period.status]}
            </span>
          </div>
          {period.submitted_by_name && (
            <p className="mt-1 text-xs text-gray-500">
              Отправлено {formatDateTime(period.submitted_at)} — {period.submitted_by_name}
            </p>
          )}
          {period.closed_by_name && (
            <p className="text-xs text-gray-500">
              Закрыто {formatDateTime(period.closed_at)} — {period.closed_by_name}
            </p>
          )}
          <PeriodHistory periodId={period.id} />
        </div>

        <div className="flex flex-col gap-1 shrink-0">
          {showFilterBtn && onFilterDept && (
            <button
              onClick={() => onFilterDept(period.department_id)}
              className="rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50"
            >
              Только этот
            </button>
          )}
          {period.can_submit && (
            <button
              onClick={() => onAction(period)}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
              data-action="submit"
            >
              Отправить на проверку
            </button>
          )}
          {period.can_close && (
            <button
              onClick={() => onAction(period)}
              className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700"
              data-action="close"
            >
              {period.department_id === null ? 'Закрыть' : 'Утвердить'}
            </button>
          )}
          {period.can_return && (
            <button
              onClick={() => onAction(period)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50"
              data-action="return"
            >
              Вернуть на доработку
            </button>
          )}
          {period.can_reopen && (
            <button
              onClick={() => onAction(period)}
              className="rounded-lg border border-orange-300 px-3 py-1.5 text-xs text-orange-700 hover:bg-orange-50"
              data-action="reopen"
            >
              Переоткрыть для правок
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── PeriodPanel ───────────────────────────────────────────────────────────────

interface PeriodPanelProps {
  periods: TimesheetPeriod[]
  onPeriodsChange: (updated: TimesheetPeriod[]) => void
  onFilterDept: (deptId: number | null) => void
}

function PeriodPanel({ periods, onPeriodsChange, onFilterDept }: PeriodPanelProps) {
  const [modal, setModal] = useState<{
    period: TimesheetPeriod
    action: 'submit' | 'close' | 'return' | 'reopen'
  } | null>(null)
  const [collapsed, setCollapsed] = useState(periods.length > 3)

  const handleCardAction = (period: TimesheetPeriod) => {
    if (period.can_submit) setModal({ period, action: 'submit' })
    else if (period.can_close) setModal({ period, action: 'close' })
    else if (period.can_return) setModal({ period, action: 'return' })
    else if (period.can_reopen) setModal({ period, action: 'reopen' })
  }

  const handleConfirm = async (reason?: string) => {
    if (!modal) return
    const { period, action } = modal
    let updated: TimesheetPeriod
    try {
      if (action === 'submit') {
        updated = await timesheetApi.submitPeriod(period.id)
        toast.success('Отправлено на проверку')
      } else if (action === 'close') {
        updated = await timesheetApi.closePeriod(period.id)
        toast.success('Период закрыт')
      } else if (action === 'return') {
        updated = await timesheetApi.returnPeriod(period.id, reason!)
        toast.success('Возвращено на доработку')
      } else {
        updated = await timesheetApi.reopenPeriod(period.id, reason!)
        toast.success('Период переоткрыт')
      }
      onPeriodsChange(periods.map((p) => (p.id === updated.id ? updated : p)))
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Ошибка'
      toast.error(msg)
      throw err
    }
  }

  if (periods.length === 0) return null

  const visiblePeriods = collapsed ? periods.slice(0, 2) : periods
  const showMultiple = periods.length > 1

  return (
    <>
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
            Статусы периодов
          </span>
          {periods.length > 3 && (
            <button
              onClick={() => setCollapsed((v) => !v)}
              className="text-xs text-blue-600 hover:underline"
            >
              {collapsed ? `Показать все (${periods.length})` : 'Свернуть'}
            </button>
          )}
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {visiblePeriods.map((period) => (
            <PeriodCard
              key={period.id}
              period={period}
              onAction={handleCardAction}
              onFilterDept={onFilterDept}
              showFilterBtn={showMultiple}
            />
          ))}
        </div>
      </div>

      {modal && (modal.action === 'return' || modal.action === 'reopen') && (
        <ReasonModal
          title={modal.action === 'return' ? 'Причина возврата на доработку' : 'Причина переоткрытия'}
          onConfirm={(reason) => handleConfirm(reason)}
          onClose={() => setModal(null)}
        />
      )}
      {modal && (modal.action === 'submit' || modal.action === 'close') && (
        (() => {
          handleConfirm()
          setModal(null)
          return null
        })()
      )}
    </>
  )
}

// ── AutofillModal ─────────────────────────────────────────────────────────────

interface AutofillModalProps {
  preview: AutofillPreview
  onApply: () => Promise<void>
  onClose: () => void
}

function AutofillModal({ preview, onApply, onClose }: AutofillModalProps) {
  const [loading, setLoading] = useState(false)
  const [showSkipped, setShowSkipped] = useState(false)

  // Group entries by employee for the summary table
  const byEmployee = new Map<number, { company_id: number; count: number; hours: number }>()
  for (const e of preview.entries_to_create) {
    const key = e.employee_id
    const existing = byEmployee.get(key)
    const hours = parseFloat(e.hours as unknown as string)
    if (existing) {
      existing.count += 1
      existing.hours += hours
    } else {
      byEmployee.set(key, { company_id: e.company_id, count: 1, hours })
    }
  }

  const handleApply = async () => {
    setLoading(true)
    try {
      await onApply()
      onClose()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white shadow-xl flex flex-col max-h-[85vh]">
        <div className="p-6 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-900">Заполнить по графику</h2>
          <p className="mt-1 text-sm text-gray-500">
            Будет создано <strong>{preview.entries_to_create.length}</strong> записей для{' '}
            <strong>{preview.employees_processed}</strong> сотрудников
            {preview.cells_skipped > 0 && ` (${preview.cells_skipped} ячеек оставлено как есть)`}
          </p>
        </div>

        <div className="overflow-y-auto flex-1 p-6">
          {byEmployee.size > 0 && (
            <table className="w-full text-sm mb-4">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
                  <th className="pb-2 font-medium">ID</th>
                  <th className="pb-2 font-medium">Дней</th>
                  <th className="pb-2 font-medium">Часов</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(byEmployee.entries()).map(([empId, info]) => (
                  <tr key={empId} className="border-b border-gray-50">
                    <td className="py-1 text-gray-700">#{empId}</td>
                    <td className="py-1 text-gray-700">{info.count}</td>
                    <td className="py-1 text-gray-700">{info.hours}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {preview.employees_skipped.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <button
                onClick={() => setShowSkipped((v) => !v)}
                className="text-sm font-medium text-amber-800 hover:underline"
              >
                {preview.employees_skipped.length} сотрудников пропущено{' '}
                {showSkipped ? '▲' : '▼'}
              </button>
              {showSkipped && (
                <ul className="mt-2 space-y-1">
                  {preview.employees_skipped.map((s) => (
                    <li key={s.employee_id} className="text-xs text-amber-700">
                      #{s.employee_id} {s.employee_name} — {s.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {preview.entries_to_create.length === 0 && preview.employees_skipped.length === 0 && (
            <p className="text-sm text-gray-500">Нечего заполнять</p>
          )}
        </div>

        <div className="p-6 border-t border-gray-100 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={handleApply}
            disabled={loading || preview.entries_to_create.length === 0}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? 'Применяется...' : 'Применить'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── SavingIndicator ───────────────────────────────────────────────────────────

function SavingIndicator({ savingCount }: { savingCount: number }) {
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

// ── CellInput ─────────────────────────────────────────────────────────────────

interface CellInputProps {
  value: number | undefined
  onSave: (v: number) => Promise<void>
  locked: boolean
  dayBgClass: string
}

function CellInput({ value, onSave, locked, dayBgClass }: CellInputProps) {
  const [localVal, setLocalVal] = useState(value !== undefined ? String(value) : '')
  const prevRef = useRef(value !== undefined ? String(value) : '')

  useEffect(() => {
    const s = value !== undefined ? String(value) : ''
    setLocalVal(s)
    prevRef.current = s
  }, [value])

  const handleBlur = async () => {
    if (locked) return
    const num = localVal === '' ? 0 : parseFloat(localVal)
    if (isNaN(num)) { setLocalVal(prevRef.current); return }
    if (String(num) === prevRef.current || (num === 0 && prevRef.current === '')) return
    await onSave(num)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setLocalVal(prevRef.current)
      e.currentTarget.blur()
    } else if (e.key === 'Enter') {
      e.currentTarget.blur()
      const all = Array.from(document.querySelectorAll<HTMLInputElement>('input[data-tabelcell]'))
      const idx = all.indexOf(e.currentTarget)
      if (idx >= 0 && idx + 1 < all.length) all[idx + 1].focus()
    }
  }

  if (locked) {
    return (
      <div
        className={`w-full h-full min-h-[2rem] px-1 flex items-center justify-center text-center text-sm cursor-not-allowed select-none bg-gray-50 text-gray-400 ${dayBgClass}`}
        title="Период закрыт для редактирования"
      >
        {value !== undefined && value > 0 ? value : ''}
      </div>
    )
  }

  return (
    <input
      data-tabelcell
      type="number"
      min={0}
      max={24}
      step={0.5}
      value={localVal}
      onChange={(e) => setLocalVal(e.target.value)}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      className={`w-full h-full min-h-[2rem] px-1 text-center text-sm outline-none focus:ring-1 focus:ring-blue-400 focus:rounded ${dayBgClass}`}
    />
  )
}

// ── TimesheetHeader ───────────────────────────────────────────────────────────

interface TimesheetHeaderProps {
  year: number
  month: number
  onPrev: () => void
  onNext: () => void
  canFilterDept: boolean
  departments: Department[]
  departmentId: number | undefined
  onDeptChange: (id: number | undefined) => void
  hasDraftPeriod: boolean
  onAutofill: () => void
  autofillLoading: boolean
}

function TimesheetHeader({
  year, month, onPrev, onNext,
  canFilterDept, departments, departmentId, onDeptChange,
  hasDraftPeriod, onAutofill, autofillLoading,
}: TimesheetHeaderProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-3 shadow-sm">
      <h1 className="text-lg font-bold text-gray-900">Табель</h1>
      <div className="flex items-center gap-2">
        <button onClick={onPrev} className="rounded-md p-1 text-gray-500 hover:bg-gray-100">←</button>
        <span className="min-w-[120px] text-center text-sm font-medium text-gray-700">
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button onClick={onNext} className="rounded-md p-1 text-gray-500 hover:bg-gray-100">→</button>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
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
        <button
          onClick={onAutofill}
          disabled={!hasDraftPeriod || autofillLoading}
          title={!hasDraftPeriod ? 'Сначала откройте период для редактирования' : 'Заполнить по графику'}
          className="rounded-lg border border-blue-300 px-3 py-1.5 text-sm text-blue-700 hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {autofillLoading ? '...' : 'Заполнить по графику'}
        </button>
      </div>
    </div>
  )
}

// ── CompanyDropdown ───────────────────────────────────────────────────────────

interface CompanyDropdownProps {
  companies: Company[]
  shownCompanyIds: number[]
  onSelect: (companyId: number) => void
}

function CompanyDropdown({ companies, shownCompanyIds, onSelect }: CompanyDropdownProps) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ top: 0, left: 0 })
  const btnRef = useRef<HTMLButtonElement>(null)
  const available = companies.filter((c) => !shownCompanyIds.includes(c.id))

  if (available.length === 0) return null

  const handleOpen = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect()
      setPos({ top: r.bottom + 4, left: r.left })
    }
    setOpen((v) => !v)
  }

  return (
    <div className="relative inline-block">
      <button
        ref={btnRef}
        onClick={handleOpen}
        className="rounded text-[10px] px-1.5 py-0.5 border border-blue-200 text-blue-600 hover:bg-blue-50 whitespace-nowrap"
      >
        + компания
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="fixed z-50 min-w-[160px] max-h-44 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg py-1"
            style={{ top: pos.top, left: pos.left }}
          >
            {available.map((c) => (
              <button
                key={c.id}
                onClick={() => { onSelect(c.id); setOpen(false) }}
                className="w-full px-3 py-1.5 text-left text-sm hover:bg-gray-50 text-gray-700 whitespace-nowrap"
              >
                {c.code} — {c.name}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

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
  const [periods, setPeriods] = useState<TimesheetPeriod[]>([])
  const [savingCount, setSavingCount] = useState(0)
  const [loading, setLoading] = useState(true)

  // expandedCompanies: for each employee id, a Set of extra company ids to show
  const [expandedCompanies, setExpandedCompanies] = useState<Record<number, Set<number>>>({})

  // Autofill state
  const [autofillPreview, setAutofillPreview] = useState<AutofillPreview | null>(null)
  const [autofillLoading, setAutofillLoading] = useState(false)

  const [payroll, setPayroll] = useState<PayrollSummary | null>(null)

  const canFilterDept = user?.role === 'admin' || user?.role === 'accountant'
  const canAutofill = user?.role === 'admin' || user?.role === 'accountant' || user?.role === 'manager'
  const canSeeFinance = user?.role === 'admin' || user?.role === 'accountant'

  useEffect(() => {
    if (!canFilterDept) return
    apiClient.get<Department[]>('/api/departments').then((r) => setDepartments(r.data)).catch(() => {})
  }, [canFilterDept])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [monthData, calData] = await Promise.all([
        timesheetApi.getMonth(year, month, {
          department_id: canFilterDept ? departmentId : undefined,
          include_payroll: canSeeFinance,
        }),
        apiClient.get<MonthSummary>(`/api/calendar/${year}/${month}/summary`).then((r) => r.data).catch(() => null),
      ])
      setData(monthData)
      setCalendar(calData)
      setEntryMap(buildEntryMap(monthData.entries))
      setPeriods(monthData.periods)
      setPayroll(monthData.payroll ?? null)

      // Initialize expandedCompanies from server extra_companies_by_employee
      const extras = monthData.extra_companies_by_employee
      setExpandedCompanies((prev) => {
        const next: Record<number, Set<number>> = {}
        for (const emp of monthData.employees) {
          const serverExtras = extras[String(emp.id)] ?? []
          const prevExtras = prev[emp.id] ?? new Set<number>()
          next[emp.id] = new Set([...serverExtras, ...prevExtras])
        }
        return next
      })
    } catch {
      toast.error('Не удалось загрузить табель')
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month, departmentId, canFilterDept, canSeeFinance])

  useEffect(() => { loadData() }, [loadData])

  // Build map: department_id (as string key) → period
  const periodByDeptId = new Map<string, TimesheetPeriod>()
  for (const p of periods) {
    periodByDeptId.set(p.department_id === null ? 'null' : String(p.department_id), p)
  }

  const hasDraftPeriod = periods.some((p) => p.status === 'draft')

  const isRowLocked = (deptId: number | null): boolean => {
    const key = deptId === null ? 'null' : String(deptId)
    const period = periodByDeptId.get(key)
    if (!period) return false
    return period.status !== 'draft'
  }

  const isDraft = (deptId: number | null): boolean => {
    const key = deptId === null ? 'null' : String(deptId)
    const period = periodByDeptId.get(key)
    return period?.status === 'draft'
  }

  const handleSaveCell = useCallback(async (
    employeeId: number, workDate: string, companyId: number, hours: number, deptId: number | null
  ) => {
    if (isRowLocked(deptId)) {
      toast.error('Период закрыт, обновите страницу')
      return
    }

    const key = makeCellKey(employeeId, workDate, companyId)
    const prevVal = entryMap.get(key)

    setEntryMap((prev) => {
      const next = new Map(prev)
      if (hours === 0) next.delete(key)
      else next.set(key, hours)
      return next
    })

    setSavingCount((n) => n + 1)
    try {
      await timesheetApi.saveCell({ employee_id: employeeId, work_date: workDate, company_id: companyId, hours })
      // Refresh payroll after cell save
      if (canSeeFinance) {
        timesheetApi.getPayroll(year, month, canFilterDept ? departmentId : undefined)
          .then(setPayroll)
          .catch(() => {})
      }
    } catch (err) {
      setEntryMap((prev) => {
        const next = new Map(prev)
        if (prevVal !== undefined) next.set(key, prevVal)
        else next.delete(key)
        return next
      })
      const msg = err instanceof ApiError
        ? (err.status === 409 ? 'Период закрыт, обновите страницу' : err.message)
        : 'Ошибка сохранения'
      toast.error(msg)
    } finally {
      setSavingCount((n) => n - 1)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryMap])

  const handleAutofill = async () => {
    if (!canAutofill) return
    setAutofillLoading(true)
    try {
      const preview = await timesheetApi.autofillPreview(year, month, canFilterDept ? departmentId : undefined)
      setAutofillPreview(preview)
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Ошибка загрузки preview'
      toast.error(msg)
    } finally {
      setAutofillLoading(false)
    }
  }

  const handleAutofillApply = async () => {
    await timesheetApi.autofillApply(year, month, canFilterDept ? departmentId : undefined)
    toast.success('Табель заполнен по графику')
    await loadData()
  }

  const addCompanyForEmployee = (empId: number, companyId: number) => {
    setExpandedCompanies((prev) => ({
      ...prev,
      [empId]: new Set([...(prev[empId] ?? []), companyId]),
    }))
  }

  const removeCompanyForEmployee = (empId: number, companyId: number) => {
    setExpandedCompanies((prev) => {
      const set = new Set(prev[empId] ?? [])
      set.delete(companyId)
      return { ...prev, [empId]: set }
    })
  }

  const employeeHasHoursForCompany = (empId: number, companyId: number): boolean => {
    for (const [key] of entryMap.entries()) {
      if (key.startsWith(`${empId}_`) && key.endsWith(`_${companyId}`)) return true
    }
    return false
  }

  const prevMonth = () => {
    if (month === 1) { setYear((y) => y - 1); setMonth(12) }
    else setMonth((m) => m - 1)
  }
  const nextMonth = () => {
    if (month === 12) { setYear((y) => y + 1); setMonth(1) }
    else setMonth((m) => m + 1)
  }

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-gray-400">Загрузка...</div>
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
          hasDraftPeriod={hasDraftPeriod} onAutofill={handleAutofill} autofillLoading={autofillLoading}
        />
        <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-gray-500">
          Нет сотрудников в отделе
        </div>
      </div>
    )
  }

  const daysInMonth = new Date(year, month, 0).getDate()
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1)

  const getDayType = (day: number) => {
    if (!calendar) return 'work'
    return calendar.days.find((d) => d.day === day)?.type ?? 'work'
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

  const employeeTotal = (empId: number) => {
    let sum = 0
    for (const [key, hours] of entryMap.entries()) {
      if (key.startsWith(`${empId}_`)) sum += hours
    }
    return sum
  }

  const payrollByEmployee = (empId: number): EmployeePayroll | null =>
    payroll?.employees.find((e) => e.employee_id === empId) ?? null

  const totalHoursAll = (): number => {
    let s = 0
    for (const v of entryMap.values()) s += v
    return s
  }

  const totalNormAll = (): number | null => {
    if (!payroll) return null
    let s = 0
    for (const ep of payroll.employees) {
      if (ep.norm_hours !== null) s += parseFloat(ep.norm_hours)
    }
    return s
  }

  const weekdayOf = (day: number) => WEEKDAY_SHORT[new Date(year, month - 1, day).getDay()]

  const rowBorderCls = (isLastRow: boolean, isLastEmp: boolean) =>
    isLastRow && !isLastEmp ? 'border-b-2 border-gray-300' : 'border-b border-gray-100'

  // Build rows per employee: default company + expanded extras
  const buildEmployeeRows = (emp: Employee): number[] => {
    const rows: number[] = []
    if (emp.default_company_id !== null) {
      rows.push(emp.default_company_id)
    }
    const extras = expandedCompanies[emp.id] ?? new Set<number>()
    for (const cid of Array.from(extras).sort((a, b) => a - b)) {
      if (cid !== emp.default_company_id) {
        rows.push(cid)
      }
    }
    return rows
  }

  // Flat list of (emp, companyId, cIdx) rows for synchronized rendering across three tables
  const tableRows = employees.flatMap((emp, empIdx) => {
    const isLastEmp = empIdx === employees.length - 1
    const empRows = buildEmployeeRows(emp)
    if (empRows.length === 0) {
      return [{ key: `${emp.id}_empty`, emp, cIdx: 0, totalRows: 1, companyId: null as number | null, isLastEmp }]
    }
    return empRows.map((cid, cIdx) => ({
      key: `${emp.id}_${cid}`,
      emp, cIdx, totalRows: empRows.length, companyId: cid as number | null, isLastEmp,
    }))
  })

  return (
    <div className="space-y-4">
      <TimesheetHeader
        year={year} month={month} onPrev={prevMonth} onNext={nextMonth}
        canFilterDept={canFilterDept} departments={departments}
        departmentId={departmentId} onDeptChange={setDepartmentId}
        hasDraftPeriod={hasDraftPeriod} onAutofill={handleAutofill} autofillLoading={autofillLoading}
      />

      {periods.length > 0 && (
        <PeriodPanel
          periods={periods}
          onPeriodsChange={setPeriods}
          onFilterDept={(id) => setDepartmentId(id === null ? undefined : id)}
        />
      )}

      {/* ── Three-section table: Left fixed | Center scrollable | Right fixed ── */}
      <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
       <div className="flex">

        {/* ── LEFT PANEL: Employee + Company (fixed 280px) ── */}
        <div className="shrink-0 border-r border-gray-200" style={{ width: '280px' }}>
          <table className="w-full border-collapse text-xs table-fixed">
            <colgroup><col style={{ width: '200px' }} /><col style={{ width: '80px' }} /></colgroup>
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200" style={{ height: '48px' }}>
                <th className="px-3 py-2 text-left font-medium text-gray-500 border-r border-gray-200">Сотрудник</th>
                <th className="px-2 py-2 text-left font-medium text-gray-500">Компания</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map(({ key, emp, cIdx, totalRows, companyId, isLastEmp }) => {
                const isFirstRow = cIdx === 0
                const isLastRow = cIdx === totalRows - 1
                const isDismissed = !emp.is_active
                const locked = isRowLocked(emp.department_id)
                const draft = isDraft(emp.department_id)
                const company = companyId !== null ? companies.find(c => c.id === companyId) : null
                const cc = companyId !== null ? companyColor(companyId, companies) : COMPANY_COLORS[0]
                const hasHours = companyId !== null ? employeeHasHoursForCompany(emp.id, companyId) : false
                const isDefault = companyId === emp.default_company_id
                const canRemove = !isDefault && !hasHours && draft && companyId !== null
                return (
                  <tr key={`${key}_L`} className={rowBorderCls(isLastRow, isLastEmp)} style={{ height: '48px' }}>
                    <td className="px-2 py-1 border-r border-gray-200 overflow-hidden" style={{ maxWidth: '200px' }}>
                      {isFirstRow && (
                        <div className="flex flex-col gap-0.5 h-full justify-center">
                          <div className="flex items-center gap-1 min-w-0">
                            <span className="font-medium text-gray-800 truncate text-xs leading-tight" title={emp.full_name}>{emp.full_name}</span>
                            {isDismissed && <span className="text-[10px] text-gray-400 shrink-0">(ув.)</span>}
                            {locked && <span className={`text-[10px] rounded px-1 shrink-0 ${STATUS_BADGE[periodByDeptId.get(emp.department_id === null ? 'null' : String(emp.department_id))?.status ?? 'draft']}`}>🔒</span>}
                          </div>
                          {draft && (
                            <CompanyDropdown
                              companies={companies}
                              shownCompanyIds={buildEmployeeRows(emp)}
                              onSelect={(cid) => addCompanyForEmployee(emp.id, cid)}
                            />
                          )}
                        </div>
                      )}
                    </td>
                    <td className={`px-2 py-1 text-xs overflow-hidden ${cc.bg} ${cc.text}`}>
                      {companyId !== null ? (
                        <div className="flex items-center gap-1 h-full">
                          {canRemove && (
                            <button onClick={() => removeCompanyForEmployee(emp.id, companyId)} className="text-gray-400 hover:text-red-500 text-[10px] leading-none shrink-0" title="Убрать компанию">×</button>
                          )}
                          <span>{company?.code ?? companyId}</span>
                        </div>
                      ) : <span className="text-gray-400 italic">не выбрана</span>}
                    </td>
                  </tr>
                )
              })}
              {tableRows.length > 0 && (
                <tr className="bg-gray-50 border-t-2 border-gray-300 font-semibold text-xs" style={{ height: '40px' }}>
                  <td className="px-3 py-2 text-gray-700 border-r border-gray-200 truncate">ИТОГО: {employees.length} сотр.</td>
                  <td className="px-2 py-2" />
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ── CENTER: Day cells (scrollable) ── */}
        <div className="flex-1 overflow-x-auto min-w-0">
          <table className="border-collapse text-xs" style={{ width: `${days.length * 42}px`, minWidth: `${days.length * 42}px` }}>
            <thead>
              <tr className="bg-white border-b border-gray-200" style={{ height: '48px' }}>
                {days.map((d) => (
                  <th key={d} style={{ width: '42px', minWidth: '42px' }} className={`border-r border-gray-200 px-1 py-1 text-center font-medium ${dayHeaderClass(d)}`}>
                    <div className="text-xs">{d}</div>
                    <div className="text-[10px] font-normal opacity-70">{weekdayOf(d)}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map(({ key, emp, cIdx, totalRows, companyId, isLastEmp }) => {
                const isLastRow = cIdx === totalRows - 1
                const locked = isRowLocked(emp.department_id)
                const cc = companyId !== null ? companyColor(companyId, companies) : COMPANY_COLORS[0]
                return (
                  <tr key={`${key}_C`} className={rowBorderCls(isLastRow, isLastEmp)} style={{ height: '48px' }}>
                    {days.map((d) => {
                      if (companyId === null) return <td key={d} className="border-r border-gray-100" style={{ width: '42px' }} />
                      const workDate = toWorkDate(year, month, d)
                      const cellKey = makeCellKey(emp.id, workDate, companyId)
                      const val = entryMap.get(cellKey)
                      return (
                        <td key={d} className={`border-r border-gray-100 p-0 ${dayCellBg(d)} ${cc.cell}`} style={{ width: '42px' }}>
                          <CellInput value={val} locked={locked} dayBgClass={dayCellBg(d)}
                            onSave={(hours) => handleSaveCell(emp.id, workDate, companyId, hours, emp.department_id)} />
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
              {tableRows.length > 0 && (
                <tr className="bg-gray-50 border-t-2 border-gray-300" style={{ height: '40px' }}>
                  {days.map((d) => <td key={d} className="border-r border-gray-100" style={{ width: '42px' }} />)}
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ── RIGHT PANEL: Totals + Finance (fixed) ── */}
        <div className="shrink-0 border-l border-gray-200">
          <table className="border-collapse text-xs">
            <colgroup>
              <col style={{ width: '52px' }} />
              {canSeeFinance && <><col style={{ width: '48px' }} /><col style={{ width: '40px' }} /><col style={{ width: '68px' }} /><col style={{ width: '64px' }} /><col style={{ width: '56px' }} /><col style={{ width: '76px' }} /></>}
            </colgroup>
            <thead>
              <tr className="bg-white border-b border-gray-200" style={{ height: '48px' }}>
                <th className="px-2 py-2 text-center font-medium text-gray-500 border-l border-gray-200 text-[11px]">Итого ч</th>
                {canSeeFinance && (
                  <>
                    <th className="px-1 py-2 text-center font-medium text-gray-400 border-l border-gray-200 text-[11px]">Норма</th>
                    <th className="px-1 py-2 text-center font-medium text-gray-400 border-l border-gray-200 text-[11px]">Δ</th>
                    <th className="px-1 py-2 text-center font-medium text-gray-400 border-l border-gray-200 text-[11px]">Оклад</th>
                    <th className="px-1 py-2 text-center font-medium text-gray-400 border-l border-gray-200 text-[11px]">Сверхур.</th>
                    <th className="px-1 py-2 text-center font-medium text-gray-400 border-l border-gray-200 text-[11px]">Праздн.</th>
                    <th className="px-1 py-2 text-center font-semibold text-blue-700 bg-blue-50 border-l border-blue-200 text-[11px]">Итого ₽</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {tableRows.map(({ key, emp, cIdx, totalRows, isLastEmp }) => {
                const isFirstRow = cIdx === 0
                const isLastRow = cIdx === totalRows - 1
                const total = employeeTotal(emp.id)
                const ep = payrollByEmployee(emp.id)
                const delta = ep ? formatDelta(ep.delta_hours) : null
                const notCalcTitle = ep?.reason_if_not_calculable ?? ''
                return (
                  <tr key={`${key}_R`} className={rowBorderCls(isLastRow, isLastEmp)} style={{ height: '48px' }}>
                    <td className="px-2 py-1 text-center font-semibold text-xs border-l border-gray-200 text-gray-800">
                      {isFirstRow && total > 0 ? total : null}
                    </td>
                    {canSeeFinance && (
                      <>
                        <td className="px-1 py-1 text-center text-[11px] border-l border-gray-200 text-gray-600 whitespace-nowrap">
                          {isFirstRow ? (ep ? formatHours(ep.norm_hours) : '—') : null}
                        </td>
                        <td className={`px-1 py-1 text-center text-[11px] border-l border-gray-200 whitespace-nowrap ${isFirstRow && delta ? delta.className : ''}`}>
                          {isFirstRow && delta ? delta.text : null}
                        </td>
                        <td className="px-1 py-1 text-center text-[11px] border-l border-gray-200 text-gray-700 whitespace-nowrap" title={isFirstRow ? notCalcTitle : ''}>
                          {isFirstRow ? (ep?.is_calculable ? formatMoney(ep.base_amount) : <span className="text-gray-400 italic">—</span>) : null}
                        </td>
                        <td className="px-1 py-1 text-center text-[11px] border-l border-gray-200 text-gray-700 whitespace-nowrap" title={isFirstRow ? notCalcTitle : ''}>
                          {isFirstRow ? (ep?.is_calculable ? formatMoney(ep.overtime_amount) : <span className="text-gray-400 italic">—</span>) : null}
                        </td>
                        <td className="px-1 py-1 text-center text-[11px] border-l border-gray-200 text-gray-700 whitespace-nowrap" title={isFirstRow ? notCalcTitle : ''}>
                          {isFirstRow ? (ep?.is_calculable ? formatMoney(ep.holiday_amount) : <span className="text-gray-400 italic">—</span>) : null}
                        </td>
                        <td className="px-1 py-1 text-center text-[11px] border-l border-blue-200 bg-blue-50 font-semibold text-blue-700 whitespace-nowrap" title={isFirstRow ? notCalcTitle : ''}>
                          {isFirstRow ? (ep?.is_calculable ? formatMoney(ep.total_amount) : <span className="text-gray-400 italic font-normal">—</span>) : null}
                        </td>
                      </>
                    )}
                  </tr>
                )
              })}
              {/* Footer */}
              {tableRows.length > 0 && (
                <tr className="bg-gray-50 border-t-2 border-gray-300 font-semibold text-xs" style={{ height: '40px' }}>
                  <td className="px-2 py-2 text-center text-gray-800 border-l border-gray-200">{totalHoursAll() || '—'}</td>
                  {canSeeFinance && payroll ? (
                    <>
                      <td className="px-1 py-2 text-center text-gray-600 border-l border-gray-200 whitespace-nowrap">{totalNormAll() ?? '—'}</td>
                      <td className="px-1 py-2 text-center text-gray-500 border-l border-gray-200">—</td>
                      <td className="px-1 py-2 text-center text-gray-700 border-l border-gray-200 whitespace-nowrap">{formatMoney(payroll.total_base_amount)}</td>
                      <td className="px-1 py-2 text-center text-gray-700 border-l border-gray-200 whitespace-nowrap">{formatMoney(payroll.total_overtime_amount)}</td>
                      <td className="px-1 py-2 text-center text-gray-700 border-l border-gray-200 whitespace-nowrap">{formatMoney(payroll.total_holiday_amount)}</td>
                      <td className="px-1 py-2 text-center text-blue-800 font-bold border-l border-blue-200 bg-blue-100 whitespace-nowrap">{formatMoney(payroll.grand_total)}</td>
                    </>
                  ) : canSeeFinance ? (
                    <>
                      <td className="border-l border-gray-200 px-1 py-2" /><td className="border-l border-gray-200 px-1 py-2" />
                      <td className="border-l border-gray-200 px-1 py-2" /><td className="border-l border-gray-200 px-1 py-2" />
                      <td className="border-l border-gray-200 px-1 py-2" /><td className="border-l border-blue-200 bg-blue-50 px-1 py-2" />
                    </>
                  ) : null}
                </tr>
              )}
            </tbody>
          </table>
        </div>

       </div>{/* end flex */}
      </div>{/* end table wrapper */}

      <SavingIndicator savingCount={savingCount} />

      {autofillPreview && (
        <AutofillModal
          preview={autofillPreview}
          onApply={handleAutofillApply}
          onClose={() => setAutofillPreview(null)}
        />
      )}
    </div>
  )
}
