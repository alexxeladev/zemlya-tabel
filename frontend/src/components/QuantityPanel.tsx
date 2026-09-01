import { useEffect, useMemo, useState } from 'react'
import { timesheetApi } from '../api/timesheet'
import { usePersistentState } from '../hooks/usePersistentState'
import { toast } from '../store/toasts'
import type { DepartmentQuantities } from '../types/api'
import { companyColorByIndex } from '../utils/colors'
import { companyLabel } from '../utils/companies'
import { distribute } from '../utils/distribution'
import { UI_KEYS } from '../utils/persist'
import { Button } from './Button'

/** Минимум, нужный блоку: табель держит свой облегчённый тип компании. */
type PanelCompany = { id: number; code: string; name: string; is_active?: boolean }

type Props = {
  /** отделы с флагом «распределение по количественному показателю» из табеля */
  quantities: DepartmentQuantities[]
  companies: PanelCompany[]
  year: number
  month: number
  /** править показатель может тот же, кто правит распределение (финансовые роли) */
  canEdit: boolean
  /** суммы распределения по юрлицам за месяц, по отделам — итоговая строка блока */
  totalsByDepartment?: Map<number, { totals: Record<number, number>; grand: number }>
  /** перечитать месяц: изменившийся показатель меняет суммы в табеле и ведомости */
  onSaved: () => void
}

/** Пара полей ввода на юрлицо: две части показателя (вторая может не использоваться). */
type Draft = Record<number, { part1: string; part2: string }>

const int = (v: string | undefined): number => {
  const n = Number(String(v ?? '').replace(/[^\d]/g, ''))
  return Number.isFinite(n) && n > 0 ? n : 0
}

const fmtInt = (v: number): string => v.toLocaleString('ru-RU')
const fmtPercent = (v: number): string =>
  v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/**
 * Количественный показатель отдела по юрлицам за месяц
 * (task_hr_applications → обобщено в task_it_arm_distribution).
 *
 * ОДИН блок на все такие отделы: у HR показатель называется «Заявки» и состоит
 * из двух частей («в работе» / «закрытые», как в исходном файле HR), у ИТ — это
 * «АРМ» и вводится одним числом. Что показывать, решают настройки отдела
 * (`metric_name`, `has_parts`), а не ветка в коде: механизм и расчёт общие.
 *
 * Проценты считаются здесь тем же алгоритмом, что на бэке (`utils/distribution`
 * — зеркало `services/distribution.py`), поэтому цифра под инпутом совпадает с
 * той, по которой реально разнесётся зарплата, ещё до сохранения. Суммы, в
 * отличие от процентов, приходят с бэка: пересобирать базу распределения на
 * фронте нельзя — разъедется с ведомостью.
 *
 * Показатель заменяет каскад распределения не безусловно: рабочее место, у
 * которого распределение задано в КАРТОЧКЕ позиции, делится по карточке, а
 * показатель к нему не применяется вовсе (task_card_priority). Поэтому подпись
 * блока говорит про приоритет карточки, а не про «каскад не применяется».
 *
 * Блок показывается только для отделов с флагом, **чьи строки есть в текущей
 * выборке табеля** (отбор — `utils/quantities`, вызывается страницей: фильтры
 * юрлица и колонок клиентские, бэк о них не знает), и **сворачивается** — вместе с
 * колонками распределения он съедал пол-экрана. Свёрнутый оставляет в заголовке
 * главные цифры, выбор запоминается (`UI_KEYS.timesheetQuantities`) и переживает
 * перезагрузку. Свёрнутый блок остаётся смонтированным (`hidden`), а не
 * размонтируется: иначе набранные, но не сохранённые цифры молча пропали бы.
 */
