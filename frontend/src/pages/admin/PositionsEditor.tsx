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

import { useCallback, useEffect, useState } from 'react'
import {
  createPosition, deletePosition, listPositions, makePositionPrimary, updatePosition,
} from '../../api/employees'
import { ApiError } from '../../api/client'
import { toast } from '../../store/toasts'
import type {
  Company, Department, EmployeePosition, EmployeePositionInput, PayType, Schedule, WeekendPayType,
} from '../../types/api'
import { Button } from '../../components/Button'
import { Confirm } from '../../components/Confirm'

const PAY_TYPE_LABELS: Record<PayType, string> = {
  salary: 'Окладная',
  per_shift: 'Посменная',
  hourly: 'Почасовая',
}

// Тип оплаты → подпись и плейсхолдер поля базы. Поля взаимоисключающие:
// бэк гасит чужие при смене типа, поэтому и в форме показываем ровно одно.
const BASE_FIELD: Record<PayType, { key: 'rate' | 'shift_rate' | 'hour_rate'; label: string; placeholder: string }> = {
  salary: { key: 'rate', label: 'Оклад (₽/мес)', placeholder: '50000' },
  per_shift: { key: 'shift_rate', label: 'Ставка за смену (₽)', placeholder: '2500' },
  hourly: { key: 'hour_rate', label: 'Ставка за час (₽)', placeholder: '450' },
}

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
  night_rate: string
}

const EMPTY_DRAFT: Draft = {
  title: '', department_id: '', schedule_id: '', company_id: '',
  pay_type: 'salary', rate: '', shift_rate: '', hour_rate: '',
  weekend_pay_type: 'coefficient', weekend_coefficient: '1.5', weekend_fixed_rate: '',
  holiday_pay_type: 'coefficient', holiday_coefficient: '1.5', holiday_fixed_rate: '',
  overtime_coefficient: '1.5', has_night_shifts: false, night_rate: '',
}

function toDraft(p: EmployeePosition): Draft {
  return {
    title: p.title ?? '',
    department_id: p.department_id != null ? String(p.department_id) : '',
    schedule_id: p.schedule_id != null ? String(p.schedule_id) : '',
    company_id: p.company_id != null ? String(p.company_id) : '',
    pay_type: p.pay_type,
    rate: p.rate ?? '',
    shift_rate: p.shift_rate ?? '',
    hour_rate: p.hour_rate ?? '',
    weekend_pay_type: p.weekend_pay_type,
    weekend_coefficient: p.weekend_coefficient ?? '',
    weekend_fixed_rate: p.weekend_fixed_rate ?? '',
    holiday_pay_type: p.holiday_pay_type,
    holiday_coefficient: p.holiday_coefficient ?? '',
    holiday_fixed_rate: p.holiday_fixed_rate ?? '',
    overtime_coefficient: p.overtime_coefficient ?? '1.5',
    has_night_shifts: p.has_night_shifts,
    night_rate: p.night_rate ?? '',
  }
}

const numOrNull = (v: string): number | null => (v === '' ? null : Number(v))
const strOrNull = (v: string): string | null => (v.trim() === '' ? null : v.trim())

function toPayload(d: Draft): EmployeePositionInput {
  return {
    title: strOrNull(d.title),
    department_id: numOrNull(d.department_id),
    schedule_id: numOrNull(d.schedule_id),
    company_id: numOrNull(d.company_id),
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
    has_night_shifts: d.has_night_shifts,
    night_rate: d.has_night_shifts ? strOrNull(d.night_rate) : null,
  }
}

/** Оклад / ставка позиции одной строкой — то, что видно в свёрнутом списке. */
export function positionRateLabel(p: EmployeePosition): string {
  const fmt = (v: string | null) =>
    v == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(v))
  if (p.pay_type === 'per_shift') return `${fmt(p.shift_rate)} ₽/смена`
  if (p.pay_type === 'hourly') return `${fmt(p.hour_rate)} ₽/час`
  return `${fmt(p.rate)} ₽/мес`
}

