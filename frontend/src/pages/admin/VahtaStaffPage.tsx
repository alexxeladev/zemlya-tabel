// «Сотрудники охраны» — штат охранных подразделений ведётся здесь
// (task_guard_ownership).
//
// Таблица сотрудников одна на всю систему. Модуль вахты владеет только
// РАБОЧИМ МЕСТОМ в охране: должность, сумма, подразделение, период работы на
// месте. Человек (ФИО, таб. №, доступ, даты работы в компании) остаётся в общем
// справочнике — поэтому при правке ФИО и таб. № здесь только показываются.
//
// Тип оплаты не выбирается: он следует из должности (начальник охраны — оклад,
// остальные — смена). Пост в карточке не хранится: на пост ставят помесячно в
// табеле, и в списке он показан за выбранный месяц.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { listDepartments } from '../../api/departments'
import {
  createVahtaStaff,
  findSimilarEmployees,
  getVahtaDepartments,
  listVahtaStaff,
  updateVahtaStaff,
} from '../../api/vahta'
import { Button } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { useAuthStore } from '../../store/auth'
import { usePeriodStore } from '../../store/period'
import { toast } from '../../store/toasts'
import type {
  Department,
  VahtaDepartment,
  VahtaSimilarEmployee,
  VahtaStaff,
  VahtaStaffInput,
} from '../../types/api'
import {
  GUARD_CONFIRM_CANCELLED,
  guardAmountLabel,
  isGuardDepartment,
  withGuardConfirm,
} from '../../utils/guardStaff'
import { formatMoney } from '../../utils/money'
import { useGuardJobTitles } from '../../hooks/useGuardJobTitles'

const MONTHS = [
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]

const fmtDate = (v: string | null) =>
  v ? v.split('-').reverse().join('.') : '—'

type Draft = {
  full_name: string
  tab_number: string
  department_id: string
  job_title_id: string
  amount: string
  hire_date: string
  dismissal_date: string
}

const emptyDraft = (deptId: number | undefined): Draft => ({
  full_name: '',
  tab_number: '',
  department_id: deptId ? String(deptId) : '',
  job_title_id: '',
  amount: '',
  hire_date: '',
  dismissal_date: '',
})

const toDraft = (s: VahtaStaff): Draft => ({
  full_name: s.full_name,
  tab_number: s.tab_number ?? '',
  department_id: String(s.department_id),
  job_title_id: s.job_title_id != null ? String(s.job_title_id) : '',
  amount: s.amount != null ? String(parseFloat(s.amount)) : '',
  hire_date: s.hire_date ?? '',
  dismissal_date: s.dismissal_date ?? '',
})

const orNull = (v: string) => (v.trim() === '' ? null : v.trim())

