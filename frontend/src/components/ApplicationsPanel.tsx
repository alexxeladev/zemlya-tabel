import { useEffect, useMemo, useState } from 'react'
import { timesheetApi } from '../api/timesheet'
import { toast } from '../store/toasts'
import type { DepartmentApplications } from '../types/api'

/** Минимум, нужный блоку: табель держит свой облегчённый тип компании. */
type PanelCompany = { id: number; code: string; name: string; is_active?: boolean }
import { companyColorByIndex } from '../utils/colors'
import { distribute } from '../utils/distribution'
import { Button } from './Button'

type Props = {
  /** отделы с флагом «распределение по заявкам», попавшие в текущий табель */
  applications: DepartmentApplications[]
  companies: PanelCompany[]
  year: number
  month: number
  /** править заявки может тот же, кто правит распределение (финансовые роли) */
  canEdit: boolean
  /** перечитать месяц: изменившиеся заявки меняют суммы в ведомости */
  onSaved: () => void
}

const num = (v: string): number => {
  const n = Number(String(v).replace(',', '.'))
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

const fmtPercent = (v: number): string =>
  v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/**
 * Заявки на подбор по юрлицам за месяц (task_hr_applications).
 *
 * Отдел с флагом (у нас HR) делит зарплату ВСЕХ своих сотрудников по числу
 * заявок: процент компании = её заявки / сумма заявок. Проценты считаются здесь
 * тем же алгоритмом, что на бэке (`utils/distribution` — зеркало
 * `services/distribution.py`), поэтому цифра под инпутом совпадает с той, по
 * которой реально разнесётся зарплата, ещё до сохранения.
 *
 * Блок показывается только для отделов с флагом — у остальных заявок нет и
 * распределение идёт обычным каскадом.
 */
export function ApplicationsPanel({
  applications, companies, year, month, canEdit, onSaved,
}: Props) {
  if (applications.length === 0) return null
  return (
    <div className="flex-shrink-0 border-b border-gray-200 bg-emerald-50/40 px-6 py-3">
      <div className="flex flex-col gap-3">
        {applications.map((dept) => (
          <DepartmentRow
            key={dept.department_id}
            dept={dept}
            companies={companies}
            year={year}
            month={month}
            canEdit={canEdit}
            onSaved={onSaved}
          />
        ))}
      </div>
    </div>
  )
}

function DepartmentRow({
  dept, companies, year, month, canEdit, onSaved,
}: {
  dept: DepartmentApplications
  companies: PanelCompany[]
  year: number
  month: number
  canEdit: boolean
  onSaved: () => void
}) {
  const active = useMemo(() => companies.filter((c) => c.is_active !== false), [companies])

  // Ключ сбрасывает черновик, когда с сервера пришёл другой месяц или другой
  // набор: иначе введённые цифры «переехали» бы в чужой период.
  const serverKey = `${dept.department_id}:${year}-${month}:${dept.applications
    .map((a) => `${a.company_id}=${a.count}`)
    .join(',')}`
  const [counts, setCounts] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const next: Record<number, string> = {}
    for (const a of dept.applications) next[a.company_id] = String(a.count)
    setCounts(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey])

  const weights = useMemo(() => {
    const w: Record<number, number> = {}
    for (const c of active) {
      const n = num(counts[c.id] ?? '')
      if (n > 0) w[c.id] = n
    }
    return w
  }, [counts, active])

  const total = Object.values(weights).reduce((s, n) => s + n, 0)
  // Проценты — тем же методом наибольшего остатка, что и на бэке: сумма ровно 100.
  const percents = useMemo(() => distribute(100, weights, null, 0.01), [weights])

  const dirty = useMemo(() => {
    const saved: Record<number, number> = {}
    for (const a of dept.applications) saved[a.company_id] = a.count
    const ids = new Set([...Object.keys(saved), ...Object.keys(weights)].map(Number))
    for (const id of ids) if ((saved[id] ?? 0) !== (weights[id] ?? 0)) return true
    return false
  }, [dept.applications, weights])

  const save = async () => {
    setSaving(true)
    try {
      await timesheetApi.setApplications({
        department_id: dept.department_id,
        year,
        month,
        applications: active.map((c) => ({ company_id: c.id, count: weights[c.id] ?? 0 })),
      })
      toast.success('Заявки сохранены')
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail ?? 'Не удалось сохранить заявки')
    } finally {
      setSaving(false)
    }
  }

  const reset = () => {
    const next: Record<number, string> = {}
    for (const a of dept.applications) next[a.company_id] = String(a.count)
    setCounts(next)
  }

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
          📋 Заявки на подбор — {dept.department_name ?? 'отдел'}
        </span>
        <span className="text-xs text-gray-500">
          зарплата отдела делится по этим процентам (каскад не применяется)
        </span>
        <span className="ml-auto text-xs text-gray-600">
          Всего заявок: <span className="font-mono font-semibold">{total}</span>
        </span>
        {canEdit && (
          <>
            {dirty && (
              <Button size="sm" variant="ghost" onClick={reset} disabled={saving}>
                Отмена
              </Button>
            )}
            <Button size="sm" onClick={save} loading={saving} disabled={!dirty}>
              Сохранить
            </Button>
          </>
        )}
      </div>

      {total === 0 && (
        <p className="mb-1.5 text-xs text-amber-700">
          Заявки за месяц не заведены — пока распределение идёт по обычному каскаду
          (проценты сотрудника / дефолт отдела / часы).
        </p>
      )}

      <div className="flex flex-wrap gap-1.5">
        {active.map((c) => {
          const col = companyColorByIndex(companies.findIndex((x) => x.id === c.id))
          const percent = percents[c.id] ?? 0
          return (
            <div
              key={c.id}
              className="w-[104px] rounded-md border bg-white px-2 py-1"
              style={{ borderColor: `${col.color}40` }}
            >
              <div
                className="truncate font-mono text-[11px] font-semibold"
                style={{ color: col.color }}
                title={c.name}
              >
                {c.code}
              </div>
              <input
                className="w-full rounded border border-gray-200 px-1 py-0.5 text-right font-mono text-sm focus:border-emerald-400 focus:outline-none disabled:bg-gray-50 disabled:text-gray-500"
                value={counts[c.id] ?? ''}
                onChange={(e) =>
                  setCounts({ ...counts, [c.id]: e.target.value.replace(/[^\d]/g, '') })
                }
                placeholder="0"
                inputMode="numeric"
                disabled={!canEdit || saving}
              />
              <div className="mt-0.5 text-right font-mono text-[11px] text-gray-500">
                {percent > 0 ? `${fmtPercent(percent)} %` : '—'}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
