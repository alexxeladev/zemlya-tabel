// «Сотрудники охраны» — штат охранных подразделений ведётся здесь
// (task_guard_ownership). Вкладка настроек вахты (task_vahta_settings_staff):
// отдельной страницы и двух кнопок входа больше нет. Месяц и открытое рабочее
// место приходят из адреса — их держит экран настроек.
//
// Таблица сотрудников одна на всю систему. Модуль вахты владеет только
// РАБОЧИМ МЕСТОМ в охране: должность, сумма, подразделение, период работы на
// месте. Человек (ФИО, таб. №, доступ, даты работы в компании) остаётся в общем
// справочнике — поэтому при правке ФИО и таб. № здесь только показываются.
//
// Тип оплаты не выбирается: он следует из должности (начальник охраны — оклад,
// остальные — смена). Пост в карточке не хранится: на пост ставят помесячно в
// табеле, и в списке он показан за выбранный месяц.
//
// Вёрстка — по артборду 02 макета `docs/design/vahta-mock.html`: список слева,
// форма рабочего места — боковая панель справа, а не модалка (редизайн,
// артборд 05): список остаётся виден, открытое место — в адресе.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { listDepartments } from '../../api/departments'
import {
  createVahtaStaff,
  findSimilarEmployees,
  getVahtaSettings,
  getVahtaDepartments,
  getVahtaMonth,
  listVahtaStaff,
  updateVahtaStaff,
} from '../../api/vahta'
import { DsButton } from '../ds/Button'
import { DateField, FieldRow, SearchField, SelectField, TextField } from '../ds/fields'
import { Pill } from '../ds/Pill'
import { SidePanel } from '../ds/SidePanel'
import { useAuthStore } from '../../store/auth'
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
import { MONTHS_RU, MONTHS_RU_PREP } from '../../utils/ruDate'
import { useGuardJobTitles } from '../../hooks/useGuardJobTitles'
import {
  changedFields,
  monthFactHint,
  monthFactsByPosition,
  officialLabel,
  placePeriod,
  plural,
  type MonthFact,
} from './staffFormat'

type Draft = {
  full_name: string
  tab_number: string
  department_id: string
  job_title_id: string
  /** Ставка или оклад — ТОЛЬКО при переводе в обычное подразделение. */
  amount: string
  hire_date: string
  dismissal_date: string
  is_official: boolean
  official_salary: string
}

const emptyDraft = (deptId: number | undefined): Draft => ({
  full_name: '',
  tab_number: '',
  department_id: deptId ? String(deptId) : '',
  job_title_id: '',
  amount: '',
  hire_date: '',
  dismissal_date: '',
  is_official: false,
  official_salary: '',
})

const toDraft = (s: VahtaStaff): Draft => ({
  full_name: s.full_name,
  tab_number: s.tab_number ?? '',
  department_id: String(s.department_id),
  job_title_id: s.job_title_id != null ? String(s.job_title_id) : '',
  amount: '',
  hire_date: s.hire_date ?? '',
  dismissal_date: s.dismissal_date ?? '',
  is_official: s.is_official,
  official_salary: s.official_salary != null ? String(parseFloat(s.official_salary)) : '',
})

const orNull = (v: string) => (v.trim() === '' ? null : v.trim())

/** Подписи полей для «Изменено: …» в подвале панели. */
const FIELD_LABELS: Partial<Record<keyof Draft, string>> = {
  department_id: 'подразделение',
  job_title_id: 'должность',
  amount: 'ставка при переводе',
  hire_date: 'дата приёма',
  dismissal_date: 'дата увольнения',
  is_official: 'официальное трудоустройство',
  official_salary: 'официальная зарплата',
}

