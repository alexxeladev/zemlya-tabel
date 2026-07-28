import { useEffect, useState } from 'react'
import {
  listSchedules, createSchedule, updateSchedule, deleteSchedule, previewSchedule,
} from '../../api/schedules'
import type { ScheduleInput } from '../../api/schedules'
import { useApi } from '../../hooks/useApi'
import { toast } from '../../store/toasts'
import type { Schedule, SchedulePreview, ScheduleType } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { ApiError } from '../../api/client'

const WEEKDAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const MONTH_NAMES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

const inputCls =
  'rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

/** Состояние формы графика. Пустая строка в числовых полях = «поле чистят». */
interface FormState {
  name: string
  hours_per_shift: string
  schedule_type: ScheduleType
  work_weekdays: number[]
  cycle_start_date: string
  cycle_work_days: string
  cycle_off_days: string
  description: string
}

const emptyForm = (): FormState => ({
  name: '',
  hours_per_shift: '8',
  schedule_type: 'weekday',
  work_weekdays: [0, 1, 2, 3, 4],
  cycle_start_date: '',
  cycle_work_days: '2',
  cycle_off_days: '2',
  description: '',
})

const formFrom = (s: Schedule): FormState => ({
  name: s.name,
  hours_per_shift: String(s.hours_per_shift),
  schedule_type: s.schedule_type,
  work_weekdays: s.work_weekdays ?? [0, 1, 2, 3, 4],
  cycle_start_date: s.cycle_start_date ?? '',
  cycle_work_days: String(s.cycle_work_days ?? 2),
  cycle_off_days: String(s.cycle_off_days ?? 2),
  description: s.description ?? '',
})

/** Человекочитаемое описание графика для таблицы. */
function describeSchedule(s: Schedule): string {
  if (s.schedule_type === 'cyclic') {
    const pattern = s.cycle_work_days && s.cycle_off_days
      ? `${s.cycle_work_days}/${s.cycle_off_days}`
      : '—'
    return `цикл ${pattern}, старт ${s.cycle_start_date ?? '— не задан'}`
  }
  const days = s.work_weekdays?.length
    ? s.work_weekdays.map((d) => WEEKDAY_LABELS[d]).join(', ')
    : 'по названию'
  return days
}

/**
 * Превью рабочих дней месяца — чтобы админ глазами проверил фазу цикла
 * до сохранения. Считает бэк (тот же код, что норма и автозаполнение).
 */