function coeffLabel(p: EmployeePosition): string {
  if (p.weekend_pay_type === 'fixed_rate') {
    return p.weekend_fixed_rate ? `${Number(p.weekend_fixed_rate)} ₽/ч` : '—'
  }
  return `×${p.weekend_coefficient != null ? Number(p.weekend_coefficient) : 1.5}`
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
  const [positions, setPositions] = useState<EmployeePosition[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // Какая позиция раскрыта на редактирование ('new' — форма добавления)
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [deleteTarget, setDeleteTarget] = useState<EmployeePosition | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    listPositions(employeeId)
      .then(setPositions)
      .catch(() => toast.error('Не удалось загрузить должности'))
      .finally(() => setLoading(false))
  }, [employeeId])

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

  const startEdit = (p: EmployeePosition) => {
    setDraft(toDraft(p))
    setEditing(p.id)
  }

  const save = async () => {
    setBusy(true)
    try {
      if (editing === 'new') {
        await createPosition(employeeId, toPayload(draft))
        toast.success('Должность добавлена')
      } else if (typeof editing === 'number') {
        await updatePosition(employeeId, editing, toPayload(draft))
        toast.success('Должность сохранена')
      }
      setEditing(null)
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

  const nameOf = <T extends { id: number; name: string }>(list: T[], id: number | null) =>
    id == null ? '—' : list.find((x) => x.id === id)?.name ?? `#${id}`

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
              <span className="text-sm font-medium text-gray-800">{p.display_title}</span>
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
              <span className="text-xs text-gray-500">{PAY_TYPE_LABELS[p.pay_type]}</span>
              <span className="font-mono text-xs text-gray-700">{positionRateLabel(p)}</span>
              <span className="flex-1" />
              {!readOnly && editing === null && (
                <div className="flex gap-1.5">
                  {!p.is_primary && p.is_active && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => makePrimary(p)}
                      className="rounded border border-gray-300 px-2 py-0.5 text-[11px] text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                      title="Сделать основной: с неё платятся отпускные, больничные и займ"
                    >
                      Сделать основной
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => startEdit(p)}
                    className="rounded border border-gray-300 px-2 py-0.5 text-[11px] text-gray-600 hover:bg-gray-50"
                  >
                    Изменить
                  </button>
                  {!p.is_primary && activeCount > 1 && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setDeleteTarget(p)}
                      className="rounded border border-red-200 px-2 py-0.5 text-[11px] text-red-600 hover:bg-red-50 disabled:opacity-50"
                    >
                      Удалить
                    </button>
                  )}
                </div>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-gray-500">
              <span>Отдел: {nameOf(departments, p.department_id)}</span>
              <span>График: {nameOf(schedules, p.schedule_id)}</span>
              <span>Компания: {nameOf(companies, p.company_id)}</span>
              <span title="Коэффициент/ставка оплаты выходных">Выходные: {coeffLabel(p)}</span>
              {p.has_night_shifts && <span>Ночные: {p.night_rate ?? '—'}</span>}
            </div>

            {editing === p.id && (
              <PositionForm
                draft={draft}
                setDraft={setDraft}
                departments={departments}
                companies={companies}
                schedules={schedules}
                busy={busy}
                onSave={save}
                onCancel={() => setEditing(null)}
              />
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
            <PositionForm
              draft={draft}
              setDraft={setDraft}
              departments={departments}
              companies={companies}
              schedules={schedules}
              busy={busy}
              onSave={save}
              onCancel={() => setEditing(null)}
            />
          </div>
        )}
      </div>

      {positions.length > 1 && (
        <p className="mt-2 text-[11px] text-gray-400">
          Отпускные, больничные и погашение займа начисляются только с основной позиции —
          иначе отпуск оплачивался бы с каждого рабочего места.
        </p>
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
          {draft.has_night_shifts && (
            <input
              value={draft.night_rate}
              onChange={(e) => set('night_rate', e.target.value)}
              placeholder="Ставка за ночной час, ₽"
              className={inputCls}
            />
          )}
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Отмена</Button>
        <Button type="button" size="sm" onClick={onSave} disabled={busy}>Сохранить</Button>
      </div>
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
