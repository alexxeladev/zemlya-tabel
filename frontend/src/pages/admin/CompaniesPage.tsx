import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { listCompanies, createCompany, updateCompany, deleteCompany } from '../../api/companies'
import { useApi } from '../../hooks/useApi'
import { toast } from '../../store/toasts'
import type { Company } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { ApiError } from '../../api/client'

const schema = z.object({
  code: z.string().min(1, 'Обязательное поле').max(5, 'Максимум 5 символов'),
  name: z.string().min(1, 'Обязательное поле'),
  inn: z.string().optional().refine(
    (v) => !v || v.length === 10 || v.length === 12,
    { message: 'ИНН — 10 или 12 цифр' }
  ),
})
type FormData = z.infer<typeof schema>

export function CompaniesPage() {
  const { data: companies, isLoading, refetch } = useApi(listCompanies)
  const [editTarget, setEditTarget] = useState<Company | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Company | null>(null)

  const form = useForm<FormData>({ resolver: zodResolver(schema) })

  const openCreate = () => {
    form.reset({ code: '', name: '', inn: '' })
    setShowCreate(true)
  }

  const openEdit = (c: Company) => {
    setEditTarget(c)
    form.reset({ code: c.code, name: c.name, inn: c.inn ?? '' })
  }

  const closeModal = () => {
    setShowCreate(false)
    setEditTarget(null)
    form.reset()
  }

  const onSubmit = async (data: FormData) => {
    try {
      const payload = { ...data, inn: data.inn || null }
      if (editTarget) {
        await updateCompany(editTarget.id, payload)
        toast.success('Компания обновлена')
      } else {
        await createCompany(payload)
        toast.success('Компания создана')
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
      await deleteCompany(deleteTarget.id)
      toast.success('Компания деактивирована')
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
        title="Компании"
        action={<Button onClick={openCreate}>Добавить компанию</Button>}
      />

      <Table isLoading={isLoading} isEmpty={!companies?.length} emptyText="Компаний пока нет" skeletonCols={4}>
        <thead>
          <tr>
            <Th>Код</Th>
            <Th>Название</Th>
            <Th>ИНН</Th>
            <Th>Статус</Th>
            <Th>Действия</Th>
          </tr>
        </thead>
        <tbody>
          {companies?.map((c) => (
            <tr key={c.id} className="border-b border-gray-100 last:border-0">
              <Td><span className="font-mono">{c.code}</span></Td>
              <Td>{c.name}</Td>
              <Td>{c.inn ?? '—'}</Td>
              <Td>
                <Badge variant={c.is_active ? 'green' : 'gray'}>
                  {c.is_active ? 'Активна' : 'Неактивна'}
                </Badge>
              </Td>
              <Td>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => openEdit(c)}>Изменить</Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleteTarget(c)}>Удалить</Button>
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      <Modal
        isOpen={showCreate || !!editTarget}
        onClose={closeModal}
        title={editTarget ? 'Изменить компанию' : 'Добавить компанию'}
        actions={
          <>
            <Button type="button" variant="ghost" onClick={closeModal}>Отмена</Button>
            <Button type="submit" form="company-form" loading={form.formState.isSubmitting}>Сохранить</Button>
          </>
        }
      >
        <form id="company-form" onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
          {(['code', 'name', 'inn'] as const).map((field) => (
            <div key={field} className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">
                {field === 'code' ? 'Код' : field === 'name' ? 'Название' : 'ИНН (опционально)'}
              </label>
              <input
                {...form.register(field)}
                className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {form.formState.errors[field] && (
                <p className="text-xs text-red-600">{form.formState.errors[field]?.message}</p>
              )}
            </div>
          ))}
        </form>
      </Modal>

      <Confirm
        isOpen={!!deleteTarget}
        onConfirm={onDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Удалить компанию"
        message={`Деактивировать компанию «${deleteTarget?.name}»?`}
        danger
      />
    </div>
  )
}
