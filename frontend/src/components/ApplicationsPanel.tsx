import { useEffect, useMemo, useState } from 'react'
import { timesheetApi } from '../api/timesheet'
import { toast } from '../store/toasts'
import type { DepartmentApplications } from '../types/api'
import { companyColorByIndex } from '../utils/colors'
import { companyLabel } from '../utils/companies'
import { distribute } from '../utils/distribution'
import { Button } from './Button'

/** Минимум, нужный блоку: табель держит свой облегчённый тип компании. */
type PanelCompany = { id: number; code: string; name: string; is_active?: boolean }

type Props = {
  /** отделы с флагом «распределение по заявкам», попавшие в текущий табель */
  applications: DepartmentApplications[]
  companies: PanelCompany[]
  year: number
  month: number
  /** править заявки может тот же, кто правит распределение (финансовые роли) */
  canEdit: boolean
  /** суммы распределения по юрлицам за месяц, по отделам — итоговая строка блока */
  totalsByDepartment?: Map<number, { totals: Record<number, number>; grand: number }>
  /** перечитать месяц: изменившиеся заявки меняют суммы в табеле и ведомости */
  onSaved: () => void
}

/** Пара полей ввода на юрлицо: заявки в работе и закрытые. */
type Draft = Record<number, { in_progress: string; closed: string }>

const int = (v: string | undefined): number => {
  const n = Number(String(v ?? '').replace(/[^\d]/g, ''))
  return Number.isFinite(n) && n > 0 ? n : 0
}

const fmtInt = (v: number): string => v.toLocaleString('ru-RU')
const fmtPercent = (v: number): string =>
  v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/**
 * Заявки на подбор по юрлицам за месяц (task_hr_applications).
 *
 * Повторяет шапку исходного файла HR: строки «в работе» и «закрытые», под ними
 * «Заявок» (их сумма — она же база распределения), процент и распределённая
 * сумма по каждому юрлицу.
 *
 * Проценты считаются здесь тем же алгоритмом, что на бэке (`utils/distribution`
 * — зеркало `services/distribution.py`), поэтому цифра под инпутом совпадает с
 * той, по которой реально разнесётся зарплата, ещё до сохранения. Суммы, в
 * отличие от процентов, приходят с бэка: пересобирать «Итого начислено» из
 * кусков расчёта на фронте нельзя — разъедется с ведомостью.
 *
 * Блок показывается только для отделов с флагом.
 */
export function ApplicationsPanel({
  applications, companies, year, month, canEdit, totalsByDepartment, onSaved,
}: Props) {
  if (applications.length === 0) return null
  return (
    <div className="flex-shrink-0 border-b border-gray-200 bg-emerald-50/40 px-6 py-3">
      <div className="flex flex-col gap-4">
        {applications.map((dept) => (
          <DepartmentBlock
            key={dept.department_id}
            dept={dept}
            companies={companies}
            year={year}
            month={month}
            canEdit={canEdit}
            totals={totalsByDepartment?.get(dept.department_id)?.totals}
            grandTotal={totalsByDepartment?.get(dept.department_id)?.grand}
            onSaved={onSaved}
          />
        ))}
      </div>
    </div>
  )
}