export function VahtaStaffPage() {
  const { year, month, setPeriod } = usePeriodStore()
  const role = useAuthStore((s) => s.user?.role)
  const [rows, setRows] = useState<VahtaStaff[]>([])
  const [guardDepts, setGuardDepts] = useState<VahtaDepartment[]>([])
  const [allDepts, setAllDepts] = useState<Department[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  // null — окно закрыто, 'new' — оформление, число — правка рабочего места
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    listVahtaStaff(year, month)
      .then(setRows)
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Не удалось загрузить'))
      .finally(() => setLoading(false))
  }, [year, month])

  useEffect(load, [load])
  useEffect(() => {
    getVahtaDepartments().then(setGuardDepts).catch(() => setGuardDepts([]))
    listDepartments().then(setAllDepts).catch(() => setAllDepts([]))
  }, [])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (r) =>
        r.full_name.toLowerCase().includes(q) ||
        (r.tab_number ?? '').toLowerCase().includes(q) ||
        r.places.some((p) => p.toLowerCase().includes(q)),
    )
  }, [rows, query])

  const people = new Set(shown.map((r) => r.employee_id)).size
  const editRow = typeof editing === 'number' ? rows.find((r) => r.position_id === editing) : null

  if (!loading && guardDepts.length === 0) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        Нет доступных подразделений охраны. Отметьте отдел галочкой «подразделение
        охраны» в{' '}
        <Link to="/admin/org" className="underline">оргструктуре</Link>.
      </div>
    )
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-800">Сотрудники охраны</h1>
          <p className="mt-1 text-sm text-slate-500">
            Рабочие места охранных подразделений: оформление и правка ведутся
            здесь. ФИО, табельный номер и доступ в систему — в общем справочнике.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            to="/vahta"
            className="rounded-md bg-gray-100 px-3 py-1.5 text-sm text-gray-800 hover:bg-gray-200"
          >
            ← К табелю
          </Link>
          <Link
            to="/vahta/posts"
            className="rounded-md bg-gray-100 px-3 py-1.5 text-sm text-gray-800 hover:bg-gray-200"
          >
            Посты и экипажи
          </Link>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => setEditing('new')}>
          + Оформить сотрудника
        </Button>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск: ФИО, таб. №, пост"
          className="w-64 rounded-md border border-gray-300 px-3 py-1.5 text-sm"
        />
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Пост за
          <input
            type="month"
            value={`${year}-${String(month).padStart(2, '0')}`}
            onChange={(e) => {
              const [y, m] = e.target.value.split('-').map(Number)
              if (y && m) setPeriod(y, m)
            }}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm"
          />
        </label>
        <span className="ml-auto text-xs text-slate-500">
          {people} чел. · {shown.length} рабочих мест
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">ФИО</th>
              <th className="px-3 py-2">Таб. №</th>
              <th className="px-3 py-2">Должность</th>
              <th className="px-3 py-2">Пост ({MONTHS[month - 1]})</th>
              <th className="px-3 py-2">Оплата</th>
              <th className="px-3 py-2 text-right">Сумма</th>
              <th className="px-3 py-2">Работает на месте</th>
              <th className="px-3 py-2">Официально</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.position_id} className="group border-t border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-2">
                  <span className={r.employee_is_active ? 'text-slate-800' : 'text-slate-400'}>
                    {r.full_name}
                  </span>
                  {!r.employee_is_active && (
                    <span className="ml-2 text-xs text-slate-400">уволен</span>
                  )}
                  {guardDepts.length > 1 && (
                    <div className="text-[11px] text-slate-400">{r.department_name}</div>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-slate-600">{r.tab_number ?? '—'}</td>
                <td className="px-3 py-2">{r.job_title_name}</td>
                <td className="px-3 py-2 text-slate-600">
                  {r.places.length ? r.places.join(', ') : (
                    <span className="text-slate-400">не стоит на посту</span>
                  )}
                </td>
                <td className="px-3 py-2 text-slate-600">
                  {r.pay_type === 'salary' ? 'оклад' : 'посменно'}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono">
                  {r.amount != null ? (
                    <>
                      {formatMoney(r.amount)}
                      <span className="ml-1 text-xs text-slate-400">
                        {r.pay_type === 'salary' ? '₽/мес' : '₽/смена'}
                      </span>
                    </>
                  ) : (
                    <span className="text-slate-400">не задана</span>
                  )}
                </td>
                <td className="px-3 py-2 text-slate-600">
                  {r.hire_date || r.dismissal_date
                    ? `${fmtDate(r.hire_date)} — ${r.dismissal_date ? fmtDate(r.dismissal_date) : 'по н. в.'}`
                    : <span className="text-slate-400">без ограничений</span>}
                </td>
                <td className="px-3 py-2">
                  {r.is_official == null ? (
                    <span className="text-slate-400" title="В этом месяце не стоит на посту">—</span>
                  ) : r.is_official ? 'да' : 'нет'}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => setEditing(r.position_id)}
                    className="invisible cursor-pointer text-sm text-blue-700 hover:underline group-hover:visible"
                  >
                    Изменить
                  </button>
                </td>
              </tr>
            ))}
            {!loading && shown.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-slate-400">
                  {query ? 'Никого не нашлось' : 'Сотрудников охраны пока нет'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        «Официально» и пост — за выбранный месяц: они задаются в строке табеля
        вахты, человека ставят на пост помесячно.
      </p>

      {editing !== null && (
        <StaffModal
          row={editRow ?? null}
          guardDepts={guardDepts}
          otherDepts={allDepts.filter((d) => d.is_active && !isGuardDepartment(d))}
          canOpenDirectory={role === 'admin' || role === 'manager' || role === 'accountant'}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            load()
          }}
        />
      )}
    </div>
  )
}

