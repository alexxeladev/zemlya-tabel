import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { listUsers, createUser, updateUser, resetPassword, deleteUser } from '../../api/users'
import { listDepartments } from '../../api/departments'
import { useApi } from '../../hooks/useApi'
import { toast } from '../../store/toasts'
import type { User, UserRole } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { Select } from '../../components/Select'
import { ApiError } from '../../api/client'

const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  employee: 'Сотрудник',
}

const ROLE_BADGE: Record<UserRole, 'blue' | 'amber' | 'green' | 'gray'> = {
  admin: 'blue',
  manager: 'amber',
  accountant: 'green',
  employee: 'gray',
}

const createSchema = z.object({
  email: z.string().email('Некорректный email'),
  full_name: z.string().min(1, 'Обязательное поле'),
  role: z.enum(['admin', 'manager', 'accountant', 'employee']),
  password: z.string().min(4, 'Минимум 4 символа'),
  department_id: z.coerce.number().optional(),
})

const editSchema = z.object({
  email: z.string().email('Некорректный email'),
  full_name: z.string().min(1, 'Обязательное поле'),
  role: z.enum(['admin', 'manager', 'accountant', 'employee']),
  department_id: z.coerce.number().optional(),
  is_active: z.boolean().default(true),
})

type CreateInput = z.input<typeof createSchema>
type CreateData = z.output<typeof createSchema>
type EditInput = z.input<typeof editSchema>
type EditData = z.output<typeof editSchema>

const ROLE_OPTIONS = [
  { value: 'admin', label: 'Администратор' },
  { value: 'manager', label: 'Руководитель' },
  { value: 'accountant', label: 'Бухгалтер' },
  { value: 'employee', label: 'Сотрудник' },
]

