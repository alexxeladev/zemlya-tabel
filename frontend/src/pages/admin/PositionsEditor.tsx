// Секция «Должности (позиции)» в карточке сотрудника — task_positions ч.B.
//
// Совместитель = несколько рабочих мест: у каждого своя должность, тип оплаты
// и база (оклад / ставка за смену / ставка за час), график, отдел, компания и
// коэффициенты. Ровно одно помечено «основная»; удалить основную нельзя —
// сначала назначают основной другую.
//
// Плоские поля карточки (Структура / Трудовая занятость / коэффициенты) — это
// та же ОСНОВНАЯ позиция через compat-аксессоры бэка, поэтому в режиме
// редактирования их заменяет этот редактор: два места ввода одного оклада
// разъехались бы.
//
// УСЛОВИЯ ТРУДА СВОБОДНО НЕ ПРАВЯТСЯ (ADR-001, task_terms_per_field). Тип оплаты
// и ставка, график, коэффициенты и официальное трудоустройство показываются
// группами только для чтения, а «Изменить» спрашивает значения И ДАТУ, с которой
// они действуют. У каждой группы видно запланированное («с 01.12.2026 будет …»),
// поэтому карточка больше не выдаёт будущее за настоящее — именно из-за этого
// 27.09.2026 правка коэффициентов молча уехала в октябрь.

import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  changePositionTerms, createPosition, deletePosition, getPositionTerms, listPositions,
  makePositionPrimary, updatePosition,
} from '../../api/employees'
import { ApiError } from '../../api/client'
import { CLEARING_CANCELLED, withClearingConfirm } from '../../utils/employment'
import {
  GUARD_STAFF_PATH,
  canOpenGuardStaff,
  guardStaffPositionPath,
  isGuardDepartment,
  isGuardPosition,
} from '../../utils/guardStaff'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type {
  Company, Department, EmployeePosition, EmployeePositionInput, PayType,
  PositionTermsState, Schedule, TermChangeRow, TermGroupState,
  TermsChangeInput, WeekendPayType,
} from '../../types/api'
import {
  BASE_FIELD, PAY_TYPE_LABELS, termGroupSummary, termPlannedText, termValueText,
  workplacePeriodText,
} from '../../utils/positionTerms'
import { Button } from '../../components/Button'
import { Confirm } from '../../components/Confirm'
import { Modal } from '../../components/Modal'

// Подтверждение очистки часов показывает браузер; модуль правила к window не
// обращается сам — он собирается и в Node-конфиге тестов (tsconfig.node.json).
const confirmInBrowser = (message: string) => window.confirm(message)


type Draft = {
  title: string
  department_id: string
  schedule_id: string
  company_id: string
  pay_type: PayType
  rate: string
  shift_rate: string
  hour_rate: string
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string
  weekend_fixed_rate: string
  holiday_pay_type: WeekendPayType
  holiday_coefficient: string
  holiday_fixed_rate: string
  overtime_coefficient: string
  has_night_shifts: boolean
  // Период работы на этой должности (task_employment_period)
  hire_date: string
  dismissal_date: string
}

const EMPTY_DRAFT: Draft = {
  title: '', department_id: '', schedule_id: '', company_id: '',
  pay_type: 'salary', rate: '', shift_rate: '', hour_rate: '',
  weekend_pay_type: 'coefficient', weekend_coefficient: '1.5', weekend_fixed_rate: '',
  holiday_pay_type: 'coefficient', holiday_coefficient: '1.5', holiday_fixed_rate: '',
  overtime_coefficient: '1.5', has_night_shifts: false,
  hire_date: '', dismissal_date: '',
}


/** Действия в разделе выглядят одинаково: одна кнопка — один стиль. */
const ACTION_BUTTON =
  'rounded border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-600' +
  ' hover:bg-gray-50 disabled:opacity-50'
const DANGER_BUTTON =
  'rounded border border-red-200 bg-white px-2 py-0.5 text-[11px] text-red-600' +
  ' hover:bg-red-50 disabled:opacity-50'

/** Какие свойства рабочего места меняются СРАЗУ — по строке на каждое. */
type WorkplaceField = 'title' | 'department_id' | 'company_id' | 'has_night_shifts' | 'period'

/** Что правит открытое окно: свойство места или группу условий (с датой). */
type EditTarget =
  | { kind: 'field'; position: EmployeePosition; field: WorkplaceField }
  | { kind: 'terms'; position: EmployeePosition; group: TermGroupState }

const numOrNull = (v: string): number | null => (v === '' ? null : Number(v))
const strOrNull = (v: string): string | null => (v.trim() === '' ? null : v.trim())

/**
 * Тело запроса на СОЗДАНИЕ рабочего места: ввод свободный, истории у нового
 * места ещё нет — все условия заводятся «с начала». Правка идёт иначе: каждое
 * свойство своим окном, а условия — с датой (ADR-001).
 */
