import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { listSchedules, createSchedule, updateSchedule, deleteSchedule } from '../../api/schedules'
import { useApi } from '../../hooks/useApi'
import { toast } from '../../store/toasts'
import type { Schedule } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { ApiError } from '../../api/client'

const schema = z.object({
  name: z.string().min(1, 'Обязательное поле'),
  hours_per_shift: z.coerce.number().min(1, 'Мин. 1').max(24, 'Макс. 24'),
  description: z.string().optional(),
})
type FormInput = z.input<typeof schema>
type FormData = z.output<typeof schema>

export function SchedulesPage() {
  const { data: schedules, isLoading, refetch } = useApi(listSchedules)
  const [editTarget, setEditTarget] = useState<Schedule | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Schedule | null>(null)

  const form = useForm<FormInput, unknown, FormData>({ resolver: zodResolver(schema) })

  const openCreate = () => {
    form.reset({ name: '', hours_per_shift: 8, description: '' })
    setShowCreate(true)
  }

  const openEdit = (s: Schedule) => {
    setEditTarget(s)
    form.reset({ name: s.name, hours_per_shift: s.hours_per_shift, description: s.description ?? '' })
  }

  const closeModal = () => {
    setShowCreate(false)
    setEditTarget(null)
    form.reset()
  }

  const onSubmit = async (data: FormData) => {
    try {
      const payload = { ...data, description: data.description || null }
      if (editTarget) {
        await updateSchedule(editTarget.id, payload)
        toast.success('График обновлён')
      } else {
        await createSchedule(payload)
        toast.success('График создан')
      }
      closeModal()
      refetch()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
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

      <Table isLoading={isLoading} isEmpty={!schedules?.length} emptyText="Графиков пока нет" skeletonCols={4}>
        <thead>
          <tr>
            <Th>Название</Th>
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
            <Button type="submit" form="schedule-form" loading={form.formState.isSubmitting}>Сохранить</Button>
          </>
        }
      >
        <form id="schedule-form" onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Название</label>
            <input {...form.register('name')} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="5/2" />
            {form.formState.errors.name && <p className="text-xs text-red-600">{form.formState.errors.name.message}</p>}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Часов в смену</label>
            <input type="number" {...form.register('hours_per_shift')} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            {form.formState.errors.hours_per_shift && <p className="text-xs text-red-600">{form.formState.errors.hours_per_shift.message}</p>}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Описание (опционально)</label>
            <textarea {...form.register('description')} rows={2} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
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
