import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { listDepartments, createDepartment, updateDepartment, deleteDepartment } from '../../api/departments'
import { useApi } from '../../hooks/useApi'
import { toast } from '../../store/toasts'
import type { Department } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { ApiError } from '../../api/client'

const schema = z.object({
  name: z.string().min(1, 'Обязательное поле'),
  code: z.string().min(2, 'Минимум 2 символа').max(10, 'Максимум 10 символов'),
})
type FormData = z.infer<typeof schema>

export function DepartmentsPage() {
  const { data: departments, isLoading, refetch } = useApi(listDepartments)
  const [editTarget, setEditTarget] = useState<Department | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Department | null>(null)

  const form = useForm<FormData>({ resolver: zodResolver(schema) })

  const openCreate = () => {
    form.reset({ name: '', code: '' })
    setShowCreate(true)
  }

  const openEdit = (dept: Department) => {
    setEditTarget(dept)
    form.reset({ name: dept.name, code: dept.code })
  }

  const closeModal = () => {
    setShowCreate(false)
    setEditTarget(null)
    form.reset()
  }

  const onSubmit = async (data: FormData) => {
    try {
      if (editTarget) {
        await updateDepartment(editTarget.id, data)
        toast.success('Отдел обновлён')
      } else {
        await createDepartment(data)
        toast.success('Отдел создан')
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
      await deleteDepartment(deleteTarget.id)
      toast.success('Отдел деактивирован')
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
        title="Отделы"
        action={<Button onClick={openCreate}>Добавить отдел</Button>}
      />

      <Table
        isLoading={isLoading}
        isEmpty={!departments?.length}
        emptyText="Отделов пока нет"
        skeletonCols={3}
      >
        <thead>
          <tr>
            <Th>Код</Th>
            <Th>Название</Th>
            <Th>Статус</Th>
            <Th>Действия</Th>
          </tr>
        </thead>
        <tbody>
          {departments?.map((d) => (
            <tr key={d.id} className="border-b border-gray-100 last:border-0">
              <Td><span className="font-mono">{d.code}</span></Td>
              <Td>{d.name}</Td>
              <Td>
                <Badge variant={d.is_active ? 'green' : 'gray'}>
                  {d.is_active ? 'Активен' : 'Неактивен'}
                </Badge>
              </Td>
              <Td>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => openEdit(d)}>Изменить</Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleteTarget(d)}>Удалить</Button>
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      <Modal
        isOpen={showCreate || !!editTarget}
        onClose={closeModal}
        title={editTarget ? 'Изменить отдел' : 'Добавить отдел'}
        actions={
          <>
            <Button type="button" variant="ghost" onClick={closeModal}>Отмена</Button>
            <Button type="submit" form="dept-form" loading={form.formState.isSubmitting}>
              Сохранить
            </Button>
          </>
        }
      >
        <form id="dept-form" onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Название</label>
            <input
              {...form.register('name')}
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Дирекция"
            />
            {form.formState.errors.name && (
              <p className="text-xs text-red-600">{form.formState.errors.name.message}</p>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">Код</label>
            <input
              {...form.register('code')}
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="DIR"
            />
            {form.formState.errors.code && (
              <p className="text-xs text-red-600">{form.formState.errors.code.message}</p>
            )}
          </div>
        </form>
      </Modal>

      <Confirm
        isOpen={!!deleteTarget}
        onConfirm={onDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Удалить отдел"
        message={`Деактивировать отдел «${deleteTarget?.name}»?`}
        danger
      />
    </div>
  )
}