export function StaffTab({
  year,
  month,
  positionId,
  onOpen,
}: {
  year: number
  month: number
  /** Открытое рабочее место из адреса (`?position_id=`), `null` — ничего. */
  positionId: number | null
  /** Открыть/закрыть рабочее место — экран пишет это в адрес. */
  onOpen: (positionId: number | null) => void
}) {
  const role = useAuthStore((s) => s.user?.role)
  const [rows, setRows] = useState<VahtaStaff[]>([])
  const [guardDepts, setGuardDepts] = useState<VahtaDepartment[]>([])
  const [allDepts, setAllDepts] = useState<Department[]>([])
  // Смены месяца по рабочему месту — для подсказки-формулы под ставкой.
  // Факт месяца по рабочему месту из табеля вахты: смены и зарплата за них ПО
  // СТАВКАМ СТРОК (у строки своя ставка — от поста или правленая, не ставка
  // рабочего места). `null` — ещё грузится, 'error' — не загрузилось: «смен нет»
  // тогда было бы неправдой.
  const [monthFacts, setMonthFacts] = useState<Map<number, MonthFact> | null | 'error'>(null)
  // Ответ за прежний месяц, пришедший позже нового, отбрасывается.
  const monthRequest = useRef(0)
  const [loading, setLoading] = useState(true)
  // Список загружен без ошибки: только тогда «места нет в списке» — правда.
  const [loaded, setLoaded] = useState(false)
  const [query, setQuery] = useState('')
  // Оформление нового — состояние вкладки; правка существующего — адрес.
  const [creating, setCreating] = useState(false)
  const editing: number | 'new' | null = creating ? 'new' : positionId

  const load = useCallback(() => {
    setLoading(true)
    listVahtaStaff(year, month)
      .then((data) => {
        setRows(data)
        setLoaded(true)
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : 'Не удалось загрузить'))
      .finally(() => setLoading(false))
    const request = ++monthRequest.current
    setMonthFacts(null)
    getVahtaMonth(year, month)
      .then((data) => {
        if (request !== monthRequest.current) return
        setMonthFacts(monthFactsByPosition(data.zones.flatMap((z) => z.cards.flatMap((c) => c.rows))))
      })
      .catch(() => {
        if (request === monthRequest.current) setMonthFacts('error')
      })
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

  const people = new Set(rows.map((r) => r.employee_id)).size
  const editRow = typeof editing === 'number' ? rows.find((r) => r.position_id === editing) : null
  const panelOpen = editing === 'new' || Boolean(editRow)
  const monthName = MONTHS_RU[month - 1].toLowerCase()

  // Ссылка на рабочее место, которого в списке нет (снято с учёта, чужое
  // подразделение, опечатка в адресе): сказать словами и убрать из адреса, а не
  // показывать пустую форму оформления.
  //
  // Проверяется САМО место из адреса, а не открытая форма, и не во время
  // оформления нового: «Оформить» очищает адрес, но адрес обновляется на
  // следующем кадре — в промежутке форма уже «новая», а `position_id` прежнего
  // человека ещё в адресе, и проверка ложно кричала «такого места нет».
  const addressedRowExists = positionId !== null && rows.some((r) => r.position_id === positionId)
  useEffect(() => {
    if (creating || loading || !loaded || positionId === null || addressedRowExists) return
    toast.error('Такого рабочего места среди сотрудников охраны нет')
    onOpen(null)
  }, [creating, loading, loaded, positionId, addressedRowExists, onOpen])

  if (!loading && guardDepts.length === 0) {
    return (
      <div className="rounded-ds-md border border-ds-warn-line bg-ds-warn-soft p-4 text-[13px] text-ds-warn">
        Нет доступных подразделений охраны. Отметьте отдел галочкой «подразделение
        охраны» в{' '}
        <Link to="/admin/org" className="underline">оргструктуре</Link>.
      </div>
    )
  }

  const close = () => {
    setCreating(false)
    onOpen(null)
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchField
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="ФИО, таб. № или пост"
          aria-label="Поиск сотрудников охраны"
          className="w-[280px]"
        />
        <DsButton
          variant="primary"
          icon="plus"
          onClick={() => {
            onOpen(null)
            setCreating(true)
          }}
        >
          Оформить сотрудника
        </DsButton>
        <span className="ml-auto text-[12.5px] text-ds-muted">
          <b className="font-medium text-ds-ink-2">
            {plural(people, 'человек', 'человека', 'человек')}
          </b>{' '}
          · {plural(rows.length, 'рабочее место', 'рабочих места', 'рабочих мест')}
          {query && ` · найдено ${shown.length}`}
        </span>
      </div>
      <p className="mb-3 text-[12.5px] text-ds-muted">
        ФИО, табельный номер и доступ в систему правятся в карточке сотрудника. Здесь — его
        рабочее место в охране.
      </p>

      <div
        className="grid items-start gap-4"
        style={{ gridTemplateColumns: panelOpen ? 'minmax(0,1fr) 520px' : 'minmax(0,1fr)' }}
      >
        <div className="min-w-0 overflow-hidden rounded-ds-lg border border-ds-line bg-ds-surface">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px] font-semibold text-ds-muted">
                <th className="w-[92px] border-b border-ds-line-strong bg-ds-surface-2 px-3 py-2">Таб. №</th>
                <th className="border-b border-ds-line-strong bg-ds-surface-2 px-3 py-2">Сотрудник</th>
                {!panelOpen && (
                  <th className="border-b border-ds-line-strong bg-ds-surface-2 px-3 py-2">
                    Пост ({monthName})
                  </th>
                )}
                <th className="border-b border-ds-line-strong bg-ds-surface-2 px-3 py-2">Официально</th>
                {!panelOpen && (
                  <th className="border-b border-ds-line-strong bg-ds-surface-2 px-3 py-2">На месте</th>
                )}
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const selected = typeof editing === 'number' && editing === r.position_id
                const place = r.places.length ? r.places.join(', ') : 'не на посту'
                return (
                  <tr
                    key={r.position_id}
                    aria-selected={selected}
                    onClick={() => {
                      setCreating(false)
                      onOpen(r.position_id)
                    }}
                    className={`cursor-pointer ${
                      selected
                        ? 'bg-ds-accent-soft [&>td:first-child]:shadow-[inset_3px_0_0_var(--ds-accent)]'
                        : 'hover:bg-ds-surface-2'
                    }`}
                  >
                    <td className="whitespace-nowrap border-b border-ds-line px-3 py-2 font-ds-mono text-[12px] text-ds-muted">
                      {r.tab_number ?? 'нет номера'}
                    </td>
                    <td className="border-b border-ds-line px-3 py-2">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          setCreating(false)
                          onOpen(r.position_id)
                        }}
                        className={`cursor-pointer text-left font-medium ${
                          r.employee_is_active ? 'text-ds-ink' : 'text-ds-muted'
                        }`}
                      >
                        {r.full_name}
                      </button>
                      {!r.employee_is_active && <Pill className="ml-2">уволен</Pill>}
                      <span className="block text-[11.5px] text-ds-muted">
                        {r.job_title_name}
                        {panelOpen && ` · ${place}`}
                        {guardDepts.length > 1 && ` · ${r.department_name}`}
                      </span>
                    </td>
                    {!panelOpen && (
                      <td className={`border-b border-ds-line px-3 py-2 ${r.places.length ? '' : 'text-ds-muted'}`}>
                        {place}
                      </td>
                    )}
                    <td
                      className={`whitespace-nowrap border-b border-ds-line px-3 py-2 ${
                        r.is_official ? '' : 'text-ds-muted'
                      }`}
                    >
                      {officialLabel(r.is_official)}
                      {r.is_official && (
                        <span className="block text-[11.5px] text-ds-muted">
                          {r.official_salary != null
                            ? `${formatMoney(r.official_salary)} ₽/мес на руки`
                            : 'зарплата не задана'}
                        </span>
                      )}
                    </td>
                    {!panelOpen && (
                      <td
                        className={`whitespace-nowrap border-b border-ds-line px-3 py-2 ${
                          r.hire_date || r.dismissal_date ? '' : 'text-ds-muted'
                        }`}
                      >
                        {placePeriod(r.hire_date, r.dismissal_date)}
                      </td>
                    )}
                  </tr>
                )
              })}
              {!loading && shown.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-ds-muted">
                    {query
                      ? 'Никого не нашлось. Измените запрос.'
                      : 'Сотрудников охраны пока нет. Оформите первого кнопкой «Оформить сотрудника».'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Панель — только когда подразделения охраны уже пришли: от них
            зависит подразделение по умолчанию в форме оформления. */}
        {panelOpen && guardDepts.length > 0 && (
          // Своя рамка и тень: SidePanel рисует только левую границу (он
          // рассчитан стоять у края экрана), а здесь он карточкой рядом со списком.
          <div className="sticky top-3 h-[calc(100vh-9rem)] min-h-[420px] overflow-hidden rounded-ds-lg border border-ds-line shadow-ds-pop">
            <StaffPanel
              key={editRow ? editRow.position_id : 'new'}
              row={editRow ?? null}
              guardDepts={guardDepts}
              otherDepts={allDepts.filter((d) => d.is_active && !isGuardDepartment(d))}
              canOpenDirectory={role === 'admin' || role === 'manager' || role === 'accountant'}
              month={month}
              fact={
                !editRow || monthFacts === null
                  ? null
                  : monthFacts === 'error'
                    ? 'error'
                    : monthFacts.get(editRow.position_id) ?? { shifts: 0, pay: 0 }
              }
              onClose={close}
              onSaved={() => {
                close()
                load()
              }}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function StaffPanel({
  row,
  guardDepts,
  otherDepts,
  canOpenDirectory,
  month,
  fact,
  onClose,
  onSaved,
}: {
  row: VahtaStaff | null
  guardDepts: VahtaDepartment[]
  otherDepts: Department[]
  canOpenDirectory: boolean
  month: number
  /** Факт месяца из табеля; `null` — грузится, 'error' — не загрузилось. */
  fact: MonthFact | null | 'error'
  onClose: () => void
  onSaved: () => void
}) {
  const initial = useMemo(
    () => (row ? toDraft(row) : emptyDraft(guardDepts[0]?.id)),
    [row, guardDepts],
  )
  const [draft, setDraft] = useState<Draft>(initial)
  const jobTitles = useGuardJobTitles()
  // Ставка налога — настройка вахты (вкладка «Налог»), а не константа: в
  // подсказке про «налоги сверху» она должна быть той же, что в расчёте.
  const [taxPercent, setTaxPercent] = useState<string | null>(null)
  useEffect(() => {
    getVahtaSettings()
      .then((s) => setTaxPercent(String(parseFloat(s.employer_tax_percent))))
      .catch(() => setTaxPercent(null))
  }, [])
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
  const payType = jobTitles.find((t) => String(t.id) === draft.job_title_id)?.pay_type
  const monthPrep = MONTHS_RU_PREP[month - 1]

  const save = async () => {
    setSaving(true)
    try {
      const base: VahtaStaffInput = {
        department_id: Number(draft.department_id),
        // Перевод из охраны — должность не нужна (в обычном отделе её ведёт
        // справочник); пустой выбор не шлём вовсе, иначе бэк искал бы должность #0.
        ...(draft.job_title_id && !transferOut ? { job_title_id: Number(draft.job_title_id) } : {}),
        // Ставка — только для перевода в обычное подразделение: у охранного
        // места её нет, цена смены живёт на посту и в строке табеля.
        ...(transferOut ? { amount: orNull(draft.amount) } : {}),
        hire_date: orNull(draft.hire_date),
        dismissal_date: orNull(draft.dismissal_date),
        // При переводе официальные поля не шлём: место уходит из охраны, и
        // признак гасит сам бэк — иначе одно «Продолжить?» подтверждало бы два
        // разных действия (нашло ревью).
        ...(transferOut
          ? {}
          : {
              is_official: draft.is_official,
              official_salary: draft.is_official ? orNull(draft.official_salary) : null,
            }),
      }
      if (row) {
        const saved = await withGuardConfirm(
          (confirm) => updateVahtaStaff(row.position_id, base, confirm),
          (message) => window.confirm(message),
        )
        if (saved === GUARD_CONFIRM_CANCELLED) {
          toast.info(
            transferOut
              ? 'Перевод отменён — ничего не изменилось'
              : 'Отменено — ничего не изменилось',
          )
          return
        }
        toast.success(transferOut ? 'Переведён — дальше ведётся в общем справочнике' : 'Сохранено')
      } else {
        const created = await createVahtaStaff({
          ...base,
          full_name: draft.full_name.trim(),
          tab_number: orNull(draft.tab_number),
        })
        toast.success(`Оформлен, табельный номер ${created.tab_number ?? 'не присвоен'}`)
      }
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
    } finally {
      setSaving(false)
    }
  }

  const canSave =
    (row != null || draft.full_name.trim().length >= 3) &&
    draft.department_id !== '' &&
    (transferOut || draft.job_title_id !== '')
  const changed = row ? changedFields(initial, draft, FIELD_LABELS) : []

  const group = (title: string) => (
    <p className="mb-2.5 mt-0 flex items-center gap-2 text-[12px] font-semibold text-ds-ink-2 after:h-px after:flex-1 after:bg-ds-line">
      {title}
    </p>
  )

  return (
    <SidePanel
      title={row ? row.full_name : 'Оформить сотрудника охраны'}
      subtitle={
        row
          ? [row.tab_number, row.job_title_name, row.department_name].filter(Boolean).join(' · ')
          : 'Новый сотрудник и его рабочее место в охране'
      }
      onClose={onClose}
      footer={
        <>
          <p className="m-0 text-[12px] text-ds-muted">
            {changed.length > 0 && `Изменено: ${changed.join(', ')}`}
          </p>
          <DsButton variant="ghost" className="ml-auto" onClick={onClose}>
            Отменить
          </DsButton>
          <DsButton variant="primary" onClick={() => void save()} loading={saving} disabled={!canSave}>
            {row ? (transferOut ? 'Перевести' : 'Сохранить') : 'Оформить'}
          </DsButton>
        </>
      }
    >
      <div className="mb-5">
        {group('Человек')}
        {row ? (
          <>
            <dl className="m-0 grid grid-cols-[140px_1fr] gap-x-3 gap-y-1 text-[12.5px]">
              <dt className="text-ds-muted">ФИО</dt>
              <dd className="m-0 text-ds-ink">{row.full_name}</dd>
              <dt className="text-ds-muted">Табельный номер</dt>
              <dd className="m-0 font-ds-mono text-ds-ink">{row.tab_number ?? 'не присвоен'}</dd>
            </dl>
            {canOpenDirectory && (
              <Link
                to={`/admin/employees?employee_id=${row.employee_id}`}
                className="mt-2 inline-block text-[12.5px] text-ds-accent hover:underline"
              >
                ФИО, таб. № и доступ — в карточке сотрудника
              </Link>
            )}
          </>
        ) : (
          <>
            <FieldRow label="ФИО" htmlFor="staff-fio">
              <TextField
                id="staff-fio"
                autoFocus
                value={draft.full_name}
                onChange={(e) => set('full_name', e.target.value)}
              />
            </FieldRow>
            {shownSimilar.length > 0 && (
              <div className="mb-2.5 rounded-ds-md border border-ds-warn-line bg-ds-warn-soft p-3 text-[12px] text-ds-warn">
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
            <FieldRow label="Табельный номер" htmlFor="staff-tab" hint="Пусто — присвоится автоматически, по общей нумерации">
              <TextField
                id="staff-tab"
                value={draft.tab_number}
                onChange={(e) => set('tab_number', e.target.value)}
                placeholder="присвоится автоматически"
                className="max-w-[220px] font-ds-mono"
              />
            </FieldRow>
          </>
        )}
      </div>

      <div className="mb-5">
        {group('Рабочее место в охране')}
        <FieldRow label="Подразделение" htmlFor="staff-dept">
          <SelectField
            id="staff-dept"
            value={draft.department_id}
            onChange={(e) => set('department_id', e.target.value)}
            className="w-full"
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
          </SelectField>
        </FieldRow>
        {transferOut && (
          <p className="mb-2.5 rounded-ds-md border border-ds-warn-line bg-ds-warn-soft px-3 py-2 text-[12px] text-ds-warn">
            Перевод из охраны: рабочее место уйдёт в общий справочник. Если у него нет графика
            или ставки, оно не войдёт в расчёт — при сохранении покажем, чего не хватает.
          </p>
        )}
        {!transferOut && (
          <>
            <FieldRow
              label="Должность"
              htmlFor="staff-title"
              hint={
                payType
                  ? `Способ оплаты — от должности: ${payType === 'salary' ? 'оклад за месяц' : 'ставка за смену'}`
                  : undefined
              }
            >
              <SelectField
                id="staff-title"
                value={draft.job_title_id}
                onChange={(e) => set('job_title_id', e.target.value)}
                className="w-full"
              >
                <option value="">Выберите должность</option>
                {jobTitles.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </SelectField>
            </FieldRow>
            <p className="mb-2.5 text-[11.5px] text-ds-muted">
              {payType === 'salary'
                ? 'Оклад здесь не задаётся: он стоит в строке табеля (колонка «Ставка / оклад») и по умолчанию берётся от поста.'
                : 'Ставка за смену здесь не задаётся: цена смены — у объекта, поста или экипажа, а правится в строке табеля.'}
            </p>
            <FieldRow label="Официально устроен" htmlFor="staff-official">
              <label className="flex cursor-pointer items-center gap-2 text-[13px]">
                <input
                  id="staff-official"
                  type="checkbox"
                  checked={draft.is_official}
                  onChange={(e) => set('is_official', e.target.checked)}
                />
                <span>{draft.is_official ? 'да' : 'нет'}</span>
              </label>
            </FieldRow>
            {draft.is_official && (
              <FieldRow
                label="Официальная зарплата на руки, ₽/мес"
                htmlFor="staff-official-salary"
                hint={
                  'Сумма на карту после НДФЛ. Налоги' +
                  (taxPercent ? ` ${taxPercent} %` : '') +
                  ' добавляются сверху при разнесении по юрлицам'
                }
              >
                <TextField
                  id="staff-official-salary"
                  value={draft.official_salary}
                  onChange={(e) => set('official_salary', e.target.value.replace(',', '.'))}
                  inputMode="decimal"
                  className="max-w-[160px] text-right font-ds-mono"
                />
              </FieldRow>
            )}
          </>
        )}
        {transferOut && (
          <FieldRow
            label={guardAmountLabel(payType)}
            htmlFor="staff-amount"
            hint="Нужна расчёту в обычном подразделении — в охране суммы у места нет"
          >
            <TextField
              id="staff-amount"
              value={draft.amount}
              onChange={(e) => set('amount', e.target.value.replace(',', '.'))}
              inputMode="decimal"
              className="max-w-[160px] text-right font-ds-mono"
            />
          </FieldRow>
        )}
      </div>

      <div className="mb-5">
        {group('Период на месте')}
        <FieldRow label="Принят на место" htmlFor="staff-hire">
          <DateField id="staff-hire" value={draft.hire_date} onChange={(v) => set('hire_date', v)} />
        </FieldRow>
        <FieldRow
          label="Уволен с места"
          htmlFor="staff-dismiss"
          hint="Пустая дата — без ограничения. После даты увольнения дни в табеле закрыты."
        >
          <DateField
            id="staff-dismiss"
            value={draft.dismissal_date}
            onChange={(v) => set('dismissal_date', v)}
          />
        </FieldRow>
      </div>

      {row && (
        <div>
          {group(`В ${monthPrep}`)}
          <dl className="m-0 grid grid-cols-[140px_1fr] gap-x-3 gap-y-1 text-[12.5px]">
            <dt className="text-ds-muted">Пост</dt>
            <dd className="m-0 text-ds-ink-2">{row.places.length ? row.places.join(', ') : 'не на посту'}</dd>
            <dt className="text-ds-muted">Смен</dt>
            <dd className="m-0 text-ds-ink-2">
              {fact === null ? 'загружается…' : fact === 'error' ? 'не загрузились' : fact.shifts || 'нет'}
            </dd>
            <dt className="text-ds-muted">Начислено</dt>
            <dd className="m-0 text-ds-ink-2">{monthFactHint(fact, monthPrep) ?? '—'}</dd>
          </dl>
          <p className="mb-0 mt-1.5 text-[11.5px] text-ds-muted">
            Пост задаётся в строке табеля — помесячно.
          </p>
        </div>
      )}
    </SidePanel>
  )
}