function toPayload(d: Draft): EmployeePositionInput {
  return {
    title: strOrNull(d.title),
    department_id: numOrNull(d.department_id),
    company_id: numOrNull(d.company_id),
    has_night_shifts: d.has_night_shifts,
    // Пустое поле — это СНЯТИЕ границы, поэтому шлём null, а не пропускаем:
    // бэк читает payload с exclude_unset, и пропуск означал бы «не менять».
    hire_date: strOrNull(d.hire_date),
    dismissal_date: strOrNull(d.dismissal_date),
    schedule_id: numOrNull(d.schedule_id),
    pay_type: d.pay_type,
    // Поле чужого типа не отправляем — бэк его всё равно обнулит.
    rate: d.pay_type === 'salary' ? strOrNull(d.rate) : null,
    shift_rate: d.pay_type === 'per_shift' ? strOrNull(d.shift_rate) : null,
    hour_rate: d.pay_type === 'hourly' ? strOrNull(d.hour_rate) : null,
    weekend_pay_type: d.weekend_pay_type,
    weekend_coefficient: d.weekend_pay_type === 'coefficient' ? strOrNull(d.weekend_coefficient) : null,
    weekend_fixed_rate: d.weekend_pay_type === 'fixed_rate' ? strOrNull(d.weekend_fixed_rate) : null,
    holiday_pay_type: d.holiday_pay_type,
    holiday_coefficient: d.holiday_pay_type === 'coefficient' ? strOrNull(d.holiday_coefficient) : null,
    holiday_fixed_rate: d.holiday_pay_type === 'fixed_rate' ? strOrNull(d.holiday_fixed_rate) : null,
    overtime_coefficient: strOrNull(d.overtime_coefficient),
  }
}

/**
 * Оклад / ставка одной строкой — то, что видно в свёрнутом списке.
 *
 * Читается из УСЛОВИЙ НА СЕГОДНЯ, если они загружены: колонки позиции —
 * зеркало ПОСЛЕДНЕГО значения, то есть у запланированного повышения показывали
 * бы будущий оклад как текущий. На одном экране с блоком условий это выглядело
 * бы прямым противоречием.
 */
export function positionRateLabel(
  p: EmployeePosition, terms?: PositionTermsState,
): string {
  const group = terms?.groups.find((g) => g.group === 'pay')
  if (group) return termGroupSummary('pay', group.current, terms?.schedule_names)
  const fmt = (v: string | null) =>
    v == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(v))
  if (p.pay_type === 'per_shift') return `${fmt(p.shift_rate)} ₽/смена`
  if (p.pay_type === 'hourly') return `${fmt(p.hour_rate)} ₽/час`
  return `${fmt(p.rate)} ₽/мес`
}

type Props = {
  employeeId: number
  departments: Department[]
  companies: Company[]
  schedules: Schedule[]
  readOnly: boolean
  /** карточка сотрудника перечитывается: плоские поля = основная позиция */
  onChanged?: () => void
}