function DepartmentBlock({
  dept, companies, year, month, canEdit, totals, grandTotal, onSaved,
}: {
  dept: DepartmentApplications
  companies: PanelCompany[]
  year: number
  month: number
  canEdit: boolean
  totals?: Record<number, number>
  grandTotal?: number
  onSaved: () => void
}) {
  const active = useMemo(() => companies.filter((c) => c.is_active !== false), [companies])

  // Ключ сбрасывает черновик, когда с сервера пришёл другой месяц или другой
  // набор: иначе введённые цифры «переехали» бы в чужой период.
  const serverKey = `${dept.department_id}:${year}-${month}:${dept.applications
    .map((a) => `${a.company_id}=${a.in_progress}/${a.closed}`)
    .join(',')}`
  const [draft, setDraft] = useState<Draft>({})
  const [saving, setSaving] = useState(false)

  const fromServer = (): Draft => {
    const next: Draft = {}
    for (const a of dept.applications) {
      next[a.company_id] = { in_progress: String(a.in_progress), closed: String(a.closed) }
    }
    return next
  }

  useEffect(() => {
    setDraft(fromServer())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey])

  const parts = (id: number) => ({
    work: int(draft[id]?.in_progress),
    closed: int(draft[id]?.closed),
  })
  const countOf = (id: number) => {
    const p = parts(id)
    return p.work + p.closed
  }

  const weights = useMemo(() => {
    const w: Record<number, number> = {}
    for (const c of active) {
      const n = countOf(c.id)
      if (n > 0) w[c.id] = n
    }
    return w
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, active])

  const totalWork = active.reduce((s, c) => s + parts(c.id).work, 0)
  const totalClosed = active.reduce((s, c) => s + parts(c.id).closed, 0)
  const totalCount = totalWork + totalClosed
  // Проценты — тем же методом наибольшего остатка, что и на бэке: сумма ровно 100.
  const percents = useMemo(() => distribute(100, weights, null, 0.01), [weights])

  const dirty = useMemo(() => {
    const saved = fromServer()
    const ids = new Set([...Object.keys(saved), ...Object.keys(draft)].map(Number))
    for (const id of ids) {
      if (int(saved[id]?.in_progress) !== int(draft[id]?.in_progress)) return true
      if (int(saved[id]?.closed) !== int(draft[id]?.closed)) return true
    }
    return false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, dept.applications])

  const setPart = (id: number, field: 'in_progress' | 'closed', value: string) => {
    setDraft({
      ...draft,
      [id]: {
        in_progress: draft[id]?.in_progress ?? '',
        closed: draft[id]?.closed ?? '',
        [field]: value.replace(/[^\d]/g, ''),
      },
    })
  }

  const save = async () => {
    setSaving(true)
    try {
      await timesheetApi.setApplications({
        department_id: dept.department_id,
        year,
        month,
        applications: active.map((c) => ({
          company_id: c.id,
          in_progress: parts(c.id).work,
          closed: parts(c.id).closed,
        })),
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

  const cellCls = 'border border-emerald-200 px-2 py-1 text-right font-mono'
  const labelCls = 'border border-emerald-200 px-2 py-1 text-left text-gray-600 whitespace-nowrap'

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-emerald-700">
          📋 Заявки на подбор — {dept.department_name ?? 'отдел'}
        </span>
        <span className="text-xs text-gray-500">
          зарплата отдела делится по этим процентам (обычный каскад не применяется)
        </span>
        {canEdit && (
          <span className="ml-auto flex items-center gap-2">
            {dirty && (
              <Button size="sm" variant="ghost" onClick={() => setDraft(fromServer())} disabled={saving}>
                Отмена
              </Button>
            )}
            <Button size="sm" onClick={save} loading={saving} disabled={!dirty}>
              Сохранить
            </Button>
          </span>
        )}
      </div>

      {totalCount === 0 && (
        <p className="mb-1.5 text-xs text-amber-700">
          Заявки за месяц не заведены — пока распределение идёт по обычному каскаду
          (проценты сотрудника / дефолт отдела / часы).
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="border-collapse bg-white text-xs">
          <thead>
            <tr>
              <th className="border border-emerald-200 bg-emerald-50 px-2 py-1 text-left font-medium text-gray-600">
                Распределение
              </th>
              {active.map((c) => {
                const col = companyColorByIndex(companies.findIndex((x) => x.id === c.id))
                return (
                  <th
                    key={c.id}
                    className="border border-emerald-200 bg-emerald-50 px-2 py-1 text-right font-semibold leading-tight"
                    style={{ color: col.color, minWidth: 96, maxWidth: 130 }}
                    title={c.name}
                  >
                    {companyLabel(c)}
                  </th>
                )
              })}
              <th className="border border-emerald-200 bg-emerald-100 px-2 py-1 text-right font-semibold text-emerald-900">
                ИТОГО
              </th>
            </tr>
          </thead>
          <tbody>
            {(['in_progress', 'closed'] as const).map((field) => (
              <tr key={field}>
                <td className={labelCls}>{field === 'in_progress' ? 'в работе' : 'закрытые'}</td>
                {active.map((c) => (
                  <td key={c.id} className={cellCls}>
                    <input
                      className="w-full rounded border border-gray-200 px-1 py-0.5 text-right font-mono focus:border-emerald-400 focus:outline-none disabled:border-transparent disabled:bg-transparent disabled:text-gray-600"
                      value={draft[c.id]?.[field] ?? ''}
                      onChange={(e) => setPart(c.id, field, e.target.value)}
                      placeholder="0"
                      inputMode="numeric"
                      disabled={!canEdit || saving}
                    />
                  </td>
                ))}
                <td className="border border-emerald-200 bg-emerald-50/60 px-2 py-1 text-right font-mono font-semibold">
                  {fmtInt(field === 'in_progress' ? totalWork : totalClosed)}
                </td>
              </tr>
            ))}
            {/* «Заявок» не вводится: это сумма частей и одновременно база
                распределения — второй способ её задать неминуемо разъехался бы. */}
            <tr className="bg-emerald-50/60">
              <td className={`${labelCls} font-semibold text-gray-800`}>Заявок</td>
              {active.map((c) => (
                <td key={c.id} className={`${cellCls} font-semibold`}>
                  {countOf(c.id) > 0 ? fmtInt(countOf(c.id)) : '—'}
                </td>
              ))}
              <td className="border border-emerald-200 bg-emerald-100 px-2 py-1 text-right font-mono font-bold text-emerald-900">
                {fmtInt(totalCount)}
              </td>
            </tr>
            <tr>
              <td className={labelCls} title="Заявки компании ÷ всего заявок">
                % распределения
              </td>
              {active.map((c) => (
                <td key={c.id} className={`${cellCls} text-gray-600`}>
                  {percents[c.id] ? `${fmtPercent(percents[c.id])}` : '—'}
                </td>
              ))}
              <td className="border border-emerald-200 bg-emerald-50/60 px-2 py-1 text-right font-mono font-semibold text-gray-700">
                {totalCount > 0 ? '100,00' : '—'}
              </td>
            </tr>
            {totals && (
              <tr>
                <td className={`${labelCls} font-semibold text-gray-800`} title="Сумма распределения по всем сотрудникам отдела за месяц">
                  Сумма ₽
                </td>
                {active.map((c) => (
                  <td key={c.id} className={`${cellCls} font-semibold text-emerald-800`}>
                    {totals[c.id] ? fmtInt(Math.round(totals[c.id])) : '—'}
                  </td>
                ))}
                <td className="border border-emerald-200 bg-emerald-100 px-2 py-1 text-right font-mono font-bold text-emerald-900">
                  {grandTotal ? fmtInt(Math.round(grandTotal)) : '—'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