function SchedulePreviewGrid({ form }: { form: FormState }) {
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [preview, setPreview] = useState<SchedulePreview | null>(null)
  const [error, setError] = useState<string | null>(null)

  const hours = Number(form.hours_per_shift) || 0
  const cycleWork = Number(form.cycle_work_days) || 0
  const cycleOff = Number(form.cycle_off_days) || 0
  const weekdaysKey = form.work_weekdays.join(',')

  useEffect(() => {
    let cancelled = false
    previewSchedule({
      year,
      month,
      name: form.name,
      hours_per_shift: hours,
      schedule_type: form.schedule_type,
      work_weekdays: form.work_weekdays,
      cycle_start_date: form.cycle_start_date || null,
      cycle_work_days: cycleWork || null,
      cycle_off_days: cycleOff || null,
    })
      .then((data) => { if (!cancelled) { setPreview(data); setError(null) } })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : 'Не удалось построить превью')
      })
    return () => { cancelled = true }
  }, [year, month, form.schedule_type, hours, weekdaysKey, form.cycle_start_date,
      cycleWork, cycleOff, form.name])

  // Пустые ячейки в начале, чтобы 1-е число встало под свой день недели.
  const lead = preview ? preview.days[0]?.weekday ?? 0 : 0
  const cells: Array<SchedulePreview['days'][number] | null> = preview
    ? [...Array(lead).fill(null), ...preview.days]
    : []
  while (cells.length % 7 !== 0) cells.push(null)

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium text-gray-700">Превью рабочих дней</span>
        <select
          value={month}
          onChange={(e) => setMonth(Number(e.target.value))}
          className="ml-auto rounded border border-gray-300 px-2 py-1 text-xs"
        >
          {MONTH_NAMES.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
        </select>
        <input
          type="number"
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="w-20 rounded border border-gray-300 px-2 py-1 text-xs"
        />
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}
      {preview?.issue && <p className="text-xs text-amber-700">{preview.issue}</p>}

      {preview && !preview.issue && (
        <>
          <div className="grid grid-cols-7 gap-1 text-center text-[10px] text-gray-500">
            {WEEKDAY_LABELS.map((w) => <div key={w}>{w}</div>)}
          </div>
          <div className="mt-1 grid grid-cols-7 gap-1">
            {cells.map((cell, i) => (
              <div
                key={i}
                title={cell?.is_work_day ? `${cell.hours} ч` : undefined}
                className={[
                  'flex h-9 flex-col items-center justify-center rounded text-xs',
                  cell === null ? 'invisible' : '',
                  cell?.is_work_day
                    ? 'bg-blue-600 font-semibold text-white'
                    : 'bg-white text-gray-400',
                  cell && !cell.is_work_day && cell.is_holiday ? 'text-red-500' : '',
                ].join(' ')}
              >
                <span>{cell?.day}</span>
                {cell?.is_work_day && <span className="text-[9px] opacity-80">{cell.hours}</span>}
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-gray-600">
            Рабочих дней: <b>{preview.work_days}</b> · Норма: <b>{preview.norm_hours} ч</b>
            {!preview.has_calendar && (
              <span className="text-amber-700"> · производственный календарь {preview.year} не загружен</span>
            )}
          </p>
        </>
      )}
    </div>
  )
}

export function SchedulesPage() {
  const { data: schedules, isLoading, refetch } = useApi(listSchedules)
  const [editTarget, setEditTarget] = useState<Schedule | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Schedule | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm())
  const [saving, setSaving] = useState(false)

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const toggleWeekday = (d: number) =>
    setForm((f) => ({
      ...f,
      work_weekdays: f.work_weekdays.includes(d)
        ? f.work_weekdays.filter((x) => x !== d)
        : [...f.work_weekdays, d].sort((a, b) => a - b),
    }))

  const openCreate = () => {
    setForm(emptyForm())
    setShowCreate(true)
  }

  const openEdit = (s: Schedule) => {
    setForm(formFrom(s))
    setEditTarget(s)
  }

  const closeModal = () => {
    setShowCreate(false)
    setEditTarget(null)
  }

  const validate = (): string | null => {
    if (!form.name.trim()) return 'Укажите название'
    const hours = Number(form.hours_per_shift)
    if (!hours || hours < 1 || hours > 24) return 'Часов в смену: от 1 до 24'
    if (form.schedule_type === 'weekday') {
      if (form.work_weekdays.length === 0) return 'Отметьте хотя бы один рабочий день недели'
    } else {
      if (!form.cycle_start_date) return 'Укажите дату начала цикла'
      if (Number(form.cycle_work_days) < 1) return 'Смен подряд: не меньше 1'
      if (Number(form.cycle_off_days) < 1) return 'Выходных подряд: не меньше 1'
    }
    return null
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const error = validate()
    if (error) {
      toast.error(error)
      return
    }
    const cyclic = form.schedule_type === 'cyclic'
    const payload: ScheduleInput = {
      name: form.name.trim(),
      hours_per_shift: Number(form.hours_per_shift),
      schedule_type: form.schedule_type,
      // Поля чужого типа обнуляем, чтобы график не тащил остатки прошлой настройки.
      work_weekdays: cyclic ? null : form.work_weekdays,
      cycle_start_date: cyclic ? form.cycle_start_date : null,
      cycle_work_days: cyclic ? Number(form.cycle_work_days) : null,
      cycle_off_days: cyclic ? Number(form.cycle_off_days) : null,
      description: form.description || null,
    }
    setSaving(true)
    try {
      if (editTarget) {
        await updateSchedule(editTarget.id, payload)
        toast.success('График обновлён')
      } else {
        await createSchedule(payload)
        toast.success('График создан')
      }
      closeModal()
      refetch()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Ошибка')
    } finally {
      setSaving(false)
    }
  }

  const onDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteSchedule(deleteTarget.id)
      toast.success('График деактивирован')
      setDeleteTarget(null)
      refetch()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
      setDeleteTarget(null)
    }
  }

  return (
    <div>
      <PageHeader
        title="Графики работы"
        action={<Button onClick={openCreate}>Добавить график</Button>}
      />

      <Table isLoading={isLoading} isEmpty={!schedules?.length} emptyText="Графиков пока нет" skeletonCols={6}>
        <thead>
          <tr>
            <Th>Название</Th>
            <Th>Тип</Th>
            <Th>Рабочие дни</Th>
            <Th>Часов/смена</Th>
            <Th>Описание</Th>
            <Th>Статус</Th>
            <Th>Действия</Th>
          </tr>
        </thead>
        <tbody>
          {schedules?.map((s) => (
            <tr key={s.id} className="border-b border-gray-100 last:border-0">
              <Td>{s.name}</Td>
              <Td>
                <Badge variant={s.schedule_type === 'cyclic' ? 'blue' : 'gray'}>
                  {s.schedule_type === 'cyclic' ? 'Скользящий цикл' : 'По дням недели'}
                </Badge>
              </Td>
              <Td>{describeSchedule(s)}</Td>
              <Td>{s.hours_per_shift}</Td>
              <Td>{s.description ?? '—'}</Td>
              <Td>
                <Badge variant={s.is_active ? 'green' : 'gray'}>
                  {s.is_active ? 'Активен' : 'Неактивен'}
                </Badge>
              </Td>
              <Td>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => openEdit(s)}>Изменить</Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleteTarget(s)}>Удалить</Button>
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      <Modal
        isOpen={showCreate || !!editTarget}
        onClose={closeModal}
        title={editTarget ? 'Изменить график' : 'Добавить график'}
        actions={
          <>
            <Button type="button" variant="ghost" onClick={closeModal}>Отмена</Button>
            <Button type="submit" form="schedule-form" loading={saving}>Сохранить</Button>
          </>
        }
      >
        <form id="schedule-form" onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Название</label>
            <input
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              className={inputCls}
              placeholder="2/2 смена 1"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Тип графика</label>
            <div className="flex gap-4 text-sm">
              {([
                ['weekday', 'По дням недели'],
                ['cyclic', 'Скользящий цикл'],
              ] as Array<[ScheduleType, string]>).map(([value, label]) => (
                <label key={value} className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="schedule_type"
                    checked={form.schedule_type === value}
                    onChange={() => set('schedule_type', value)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          {form.schedule_type === 'weekday' ? (
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">Рабочие дни недели</label>
              <div className="flex gap-1">
                {WEEKDAY_LABELS.map((label, d) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => toggleWeekday(d)}
                    className={[
                      'w-11 rounded-lg border px-2 py-1.5 text-sm',
                      form.work_weekdays.includes(d)
                        ? 'border-blue-600 bg-blue-600 font-medium text-white'
                        : 'border-gray-300 bg-white text-gray-600',
                    ].join(' ')}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-gray-500">
                Праздники производственного календаря из графика исключаются автоматически.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">Дата начала цикла</label>
                <input
                  type="date"
                  value={form.cycle_start_date}
                  onChange={(e) => set('cycle_start_date', e.target.value)}
                  className={inputCls}
                />
                <p className="text-xs text-gray-500">
                  Первый рабочий день цикла. Смена 2 — тот же паттерн с другой стартовой
                  датой (сдвиг фазы). Праздники на цикл не влияют.
                </p>
              </div>
              <div className="flex gap-3">
                <div className="flex flex-1 flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Смен подряд</label>
                  <input
                    type="number" min={1}
                    value={form.cycle_work_days}
                    onChange={(e) => set('cycle_work_days', e.target.value)}
                    className={inputCls}
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Выходных подряд</label>
                  <input
                    type="number" min={1}
                    value={form.cycle_off_days}
                    onChange={(e) => set('cycle_off_days', e.target.value)}
                    className={inputCls}
                  />
                </div>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Часов в смену</label>
            <input
              type="number" min={1} max={24}
              value={form.hours_per_shift}
              onChange={(e) => set('hours_per_shift', e.target.value)}
              className={inputCls}
            />
          </div>

          <SchedulePreviewGrid form={form} />

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Описание (опционально)</label>
            <textarea
              value={form.description}
              onChange={(e) => set('description', e.target.value)}
              rows={2}
              className={inputCls}
            />
          </div>
        </form>
      </Modal>

      <Confirm
        isOpen={!!deleteTarget}
        onConfirm={onDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Удалить график"
        message={`Деактивировать график «${deleteTarget?.name}»?`}
        danger
      />
    </div>
  )
}