export function PositionsEditor({
  employeeId, departments, companies, schedules, readOnly, onChanged,
}: Props) {
  const role = useAuthStore((s) => s.user?.role)
  const [positions, setPositions] = useState<EmployeePosition[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // Какая позиция раскрыта на редактирование ('new' — форма добавления)
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [deleteTarget, setDeleteTarget] = useState<EmployeePosition | null>(null)
  // Позиция, у которой раскрыта история условий
  const [historyOf, setHistoryOf] = useState<number | null>(null)
  // Условия каждого рабочего места: действующие сегодня, запланированные, история
  const [terms, setTerms] = useState<Record<number, PositionTermsState>>({})
  // Какие места раскрыты. У совместителя список свойств каждого места занимает
  // пол-экрана, поэтому раскрыто основное, остальные — по клику. Одно место —
  // раскрыто всегда: сворачивать там нечего.
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  // Открытое окно «Изменить» — одно свойство или одна группа условий
  const [dialog, setDialog] = useState<EditTarget | null>(null)
  // Условия — деньги: табельщику и сотруднику их не показываем
  const canSeeTerms = role === 'admin' || role === 'accountant' || role === 'manager'

  const loadTerms = useCallback((list: EmployeePosition[]) => {
    if (!canSeeTerms) return
    // Рабочих мест у человека одно-три, поэтому условия грузятся сразу: в
    // карточке они видны без лишнего клика (ADR-001 — «запланированное должно
    // быть на экране»).
    Promise.all(list.map((p) => getPositionTerms(employeeId, p.id).catch(() => null)))
      .then((states) => {
        const byPosition: Record<number, PositionTermsState> = {}
        states.forEach((st) => { if (st) byPosition[st.position_id] = st })
        setTerms(byPosition)
      })
  }, [employeeId, canSeeTerms])

  const load = useCallback(() => {
    setLoading(true)
    listPositions(employeeId)
      .then((list) => {
        setPositions(list)
        loadTerms(list)
        setExpanded(Object.fromEntries(
          list.map((p) => [p.id, list.length === 1 || p.is_primary]),
        ))
      })
      .catch(() => toast.error('Не удалось загрузить должности'))
      .finally(() => setLoading(false))
  }, [employeeId, loadTerms])

  useEffect(load, [load])

  const refresh = () => { load(); onChanged?.() }

  const startAdd = () => {
    setDraft({
      ...EMPTY_DRAFT,
      // Совместительство обычно в той же компании/графике не заводят, но отдел
      // по умолчанию берём от основной — так реже забывают его указать.
      department_id: positions[0]?.department_id != null ? String(positions[0].department_id) : '',
    })
    setEditing('new')
  }

  const toggleHistory = (p: EmployeePosition) => {
    setHistoryOf(historyOf === p.id ? null : p.id)
  }

  /** Сохранить изменение группы условий: значения и дата уходят как есть. */
  const saveTerms = async (change: TermsChangeInput) => {
    if (dialog?.kind !== 'terms') return
    setBusy(true)
    try {
      const state = await changePositionTerms(employeeId, dialog.position.id, [change])
      setTerms((prev) => ({ ...prev, [state.position_id]: state }))
      setDialog(null)
      toast.success('Условия изменены')
      // Зеркало позиции могло сдвинуться — перечитываем список и карточку.
      refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Не удалось изменить условия')
    } finally {
      setBusy(false)
    }
  }

  /**
   * Сохранить свойство, которое меняется сразу. Шлём ТОЛЬКО его: бэк читает
   * тело с `exclude_unset`, и лишние поля были бы правкой того, что не трогали.
   */
  const saveField = async (payload: EmployeePositionInput) => {
    if (dialog?.kind !== 'field') return
    const positionId = dialog.position.id
    setBusy(true)
    try {
      // Сдвиг дат периода работы может выкинуть уже проставленные часы за
      // границу. Бэк не удаляет их молча: отвечает 409 с числами, а сохранение
      // откатывает. Спрашиваем и повторяем с подтверждением.
      const saved = await withClearingConfirm((confirm) =>
        updatePosition(employeeId, positionId, payload, confirm), confirmInBrowser)
      if (saved === CLEARING_CANCELLED) {
        toast.info('Сохранение отменено — ничего не изменилось')
        return
      }
      setDialog(null)
      toast.success('Сохранено')
      refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка сохранения')
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    try {
      await createPosition(employeeId, toPayload(draft))
      toast.success('Должность добавлена')
      setEditing(null)
      if (historyOf != null) setHistoryOf(null)
      refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка сохранения')
    } finally {
      setBusy(false)
    }
  }

  const makePrimary = async (p: EmployeePosition) => {
    setBusy(true)
    try {
      setPositions(await makePositionPrimary(employeeId, p.id))
      toast.success(`Основная должность: ${p.display_title}`)
      onChanged?.()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
    } finally {
      setBusy(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setBusy(true)
    try {
      const { result } = await deletePosition(employeeId, deleteTarget.id)
      toast.success(
        result === 'deactivated'
          ? 'На должности есть часы или начисления — она отключена, история сохранена'
          : 'Должность удалена',
      )
      setDeleteTarget(null)
      refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка удаления')
      setDeleteTarget(null)
    } finally {
      setBusy(false)
    }
  }

  if (loading) return null

  const activeCount = positions.filter((p) => p.is_active).length

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          Должности (позиции)
        </p>
        {!readOnly && editing === null && (
          <Button type="button" variant="secondary" size="sm" onClick={startAdd}>
            + Совместительство
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-2">
        {positions.map((p) => (
          <div
            key={p.id}
            className={`rounded-lg border px-3 py-2 ${
              p.is_primary ? 'border-blue-200 bg-blue-50/40' : 'border-gray-200'
            } ${p.is_active ? '' : 'opacity-60'}`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setExpanded((prev) => ({ ...prev, [p.id]: !prev[p.id] }))}
                aria-expanded={!!expanded[p.id]}
                className="flex items-center gap-1.5 text-sm font-medium text-gray-800 hover:text-blue-700"
                title={expanded[p.id] ? 'Свернуть рабочее место' : 'Развернуть рабочее место'}
              >
                <span className="text-[10px] text-gray-400">{expanded[p.id] ? '▾' : '▸'}</span>
                {p.display_title}
              </button>
              {p.is_primary && (
                <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                  основная
                </span>
              )}
              {!p.is_active && (
                <span className="rounded-full bg-gray-200 px-2 py-0.5 text-[10px] font-medium text-gray-600">
                  отключена
                </span>
              )}
              <span className="font-mono text-xs text-gray-700">
                {positionRateLabel(p, terms[p.id])}
              </span>
              <span className="flex-1" />
              {/* Рабочее место охраны ведёт вахта (task_guard_ownership): здесь
                  только просмотр, бэк правку всё равно отклонит. Кому вкладка
                  закрыта (бухгалтер) — текст без ссылки. */}
              {isGuardPosition(p) &&
                (canOpenGuardStaff(role) ? (
                  <Link
                    to={guardStaffPositionPath(p.id)}
                    className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-200"
                    title="Рабочее место охранного подразделения правится в настройках вахты"
                  >
                    ведётся в модуле «Вахта» →
                  </Link>
                ) : (
                  <span
                    className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800"
                    title="Правят администратор и руководитель охраны в настройках вахты"
                  >
                    ведётся в модуле «Вахта»
                  </span>
                ))}
              {!readOnly && editing === null && (
                <div className="flex gap-1.5">
                  {!p.is_primary && p.is_active && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => makePrimary(p)}
                      className={ACTION_BUTTON}
                      title="Сделать основной: с неё платятся отпускные, больничные и займ"
                    >
                      Сделать основной
                    </button>
                  )}
                  {canSeeTerms && (
                    <button
                      type="button"
                      onClick={() => toggleHistory(p)}
                      aria-expanded={historyOf === p.id}
                      className={ACTION_BUTTON}
                      title="Какие условия действовали и с какой даты"
                    >
                      История условий
                    </button>
                  )}
                  {!p.is_primary && activeCount > 1 && !isGuardPosition(p) && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setDeleteTarget(p)}
                      className={DANGER_BUTTON}
                    >
                      Удалить
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* Свойства рабочего места ОДНИМ списком: подпись — значение —
                «Изменить». Две зоны, и разница между ними видна глазами: что
                меняется сразу, а что действует с даты (ADR-001). */}
            {expanded[p.id] && (
            <WorkplaceRows
              position={p}
              terms={canSeeTerms ? terms[p.id] : undefined}
              showTerms={canSeeTerms}
              departments={departments}
              companies={companies}
              canEdit={!readOnly && !isGuardPosition(p)}
              onEdit={setDialog}
            />
            )}

            {expanded[p.id] && historyOf === p.id && (
              <TermsHistory state={terms[p.id]} />
            )}
          </div>
        ))}

        {editing === 'new' && (
          <div className="rounded-lg border border-dashed border-blue-300 px-3 py-2">
            <p className="text-sm font-medium text-gray-800">Новое рабочее место</p>
            <p className="text-[11px] text-gray-500">
              Расчёт по нему идёт отдельно: свой оклад, график и норма. «К выплате»
              с разных позиций не суммируется — платят разные компании.
            </p>
            {/* Новое рабочее место охраны заводят в вахте — здесь его отделов нет. */}
            <PositionForm
              draft={draft}
              setDraft={setDraft}
              departments={departments.filter((d) => !isGuardDepartment(d))}
              companies={companies}
              schedules={schedules}
              busy={busy}
              onSave={save}
              onCancel={() => setEditing(null)}
            />
            <p className="mt-1 text-[11px] text-gray-500">
              Рабочее место в охране оформляется в{' '}
              <Link to={GUARD_STAFF_PATH} className="underline">модуле «Вахта»</Link>.
            </p>
          </div>
        )}
      </div>

      {positions.length > 1 && (
        <p className="mt-2 text-[11px] text-gray-400">
          Отпускные, больничные и погашение займа начисляются только с основной позиции —
          иначе отпуск оплачивался бы с каждого рабочего места.
        </p>
      )}

      {dialog?.kind === 'terms' && (
        <TermsChangeDialog
          group={dialog.group}
          state={terms[dialog.position.id]}
          positionTitle={dialog.position.display_title}
          schedules={schedules}
          busy={busy}
          onCancel={() => setDialog(null)}
          onSave={saveTerms}
        />
      )}

      {dialog?.kind === 'field' && (
        <FieldDialog
          position={dialog.position}
          field={dialog.field}
          departments={departments}
          companies={companies}
          busy={busy}
          onCancel={() => setDialog(null)}
          onSave={saveField}
        />
      )}

      <Confirm
        isOpen={!!deleteTarget}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Удалить должность"
        message={
          `Удалить «${deleteTarget?.display_title}»? Если по ней уже есть часы или ` +
          'начисления, она будет отключена, а история сохранится.'
        }
        danger
      />
    </div>
  )
}

// ── Форма одной позиции ───────────────────────────────────────────────────────

function PositionForm({
  draft, setDraft, departments, companies, schedules, busy, onSave, onCancel,
}: {
  draft: Draft
  setDraft: (d: Draft) => void
  departments: Department[]
  companies: Company[]
  schedules: Schedule[]
  busy: boolean
  onSave: () => void
  onCancel: () => void
}) {
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft({ ...draft, [key]: value })

  const base = BASE_FIELD[draft.pay_type]
  const inputCls =
    'rounded-lg border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-gray-200 pt-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">Должность</span>
          <input
            value={draft.title}
            onChange={(e) => set('title', e.target.value)}
            placeholder="Инженер"
            className={inputCls}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">Отдел</span>
          <select
            value={draft.department_id}
            onChange={(e) => set('department_id', e.target.value)}
            className={inputCls}
          >
            <option value="">— без отдела —</option>
            {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
          {isGuardDepartment(departments.find((d) => String(d.id) === draft.department_id)) && (
            <span className="text-[11px] leading-tight text-amber-700">
              Подразделение охраны: после сохранения рабочее место ведётся в
              модуле «Вахта», график и коэффициенты перестают учитываться.
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">График</span>
          <select
            value={draft.schedule_id}
            onChange={(e) => set('schedule_id', e.target.value)}
            className={inputCls}
          >
            <option value="">— не указан —</option>
            {schedules.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">Основная компания</span>
          <select
            value={draft.company_id}
            onChange={(e) => set('company_id', e.target.value)}
            className={inputCls}
          >
            <option value="">— не указана —</option>
            {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
      </div>

      {/* Тип оплаты и его база — взаимоисключающие поля */}
      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-gray-700">Тип оплаты</span>
        <div className="flex flex-wrap gap-4 text-sm">
          {(Object.keys(PAY_TYPE_LABELS) as PayType[]).map((t) => (
            <label key={t} className="flex cursor-pointer items-center gap-1.5 text-gray-700">
              <input
                type="radio"
                checked={draft.pay_type === t}
                onChange={() => set('pay_type', t)}
              />
              {PAY_TYPE_LABELS[t]}
            </label>
          ))}
        </div>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">{base.label}</span>
          <input
            value={draft[base.key]}
            onChange={(e) => set(base.key, e.target.value)}
            placeholder={base.placeholder}
            className={inputCls}
          />
        </label>
        {draft.pay_type === 'hourly' && (
          <p className="text-[11px] text-gray-400">
            Платим за фактические часы. Отпускные и больничные по почасовой не начисляются;
            переработка считается по дням — сверх дневной нормы смены.
          </p>
        )}
        {draft.pay_type === 'per_shift' && (
          <p className="text-[11px] text-gray-400">
            База — смены плановых дней графика. Смена в выходной/праздник оплачивается
            ставкой × коэффициент (целиком за смену), переработка — по часам сверх смены.
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <CoefficientBlock
          label="Оплата вне графика (свой выходной)"
          payType={draft.weekend_pay_type}
          coefficient={draft.weekend_coefficient}
          fixedRate={draft.weekend_fixed_rate}
          onPayType={(v) => set('weekend_pay_type', v)}
          onCoefficient={(v) => set('weekend_coefficient', v)}
          onFixedRate={(v) => set('weekend_fixed_rate', v)}
          inputCls={inputCls}
        />
        <CoefficientBlock
          label="Оплата праздничных"
          payType={draft.holiday_pay_type}
          coefficient={draft.holiday_coefficient}
          fixedRate={draft.holiday_fixed_rate}
          onPayType={(v) => set('holiday_pay_type', v)}
          onCoefficient={(v) => set('holiday_coefficient', v)}
          onFixedRate={(v) => set('holiday_fixed_rate', v)}
          inputCls={inputCls}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">Коэффициент переработки</span>
          <input
            value={draft.overtime_coefficient}
            onChange={(e) => set('overtime_coefficient', e.target.value)}
            placeholder="1.5"
            className={inputCls}
          />
        </label>
        <div className="flex flex-col gap-1">
          <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-gray-700">
            <input
              type="checkbox"
              checked={draft.has_night_shifts}
              onChange={(e) => set('has_night_shifts', e.target.checked)}
            />
            Ночные смены
          </label>
          {/* Ставка здесь НЕ задаётся: она вычисляется из фонда отдела
              (фонд / календарные дни месяца) — task_night_shifts_rework. */}
          {draft.has_night_shifts && (
            <p className="text-[11px] leading-tight text-gray-500">
              Ставка считается из фонда ночных смен отдела
              (фонд ÷ календарные дни месяца) и вручную не задаётся.
            </p>
          )}
        </div>
      </div>

      {/* Период работы на этой должности (task_employment_period).
          Даты берутся с ПОЗИЦИИ: у совместителя одна работа может быть закрыта,
          а вторая продолжаться. Границы включительные, пустое поле — границы
          нет. Действуют в пересечении с датами человека («Работа в компании» в
          карточке): увольнение кадровиком закрывает все рабочие места разом.
          На is_active это не влияет — строка в табеле остаётся, дни после даты
          просто заблокированы. */}
      <div className="rounded-lg border border-gray-200 bg-gray-50/60 p-3">
        <div className="mb-2 text-xs font-semibold text-gray-700">
          Период на этой должности
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">Принят</span>
            <input
              type="date"
              value={draft.hire_date}
              onChange={(e) => set('hire_date', e.target.value)}
              className={inputCls}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">Уволен</span>
            <input
              type="date"
              value={draft.dismissal_date}
              onChange={(e) => set('dismissal_date', e.target.value)}
              className={inputCls}
            />
          </label>
        </div>
        <p className="mt-2 text-[11px] leading-tight text-gray-500">
          Табель заполняется только внутри периода, включая обе даты. Пустое
          поле — без ограничения. Если после сдвига даты за границей останутся
          проставленные часы, их удаление придётся подтвердить.
        </p>
      </div>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Отмена</Button>
        <Button type="button" size="sm" onClick={onSave} disabled={busy}>Сохранить</Button>
      </div>
    </div>
  )
}

// ── Свойства рабочего места: один список, одна кнопка на строку ───────────────

/** Строка списка: подпись — значение — «Изменить». Сетка одна на все строки,
 *  поэтому кнопки стоят в колонку, как бы длинно ни читалось значение. */
function PropertyRow({
  label, value, note, canEdit, onEdit,
}: {
  label: string
  value: string
  note?: ReactNode
  canEdit: boolean
  onEdit: () => void
}) {
  return (
    <div className="grid grid-cols-[12.5rem_minmax(0,1fr)_auto] items-baseline gap-x-3 py-1.5">
      <span className="text-[11px] text-gray-500">{label}</span>
      <span className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-xs text-gray-800">{value}</span>
        {note}
      </span>
      {canEdit ? (
        <button type="button" onClick={onEdit} className={ACTION_BUTTON}>
          Изменить
        </button>
      ) : (
        <span />
      )}
    </div>
  )
}

function ZoneHeading({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 border-b border-gray-200 bg-gray-50/60 px-3 py-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
        {title}
      </span>
      {hint && <span className="text-[11px] text-gray-400">{hint}</span>}
    </div>
  )
}

/**
 * Рабочее место одним списком свойств. Две зоны, и разница между ними видна
 * глазами: что меняется сразу, а что действует с даты. Значения второй зоны —
 * НА СЕГОДНЯ, запланированное дальше стоит рядом пометкой, поэтому карточка не
 * выдаёт будущее за настоящее (ADR-001).
 */
function WorkplaceRows({
  position, terms, showTerms, departments, companies, canEdit, onEdit,
}: {
  position: EmployeePosition
  terms?: PositionTermsState
  showTerms: boolean
  departments: Department[]
  companies: Company[]
  canEdit: boolean
  onEdit: (target: EditTarget) => void
}) {
  const nameOf = <T extends { id: number; name: string }>(list: T[], id: number | null) =>
    id == null ? 'не указан' : list.find((x) => x.id === id)?.name ?? `#${id}`
  const field = (f: WorkplaceField) => () => onEdit({ kind: 'field', position, field: f })

  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-gray-200">
      <ZoneHeading title="Меняется сразу" />
      <div className="divide-y divide-gray-100 px-3">
        <PropertyRow
          label="Должность"
          value={position.title || 'не указана'}
          canEdit={canEdit}
          onEdit={field('title')}
        />
        <PropertyRow
          label="Отдел"
          value={nameOf(departments, position.department_id)}
          canEdit={canEdit}
          onEdit={field('department_id')}
        />
        <PropertyRow
          label="Основная компания"
          value={nameOf(companies, position.company_id)}
          canEdit={canEdit}
          onEdit={field('company_id')}
        />
        <PropertyRow
          label="Ночные смены"
          value={position.has_night_shifts ? 'да' : 'нет'}
          note={position.has_night_shifts ? (
            <span className="text-[11px] text-gray-400">
              ставка — из фонда отдела
            </span>
          ) : undefined}
          canEdit={canEdit}
          onEdit={field('has_night_shifts')}
        />
        <PropertyRow
          label="Период работы"
          value={workplacePeriodText(position.hire_date, position.dismissal_date)}
          note={
            <span className="text-[11px] text-gray-400">
              табель заполняется только внутри него
            </span>
          }
          canEdit={canEdit}
          onEdit={field('period')}
        />
      </div>

      {showTerms && (
        <>
          <ZoneHeading
            title="Действует с даты"
            hint="изменение спрашивает, с какого числа"
          />
          <div className="divide-y divide-gray-100 px-3">
            {terms == null ? (
              <p className="py-1.5 text-[11px] text-gray-400">Условия загружаются…</p>
            ) : (
              terms.groups.map((g) => (
                <PropertyRow
                  key={g.group}
                  label={g.label}
                  value={termGroupSummary(g.group, g.current, terms.schedule_names)}
                  note={g.planned.map((pl) => (
                    <span
                      key={pl.effective_from}
                      className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800"
                      title="Изменение уже заведено и начнёт действовать с этой даты"
                    >
                      {termPlannedText(g.group, g.current, pl, terms.schedule_names)}
                    </span>
                  ))}
                  canEdit={canEdit}
                  onEdit={() => onEdit({ kind: 'terms', position, group: g })}
                />
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ── Окно правки свойства, которое меняется сразу ──────────────────────────────

const FIELD_TITLES: Record<WorkplaceField, string> = {
  title: 'Должность',
  department_id: 'Отдел',
  company_id: 'Основная компания',
  has_night_shifts: 'Ночные смены',
  period: 'Период работы',
}

/**
 * Правка идёт тем же окном, что и условия, — разница ровно одна: здесь не
 * спрашивается дата, потому что эти свойства действуют сразу.
 */
function FieldDialog({
  position, field, departments, companies, busy, onCancel, onSave,
}: {
  position: EmployeePosition
  field: WorkplaceField
  departments: Department[]
  companies: Company[]
  busy: boolean
  onCancel: () => void
  onSave: (payload: EmployeePositionInput) => void
}) {
  const [title, setTitle] = useState(position.title ?? '')
  const [departmentId, setDepartmentId] = useState(
    position.department_id != null ? String(position.department_id) : '',
  )
  const [companyId, setCompanyId] = useState(
    position.company_id != null ? String(position.company_id) : '',
  )
  const [night, setNight] = useState(position.has_night_shifts)
  const [hire, setHire] = useState(position.hire_date ?? '')
  const [dismissal, setDismissal] = useState(position.dismissal_date ?? '')

  const inputCls =
    'rounded-lg border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

  const submit = () => {
    if (field === 'title') return onSave({ title: strOrNull(title) })
    if (field === 'department_id') return onSave({ department_id: numOrNull(departmentId) })
    if (field === 'company_id') return onSave({ company_id: numOrNull(companyId) })
    if (field === 'has_night_shifts') return onSave({ has_night_shifts: night })
    // Пустое поле — это СНЯТИЕ границы, поэтому шлём null, а не пропускаем.
    return onSave({ hire_date: strOrNull(hire), dismissal_date: strOrNull(dismissal) })
  }

  const targetDepartment = departments.find((d) => String(d.id) === departmentId)

  return (
    <Modal
      isOpen
      onClose={onCancel}
      title={FIELD_TITLES[field]}
      actions={
        <>
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Отмена</Button>
          <Button type="button" size="sm" onClick={submit} disabled={busy}>Сохранить</Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-[11px] text-gray-500">
          Рабочее место: {position.display_title}
        </p>

        {field === 'title' && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">Должность</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Инженер"
              className={inputCls}
            />
          </label>
        )}

        {field === 'department_id' && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">Отдел</span>
            <select
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
              className={inputCls}
            >
              <option value="">— без отдела —</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            {isGuardDepartment(targetDepartment) && (
              <span className="text-[11px] leading-tight text-amber-700">
                Подразделение охраны: после сохранения рабочее место ведётся в
                модуле «Вахта», график и коэффициенты перестают учитываться.
              </span>
            )}
          </label>
        )}

        {field === 'company_id' && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">Основная компания</span>
            <select
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              className={inputCls}
            >
              <option value="">— не указана —</option>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <span className="text-[11px] leading-tight text-gray-500">
              На расчёт она не влияет: юрлица берутся из часов табеля и процентов
              распределения.
            </span>
          </label>
        )}

        {field === 'has_night_shifts' && (
          <>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={night}
                onChange={(e) => setNight(e.target.checked)}
              />
              Рабочее место выходит в ночные смены
            </label>
            <span className="text-[11px] leading-tight text-gray-500">
              Ставка считается из фонда ночных смен отдела (фонд ÷ календарные дни
              месяца) и вручную не задаётся.
            </span>
          </>
        )}

        {field === 'period' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-gray-700">Принят</span>
                <input
                  type="date"
                  value={hire}
                  onChange={(e) => setHire(e.target.value)}
                  className={inputCls}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-gray-700">Уволен</span>
                <input
                  type="date"
                  value={dismissal}
                  onChange={(e) => setDismissal(e.target.value)}
                  className={inputCls}
                />
              </label>
            </div>
            <span className="text-[11px] leading-tight text-gray-500">
              Табель заполняется только внутри периода, включая обе даты. Пустое
              поле — без ограничения. Если после сдвига даты за границей останутся
              проставленные часы, их удаление придётся подтвердить.
            </span>
          </>
        )}
      </div>
    </Modal>
  )
}

// ── Диалог «Изменить»: значения группы + ОБЯЗАТЕЛЬНАЯ дата ────────────────────

const strOrNullValue = (v: unknown): string | null => {
  const s = String(v ?? '').trim()
  return s === '' ? null : s
}

/**
 * Одна группа, одна дата, один запрос. Бэкенд ничего не диффит: что показано в
 * диалоге — то и будет записано с этой даты, даже если значение не изменилось
 * (это законная правка задним числом, из-за невозможности которой и затевался
 * ADR-001).
 */
function TermsChangeDialog({
  group, state, positionTitle, schedules, busy, onCancel, onSave,
}: {
  group: TermGroupState
  state: PositionTermsState
  positionTitle: string
  schedules: Schedule[]
  busy: boolean
  onCancel: () => void
  onSave: (change: TermsChangeInput) => void
}) {
  const [day, setDay] = useState(state.default_effective_from)
  const [values, setValues] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    group.fields.forEach((f) => {
      const v = group.current[f]
      out[f] = v == null || typeof v === 'boolean' ? '' : String(v)
    })
    return out
  })
  const [isOfficial, setIsOfficial] = useState(Boolean(group.current.is_official))
  const set = (field: string, value: string) =>
    setValues((prev) => ({ ...prev, [field]: value }))

  const inputCls =
    'rounded-lg border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

  const payType = (values.pay_type as PayType) || 'salary'
  const base = BASE_FIELD[payType]

  const submit = () => {
    const change: TermsChangeInput = { group: group.group, effective_from: day }
    if (group.group === 'pay') {
      change.pay_type = payType
      change[base.key] = strOrNullValue(values[base.key])
    } else if (group.group === 'schedule') {
      change.schedule_id = values.schedule_id ? Number(values.schedule_id) : null
    } else if (group.group === 'weekend') {
      const kind = (values.weekend_pay_type as WeekendPayType) || 'coefficient'
      change.weekend_pay_type = kind
      if (kind === 'coefficient') change.weekend_coefficient = strOrNullValue(values.weekend_coefficient)
      else change.weekend_fixed_rate = strOrNullValue(values.weekend_fixed_rate)
    } else if (group.group === 'holiday') {
      const kind = (values.holiday_pay_type as WeekendPayType) || 'coefficient'
      change.holiday_pay_type = kind
      if (kind === 'coefficient') change.holiday_coefficient = strOrNullValue(values.holiday_coefficient)
      else change.holiday_fixed_rate = strOrNullValue(values.holiday_fixed_rate)
    } else if (group.group === 'overtime') {
      change.overtime_coefficient = strOrNullValue(values.overtime_coefficient)
    } else if (group.group === 'official') {
      change.is_official = isOfficial
      change.official_salary = isOfficial ? strOrNullValue(values.official_salary) : null
    }
    onSave(change)
  }

  return (
    <Modal
      isOpen
      onClose={onCancel}
      title={group.label}
      actions={
        <>
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Отмена</Button>
          <Button type="button" size="sm" onClick={submit} disabled={busy || !day}>
            Сохранить
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-[11px] leading-tight text-gray-500">
          Рабочее место: {positionTitle} · сейчас{' '}
          <span className="font-mono text-gray-700">
            {termGroupSummary(group.group, group.current, state.schedule_names)}
          </span>
        </p>

        {group.group === 'pay' && (
          <>
            <div className="flex flex-wrap gap-4 text-sm">
              {(Object.keys(PAY_TYPE_LABELS) as PayType[]).map((t) => (
                <label key={t} className="flex cursor-pointer items-center gap-1.5 text-gray-700">
                  <input
                    type="radio"
                    checked={payType === t}
                    onChange={() => set('pay_type', t)}
                  />
                  {PAY_TYPE_LABELS[t]}
                </label>
              ))}
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-gray-700">{base.label}</span>
              <input
                value={values[base.key] ?? ''}
                onChange={(e) => set(base.key, e.target.value)}
                placeholder={base.placeholder}
                className={inputCls}
              />
            </label>
            <p className="text-[11px] text-gray-400">
              Базы разных типов оплаты взаимоисключающие: при смене типа чужая
              гасится с этой же даты.
            </p>
          </>
        )}

        {group.group === 'schedule' && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">График</span>
            <select
              value={values.schedule_id ?? ''}
              onChange={(e) => set('schedule_id', e.target.value)}
              className={inputCls}
            >
              <option value="">— не указан —</option>
              {schedules.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}

        {(group.group === 'weekend' || group.group === 'holiday') && (
          <CoefficientBlock
            label="Вид оплаты"
            payType={(values[`${group.group}_pay_type`] as WeekendPayType) || 'coefficient'}
            coefficient={values[`${group.group}_coefficient`] ?? ''}
            fixedRate={values[`${group.group}_fixed_rate`] ?? ''}
            onPayType={(v) => set(`${group.group}_pay_type`, v)}
            onCoefficient={(v) => set(`${group.group}_coefficient`, v)}
            onFixedRate={(v) => set(`${group.group}_fixed_rate`, v)}
            inputCls={inputCls}
          />
        )}

        {group.group === 'overtime' && (
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-gray-700">Коэффициент переработки</span>
            <input
              value={values.overtime_coefficient ?? ''}
              onChange={(e) => set('overtime_coefficient', e.target.value)}
              placeholder="1.5"
              className={`${inputCls} w-32`}
            />
          </label>
        )}

        {group.group === 'official' && (
          <>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={isOfficial}
                onChange={(e) => setIsOfficial(e.target.checked)}
              />
              Официально устроен
            </label>
            {isOfficial && (
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-gray-700">
                  Официальная зарплата на руки (₽/мес)
                </span>
                <input
                  value={values.official_salary ?? ''}
                  onChange={(e) => set('official_salary', e.target.value)}
                  placeholder="50000"
                  className={inputCls}
                />
              </label>
            )}
            <p className="text-[11px] text-gray-400">
              Сумма на карту после НДФЛ, а не оклад по договору. Признак снят —
              зарплата не хранится.
            </p>
          </>
        )}

        <label className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-700">
            С какой даты действует изменение
          </span>
          <input
            type="date"
            value={day}
            onChange={(e) => setDay(e.target.value)}
            className={`${inputCls} w-44`}
          />
          <span className="text-[11px] leading-tight text-gray-500">
            Дни до этой даты считаются по прежним условиям: повышение с середины
            месяца оплачивается с даты повышения, а не за весь месяц. По
            умолчанию — 1-е число следующего месяца. Дату в закрытом месяце или
            месяце на проверке выбрать нельзя.
          </span>
        </label>
      </div>
    </Modal>
  )
}

// ── История условий: строка на ПОЛЕ ───────────────────────────────────────────

/**
 * «Оклад: 60 000 ₽ с начала → 90 000 ₽ с 15.05.2026». Поля без изменений не
 * показываются: их базовое значение и так стоит в блоке условий.
 */
function TermsHistory({ state }: { state?: PositionTermsState }) {
  if (state == null) {
    return <p className="mt-2 text-[11px] text-gray-400">Загрузка истории…</p>
  }
  const oldestFirst = [...state.changes].reverse()
  const byField = new Map<string, TermChangeRow[]>()
  oldestFirst.forEach((c) => {
    const rows = byField.get(c.field)
    if (rows) rows.push(c)
    else byField.set(c.field, [c])
  })
  const changed = [...byField.entries()].filter(
    ([, rows]) => rows.some((r) => r.effective_from != null),
  )
  if (changed.length === 0) {
    return (
      <p className="mt-2 rounded-lg border border-gray-200 bg-gray-50/50 p-2 text-[11px] text-gray-500">
        Условия не менялись: всё действует с начала работы на этом месте.
      </p>
    )
  }
  return (
    <div className="mt-2 flex flex-col gap-1.5 rounded-lg border border-gray-200 bg-gray-50/50 p-2">
      {changed.map(([field, rows]) => (
        <div key={field} className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
          <span className="w-48 shrink-0 text-gray-500">{rows[0].field_label}</span>
          {rows.map((r, i) => (
            <span key={`${r.effective_from ?? 'base'}-${i}`} className="flex items-baseline gap-1">
              {i > 0 && <span className="text-gray-400">→</span>}
              <span className="font-mono text-gray-800">
                {termValueText(r.field, r.value, state.schedule_names)}
              </span>
              <span className="text-gray-500">{r.effective_label}</span>
              {r.created_by_name && (
                <span className="text-gray-400" title="Кто внёс изменение">
                  ({r.created_by_name})
                </span>
              )}
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

function CoefficientBlock({
  label, payType, coefficient, fixedRate, onPayType, onCoefficient, onFixedRate, inputCls,
}: {
  label: string
  payType: WeekendPayType
  coefficient: string
  fixedRate: string
  onPayType: (v: WeekendPayType) => void
  onCoefficient: (v: string) => void
  onFixedRate: (v: string) => void
  inputCls: string
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-gray-700">{label}</span>
      <select
        value={payType}
        onChange={(e) => onPayType(e.target.value as WeekendPayType)}
        className={inputCls}
      >
        <option value="coefficient">По коэффициенту</option>
        <option value="fixed_rate">Фикс. ставка за час</option>
      </select>
      {payType === 'coefficient' ? (
        <input
          value={coefficient}
          onChange={(e) => onCoefficient(e.target.value)}
          placeholder="1.5"
          className={inputCls}
        />
      ) : (
        <input
          value={fixedRate}
          onChange={(e) => onFixedRate(e.target.value)}
          placeholder="740"
          className={inputCls}
        />
      )}
    </div>
  )
}