function StaffModal({
  row,
  guardDepts,
  otherDepts,
  canOpenDirectory,
  onClose,
  onSaved,
}: {
  row: VahtaStaff | null
  guardDepts: VahtaDepartment[]
  otherDepts: Department[]
  canOpenDirectory: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [draft, setDraft] = useState<Draft>(row ? toDraft(row) : emptyDraft(guardDepts[0]?.id))
  const jobTitles = useGuardJobTitles()
  const [similar, setSimilar] = useState<VahtaSimilarEmployee[]>([])
  const [saving, setSaving] = useState(false)
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }))

  // Против дублей при оформлении — как в быстром найме.
  const searchName = row ? '' : draft.full_name.trim()
  useEffect(() => {
    if (searchName.length < 3) return
    const t = window.setTimeout(() => {
      findSimilarEmployees(searchName).then(setSimilar).catch(() => setSimilar([]))
    }, 350)
    return () => window.clearTimeout(t)
  }, [searchName])
  const shownSimilar = searchName.length < 3 ? [] : similar

  const transferOut =
    row != null && otherDepts.some((d) => String(d.id) === draft.department_id)

  const save = async () => {
    setSaving(true)
    try {
      const base: VahtaStaffInput = {
        department_id: Number(draft.department_id),
        // Перевод из охраны — должность не нужна (в обычном отделе её ведёт
        // справочник); пустой выбор не шлём вовсе, иначе бэк искал бы должность #0.
        ...(draft.job_title_id && !transferOut ? { job_title_id: Number(draft.job_title_id) } : {}),
        amount: orNull(draft.amount),
        hire_date: orNull(draft.hire_date),
        dismissal_date: orNull(draft.dismissal_date),
      }
      if (row) {
        const saved = await withGuardConfirm(
          (confirm) => updateVahtaStaff(row.position_id, base, confirm),
          (message) => window.confirm(message),
        )
        if (saved === GUARD_CONFIRM_CANCELLED) {
          toast.info('Перевод отменён — ничего не изменилось')
          return
        }
        toast.success(transferOut ? 'Переведён — дальше ведётся в общем справочнике' : 'Сохранено')
      } else {
        const created = await createVahtaStaff({
          ...base,
          full_name: draft.full_name.trim(),
          tab_number: orNull(draft.tab_number),
        })
        toast.success(`Оформлен, табельный номер ${created.tab_number ?? '—'}`)
      }
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
    } finally {
      setSaving(false)
    }
  }

  const inputCls = 'w-full rounded-md border border-gray-300 px-3 py-2 text-sm'
  const canSave =
    (row != null || draft.full_name.trim().length >= 3) &&
    draft.department_id !== '' &&
    (transferOut || draft.job_title_id !== '')

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={row ? `Сотрудник охраны: ${row.full_name}` : 'Оформить сотрудника охраны'}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button onClick={save} loading={saving} disabled={!canSave}>
            {row ? (transferOut ? 'Перевести' : 'Сохранить') : 'Оформить'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">ФИО</span>
          {row ? (
            <div className="rounded-md bg-gray-50 px-3 py-2 text-gray-700">{row.full_name}</div>
          ) : (
            <input
              autoFocus
              value={draft.full_name}
              onChange={(e) => set('full_name', e.target.value)}
              className={inputCls}
            />
          )}
        </label>

        {shownSimilar.length > 0 && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
            Похожие уже есть в справочнике — проверьте, не он ли это:
            <ul className="mt-1 list-disc pl-4">
              {shownSimilar.map((s) => (
                <li key={s.id}>
                  {s.full_name} {s.tab_number ? `(${s.tab_number})` : ''}
                  {s.department_name ? ` — ${s.department_name}` : ''}
                  {!s.is_active ? ', уволен' : ''}
                </li>
              ))}
            </ul>
            Действующего сотрудника на пост ставят в табеле вахты — нового заводить не нужно.
          </div>
        )}

        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Табельный номер</span>
          {row ? (
            <div className="rounded-md bg-gray-50 px-3 py-2 font-mono text-gray-700">
              {row.tab_number ?? '—'}
            </div>
          ) : (
            <input
              value={draft.tab_number}
              onChange={(e) => set('tab_number', e.target.value)}
              placeholder="присвоится автоматически"
              className={inputCls}
            />
          )}
        </label>
        {row && canOpenDirectory && (
          <p className="-mt-2 text-[11px] text-gray-500">
            ФИО, табельный номер и доступ в систему правятся в{' '}
            <Link to={`/admin/employees?employee_id=${row.employee_id}`} className="underline">
              карточке сотрудника
            </Link>
            .
          </p>
        )}

        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Подразделение</span>
          <select
            value={draft.department_id}
            onChange={(e) => set('department_id', e.target.value)}
            className={inputCls}
          >
            <optgroup label="Охрана">
              {guardDepts.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </optgroup>
            {row && otherDepts.length > 0 && (
              <optgroup label="Перевести в обычное подразделение">
                {otherDepts.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {transferOut && (
          <p className="-mt-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Перевод из охраны: рабочее место уйдёт в общий справочник. Если у него
            нет графика или ставки, оно не войдёт в расчёт — при сохранении
            покажем, чего не хватает.
          </p>
        )}

        {!transferOut && (
          <>
            <label className="block text-sm">
              <span className="mb-1 block text-gray-600">Должность</span>
              <select
                value={draft.job_title_id}
                onChange={(e) => set('job_title_id', e.target.value)}
                className={inputCls}
              >
                <option value="">— выберите —</option>
                {jobTitles.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </label>

            <label className="block text-sm">
              <span className="mb-1 block text-gray-600">
                {guardAmountLabel(jobTitles.find((t) => String(t.id) === draft.job_title_id)?.pay_type)}
              </span>
              <input
                value={draft.amount}
                onChange={(e) => set('amount', e.target.value.replace(',', '.'))}
                inputMode="decimal"
                className={`${inputCls} text-right`}
              />
            </label>
          </>
        )}

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="mb-1 block text-gray-600">Принят на место</span>
            <input
              type="date"
              value={draft.hire_date}
              onChange={(e) => set('hire_date', e.target.value)}
              className={inputCls}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-gray-600">Уволен с места</span>
            <input
              type="date"
              value={draft.dismissal_date}
              onChange={(e) => set('dismissal_date', e.target.value)}
              className={inputCls}
            />
          </label>
        </div>
        <p className="text-[11px] text-gray-500">
          Пустая дата — без ограничения. Пост назначается в табеле вахты помесячно.
        </p>
      </div>
    </Modal>
  )
}