export function QuantityPanel({
  quantities, companies, year, month, canEdit, totalsByDepartment, onSaved,
}: Props) {
  // Хук ДО раннего выхода: вызов после условного return ломает порядок хуков.
  const [open, setOpen] = usePersistentState(
    UI_KEYS.timesheetQuantities, false, (v) => typeof v === 'boolean',
  )
  if (quantities.length === 0) return null

  const totalCount = quantities.reduce((s, d) => s + d.total_count, 0)
  const totalMoney = quantities.reduce(
    (s, d) => s + (totalsByDepartment?.get(d.department_id)?.grand ?? 0), 0,
  )
  const names = quantities.map((d) => d.department_name ?? 'отдел').join(', ')
  // Показателей может быть несколько разных (HR и ИТ в одном табеле) — тогда
  // в заголовке перечисляются оба имени, иначе оно одно.
  const metrics = [...new Set(quantities.map((d) => d.metric_name || 'Количество'))]

  return (
    <div className="flex-shrink-0 border-b border-gray-200 bg-emerald-50/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-6 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-emerald-700 hover:bg-emerald-100/60"
        title={open ? 'Свернуть показатель' : 'Развернуть показатель'}
      >
        <span className="text-[10px] leading-none">{open ? '▾' : '▸'}</span>
        📋 {metrics.join(' / ')} — {names}
        {open ? (
          <span className="font-normal normal-case tracking-normal text-gray-500">
            по этим процентам делятся сотрудники без распределения в карточке
            позиции; задано в карточке — приоритет у неё
          </span>
        ) : (
          <span className="font-normal normal-case tracking-normal text-gray-500">
            — свёрнуто · всего: {fmtInt(totalCount)}
            {totalMoney > 0 && ` · распределено: ${fmtInt(Math.round(totalMoney))} ₽`}
          </span>
        )}
      </button>
      {/* Свёрнутый блок остаётся в DOM (набранное не теряется), но display
          переключается КЛАССОМ: атрибут hidden не сработал бы — `display:flex`
          из класса перебивает правило браузера для [hidden]. */}
      <div className={`flex-col gap-4 px-6 pb-3 ${open ? 'flex' : 'hidden'}`}>
        {quantities.map((dept) => (
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
  dept: DepartmentQuantities
  companies: PanelCompany[]
  year: number
  month: number
  canEdit: boolean
  totals?: Record<number, number>
  grandTotal?: number
  onSaved: () => void
}) {
  const active = useMemo(() => companies.filter((c) => c.is_active !== false), [companies])
  const metric = dept.metric_name || 'Количество'
  // Показатель без разбивки (АРМ) вводится ОДНИМ числом: строка ввода одна и
  // подписана именем показателя, отдельной строки-итога над ней нет.
  const partFields = dept.has_parts
    ? ([['part1', dept.part1_name || 'часть 1'], ['part2', dept.part2_name || 'часть 2']] as const)
    : ([['part1', metric]] as const)

  // Ключ сбрасывает черновик, когда с сервера пришёл другой месяц или другой
  // набор: иначе введённые цифры «переехали» бы в чужой период.
  const serverKey = `${dept.department_id}:${year}-${month}:${dept.items
    .map((a) => `${a.company_id}=${a.part1}/${a.part2}`)
    .join(',')}`
  const [draft, setDraft] = useState<Draft>({})
  const [saving, setSaving] = useState(false)

  const fromServer = (): Draft => {
    const next: Draft = {}
    for (const a of dept.items) {
      next[a.company_id] = { part1: String(a.part1), part2: String(a.part2) }
    }
    return next
  }

  useEffect(() => {
    setDraft(fromServer())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey])

  const parts = (id: number) => ({
    p1: int(draft[id]?.part1),
    p2: dept.has_parts ? int(draft[id]?.part2) : 0,
  })
  const countOf = (id: number) => {
    const p = parts(id)
    return p.p1 + p.p2
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

  const totalPart1 = active.reduce((s, c) => s + parts(c.id).p1, 0)
  const totalPart2 = active.reduce((s, c) => s + parts(c.id).p2, 0)
  const totalCount = totalPart1 + totalPart2
  // Проценты — тем же методом наибольшего остатка, что и на бэке: сумма ровно 100.
  const percents = useMemo(() => distribute(100, weights, null, 0.01), [weights])

  const dirty = useMemo(() => {
    const saved = fromServer()
    const ids = new Set([...Object.keys(saved), ...Object.keys(draft)].map(Number))
    for (const id of ids) {
      if (int(saved[id]?.part1) !== int(draft[id]?.part1)) return true
      if (int(saved[id]?.part2) !== int(draft[id]?.part2)) return true
    }
    return false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, dept.items])

  const setPart = (id: number, field: 'part1' | 'part2', value: string) => {
    setDraft({
      ...draft,
      [id]: {
        part1: draft[id]?.part1 ?? '',
        part2: draft[id]?.part2 ?? '',
        [field]: value.replace(/[^\d]/g, ''),
      },
    })
  }

  const save = async () => {
    setSaving(true)
    try {
      await timesheetApi.setQuantities({
        department_id: dept.department_id,
        year,
        month,
        items: active.map((c) => ({
          company_id: c.id,
          part1: parts(c.id).p1,
          part2: parts(c.id).p2,
        })),
      })
      toast.success(`${metric}: сохранено`)
      onSaved()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail ?? 'Не удалось сохранить показатель')
    } finally {
      setSaving(false)
    }
  }

  const cellCls = 'border border-emerald-200 px-2 py-1 text-right font-mono'
  const labelCls = 'border border-emerald-200 px-2 py-1 text-left text-gray-600 whitespace-nowrap'

  return (
    <div>
      {/* Название отдела и пояснение — в заголовке всего блока (он же кнопка
          сворачивания); здесь остаются только кнопки сохранения. */}
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-gray-500">
          {dept.department_name ?? 'отдел'} · {metric}
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
          Показатель «{metric}» за месяц не заведён — пока распределение идёт по
          обычному каскаду (проценты сотрудника / дефолт отдела / часы).
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
            {partFields.map(([field, label]) => (
              <tr key={field}>
                <td className={labelCls}>{label}</td>
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
                  {fmtInt(field === 'part1' ? totalPart1 : totalPart2)}
                </td>
              </tr>
            ))}
            {/* Строка «всего» нужна только у показателя из двух частей: она не
                вводится, а СЧИТАЕТСЯ (второй способ её задать неминуемо
                разъехался бы с частями). У показателя одним числом она
                дублировала бы строку ввода. */}
            {dept.has_parts && (
              <tr className="bg-emerald-50/60">
                <td className={`${labelCls} font-semibold text-gray-800`}>{metric}</td>
                {active.map((c) => (
                  <td key={c.id} className={`${cellCls} font-semibold`}>
                    {countOf(c.id) > 0 ? fmtInt(countOf(c.id)) : '—'}
                  </td>
                ))}
                <td className="border border-emerald-200 bg-emerald-100 px-2 py-1 text-right font-mono font-bold text-emerald-900">
                  {fmtInt(totalCount)}
                </td>
              </tr>
            )}
            <tr>
              <td className={labelCls} title={`${metric} компании ÷ всего`}>
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
                <td
                  className={`${labelCls} font-semibold text-gray-800`}
                  title="Сумма распределения («К выплате») по всем сотрудникам отдела за месяц"
                >
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