export function UsersPage() {
  const { data: users, isLoading, refetch } = useApi(listUsers)
  const { data: departments } = useApi(listDepartments)
  const [showCreate, setShowCreate] = useState(false)
  const [editTarget, setEditTarget] = useState<User | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null)
  const [resetTarget, setResetTarget] = useState<User | null>(null)
  const [tempPassword, setTempPassword] = useState<string | null>(null)

  const createForm = useForm<CreateInput, unknown, CreateData>({ resolver: zodResolver(createSchema) })
  const editForm = useForm<EditInput, unknown, EditData>({ resolver: zodResolver(editSchema) })

  const deptOptions = [
    { value: 0, label: '— нет —' },
    ...(departments?.map((d) => ({ value: d.id, label: d.name })) ?? []),
  ]

  const openEdit = (u: User) => {
    setEditTarget(u)
    editForm.reset({
      email: u.email,
      full_name: u.full_name,
      role: u.role,
      department_id: u.department_id ?? 0,
      is_active: u.is_active,
    })
  }

  const closeCreate = () => { setShowCreate(false); createForm.reset() }
  const closeEdit = () => { setEditTarget(null); editForm.reset() }

  const onCreateSubmit = async (data: CreateData) => {
    try {
      await createUser({ ...data, department_id: data.department_id || null })
      toast.success('Пользователь создан')
      closeCreate()
      refetch()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
    }
  }

  const onEditSubmit = async (data: EditData) => {
    if (!editTarget) return
    try {
      await updateUser(editTarget.id, { ...data, department_id: data.department_id || null })
      toast.success('Пользователь обновлён')
      closeEdit()
      refetch()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
    }
  }

  const onReset = async () => {
    if (!resetTarget) return
    try {
      const res = await resetPassword(resetTarget.id)
      setTempPassword(res.temp_password)
      setResetTarget(null)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
    }
  }

  const onDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteUser(deleteTarget.id)
      toast.success('Пользователь деактивирован')
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
        title="Пользователи"
        action={<Button onClick={() => setShowCreate(true)}>Добавить пользователя</Button>}
      />

      <Table isLoading={isLoading} isEmpty={!users?.length} emptyText="Пользователей нет" skeletonCols={5}>
        <thead>
          <tr>
            <Th>ФИО</Th>
            <Th>Email</Th>
            <Th>Роль</Th>
            <Th>Статус</Th>
            <Th>Действия</Th>
          </tr>
        </thead>
        <tbody>
          {users?.map((u) => (
            <tr key={u.id} className="border-b border-gray-100 last:border-0">
              <Td className="font-medium">{u.full_name}</Td>
              <Td>{u.email}</Td>
              <Td><Badge variant={ROLE_BADGE[u.role]}>{ROLE_LABELS[u.role]}</Badge></Td>
              <Td><Badge variant={u.is_active ? 'green' : 'gray'}>{u.is_active ? 'Активен' : 'Неактивен'}</Badge></Td>
              <Td>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => openEdit(u)}>Изменить</Button>
                  <Button size="sm" variant="ghost" onClick={() => setResetTarget(u)}>Сбросить пароль</Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleteTarget(u)}>Деактивировать</Button>
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>

      {/* Create modal */}
      <Modal
        isOpen={showCreate}
        onClose={closeCreate}
        title="Добавить пользователя"
        actions={
          <>
            <Button type="button" variant="ghost" onClick={closeCreate}>Отмена</Button>
            <Button type="submit" form="user-create-form" loading={createForm.formState.isSubmitting}>Создать</Button>
          </>
        }
      >
        <form id="user-create-form" onSubmit={createForm.handleSubmit(onCreateSubmit)} className="flex flex-col gap-3">
          {[
            { name: 'email' as const, label: 'Email', type: 'email' },
            { name: 'full_name' as const, label: 'ФИО', type: 'text' },
            { name: 'password' as const, label: 'Пароль', type: 'password' },
          ].map(({ name, label, type }) => (
            <div key={name} className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">{label}</label>
              <input type={type} {...createForm.register(name)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              {createForm.formState.errors[name] && <p className="text-xs text-red-600">{createForm.formState.errors[name]?.message}</p>}
            </div>
          ))}
          <Select label="Роль" options={ROLE_OPTIONS} {...createForm.register('role')} error={createForm.formState.errors.role?.message} />
          <Select label="Отдел" options={deptOptions} {...createForm.register('department_id')} />
        </form>
      </Modal>

      {/* Edit modal */}
      <Modal
        isOpen={!!editTarget}
        onClose={closeEdit}
        title="Изменить пользователя"
        actions={
          <>
            <Button type="button" variant="ghost" onClick={closeEdit}>Отмена</Button>
            <Button type="submit" form="user-edit-form" loading={editForm.formState.isSubmitting}>Сохранить</Button>
          </>
        }
      >
        <form id="user-edit-form" onSubmit={editForm.handleSubmit(onEditSubmit)} className="flex flex-col gap-3">
          {[
            { name: 'email' as const, label: 'Email', type: 'email' },
            { name: 'full_name' as const, label: 'ФИО', type: 'text' },
          ].map(({ name, label, type }) => (
            <div key={name} className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">{label}</label>
              <input type={type} {...editForm.register(name)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              {editForm.formState.errors[name] && <p className="text-xs text-red-600">{editForm.formState.errors[name]?.message}</p>}
            </div>
          ))}
          <Select label="Роль" options={ROLE_OPTIONS} {...editForm.register('role')} />
          <Select label="Отдел" options={deptOptions} {...editForm.register('department_id')} />
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" {...editForm.register('is_active')} className="rounded" />
            Активен
          </label>
        </form>
      </Modal>

      <Confirm
        isOpen={!!resetTarget}
        onConfirm={onReset}
        onCancel={() => setResetTarget(null)}
        title="Сбросить пароль"
        message={`Сгенерировать временный пароль для «${resetTarget?.full_name}»?`}
      />

      <Confirm
        isOpen={!!deleteTarget}
        onConfirm={onDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Деактивировать пользователя"
        message={`Деактивировать пользователя «${deleteTarget?.full_name}»?`}
        danger
      />

      {/* Temp password dialog */}
      <Modal
        isOpen={!!tempPassword}
        onClose={() => setTempPassword(null)}
        title="Временный пароль"
        actions={<Button type="button" onClick={() => setTempPassword(null)}>Закрыть</Button>}
      >
        <p className="text-sm text-gray-600 mb-3">Передайте пользователю этот пароль. Он будет обязан сменить его при входе.</p>
        <div className="rounded-lg bg-gray-100 px-4 py-3 font-mono text-lg tracking-widest text-gray-900">
          {tempPassword}
        </div>
      </Modal>
    </div>
  )
}
