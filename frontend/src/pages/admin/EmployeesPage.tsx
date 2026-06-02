import { useCallback, useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { listEmployees, createEmployee, updateEmployee, deleteEmployee } from '../../api/employees'
import { listDepartments } from '../../api/departments'
import { listCompanies } from '../../api/companies'
import { listSchedules } from '../../api/schedules'
import { useApi } from '../../hooks/useApi'
import { useAuth } from '../../hooks/useAuth'
import { toast } from '../../store/toasts'
import type { Employee } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { Select } from '../../components/Select'
import { ApiError } from '../../api/client'

const schema = z.object({
  tab_number: z.string().optional(),
  full_name: z.string().min(1, 'Обязательное поле'),
  position: z.string().optional(),
  department_id: z.coerce.number().min(1, 'Выберите отдел'),
  schedule_id: z.coerce.number().min(1, 'Выберите график'),
  default_company_id: z.coerce.number().min(1, 'Выберите компанию'),
  rate: z.string().min(1, 'Обязательное поле'),
  is_active: z.boolean().default(true),
  hire_date: z.string().optional(),
  dismissal_date: z.string().optional(),
})
type FormInput = z.input<typeof schema>
type FormData = z.output<typeof schema>

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export function EmployeesPage() {
  const { canAdmin, isManager, user } = useAuth()
  const [search, setSearch] = useState('')
  const [filterDept, setFilterDept] = useState<number | undefined>(undefined)
  const [filterActive, setFilterActive] = useState<boolean | undefined>(undefined)
  const debouncedSearch = useDebounce(search, 300)

  const params = {
    search: debouncedSearch || undefined,
    department_id: isManager() ? undefined : filterDept,
    is_active: filterActive,
  }
  const fetchFn = useCallback(() => listEmployees(params), [debouncedSearch, filterDept, filterActive])
  const { data: employees, isLoading, refetch } = useApi(fetchFn, [debouncedSearch, filterDept, filterActive])

  const { data: departments } = useApi(listDepartments)
  const { data: companies } = useApi(listCompanies)
  const { data: schedules } = useApi(listSchedules)

  const [editTarget, setEditTarget] = useState<Employee | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Employee | null>(null)

  const form = useForm<FormInput, unknown, FormData>({ resolver: zodResolver(schema) })

  const deptOptions = [{ value: 0, label: '— выберите —' }, ...(departments?.map((d) => ({ value: d.id, label: d.name })) ?? [])]
  const companyOptions = [{ value: 0, label: '— выберите —' }, ...(companies?.map((c) => ({ value: c.id, label: c.name })) ?? [])]
  const scheduleOptions = [{ value: 0, label: '— выберите —' }, ...(schedules?.map((s) => ({ value: s.id, label: s.name })) ?? [])]

  const openCreate = () => {
    form.reset({
      tab_number: '',
      full_name: '',
      position: '',
      department_id: isManager() ? (user?.department_id ?? 0) : 0,
      schedule_id: 0,
      default_company_id: 0,
      rate: '',
      is_active: true,
      hire_date: '',
      dismissal_date: '',
    })
    setShowCreate(true)
  }

  const openEdit = (e: Employee) => {
    setEditTarget(e)
    form.reset({
      tab_number: e.tab_number ?? '',
      full_name: e.full_name,
      position: e.position ?? '',
      department_id: e.department_id,
      schedule_id: e.schedule_id,
      default_company_id: e.default_company_id,
      rate: e.rate,
      is_active: e.is_active,
      hire_date: e.hire_date ?? '',
      dismissal_date: e.dismissal_date ?? '',
    })
  }

  const closeModal = () => {
    setShowCreate(false)
    setEditTarget(null)
    form.reset()
  }

  const onSubmit = async (data: FormData) => {
    try {
      const payload = {
        ...data,
        tab_number: data.tab_number || null,
        position: data.position || null,
        hire_date: data.hire_date || null,
        dismissal_date: data.dismissal_date || null,
      }
      if (editTarget) {
        await updateEmployee(editTarget.id, payload)
        toast.success('Сотрудник обновлён')
      } else {
        await createEmployee(payload)
        toast.success('Сотрудник создан')
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
      await deleteEmployee(deleteTarget.id)
      toast.success('Сотрудник деактивирован')
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
        title="Сотрудники"
        action={canAdmin() ? <Button onClick={openCreate}>Добавить сотрудника</Button> : undefined}
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по ФИО или табельному №"
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 w-64"
        />
        {!isManager() && (
          <select
            value={filterDept ?? ''}
            onChange={(e) => setFilterDept(e.target.value ? Number(e.target.value) : undefined)}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Все отделы</option>
            {departments?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        )}
        <select
          value={filterActive === undefined ? '' : String(filterActive)}
          onChange={(e) => setFilterActive(e.target.value === '' ? undefined : e.target.value === 'true')}
          className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">Все</option>
          <option value="true">Только активные</option>
          <option value="false">Только уволенные</option>
        </select>
      </div>

      <Table isLoading={isLoading} isEmpty={!employees?.length} emptyText="Сотрудников не найдено" skeletonCols={7}>
        <thead>
          <tr>
            <Th>Таб. №</Th>
            <Th>ФИО</Th>
            <Th>Должность</Th>
            <Th>Отдел</Th>
            <Th>График</Th>
            <Th>Компания</Th>
            <Th>Оклад</Th>
            <Th>Статус</Th>
            {canAdmin() && <Th>Действия</Th>}
          </tr>
        </thead>
        <tbody>
          {employees?.map((e) => (
            <tr key={e.id} className="border-b border-gray-100 last:border-0">
              <Td><span className="font-mono text-xs">{e.tab_number ?? '—'}</span></Td>
              <Td className="font-medium">{e.full_name}</Td>
              <Td>{e.position ?? '—'}</Td>
              <Td>{e.department?.name ?? '—'}</Td>
              <Td>{e.schedule?.name ?? '—'}</Td>
              <Td>{e.default_company?.name ?? '—'}</Td>
              <Td>{Number(e.rate).toLocaleString('ru-RU')} ₽</Td>
              <Td>
                <Badge variant={e.is_active ? 'green' : 'gray'}>
                  {e.is_active ? 'Активен' : 'Уволен'}
                </Badge>
              </Td>
              {canAdmin() && (
                <Td>
                  <div className="flex gap-2">
                    <Button size="sm" variant="secondary" onClick={() => openEdit(e)}>Изменить</Button>
                    <Button size="sm" variant="danger" onClick={() => setDeleteTarget(e)}>Уволить</Button>
                  </div>
                </Td>
              )}
            </tr>
          ))}
        </tbody>
      </Table>

      <Modal
        isOpen={showCreate || !!editTarget}
        onClose={closeModal}
        title={editTarget ? 'Изменить сотрудника' : 'Добавить сотрудника'}
        actions={
          <>
            <Button type="button" variant="ghost" onClick={closeModal}>Отмена</Button>
            <Button type="submit" form="emp-form" loading={form.formState.isSubmitting}>Сохранить</Button>
          </>
        }
      >
        <form id="emp-form" onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-3 max-h-[60vh] overflow-y-auto pr-1">
          {[
            { name: 'tab_number' as const, label: 'Табельный номер' },
            { name: 'full_name' as const, label: 'ФИО' },
            { name: 'position' as const, label: 'Должность' },
            { name: 'rate' as const, label: 'Оклад' },
          ].map(({ name, label }) => (
            <div key={name} className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">{label}</label>
              <input {...form.register(name)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              {form.formState.errors[name] && <p className="text-xs text-red-600">{form.formState.errors[name]?.message}</p>}
            </div>
          ))}
          <Select label="Отдел" options={deptOptions} {...form.register('department_id')} error={form.formState.errors.department_id?.message} />
          <Select label="График" options={scheduleOptions} {...form.register('schedule_id')} error={form.formState.errors.schedule_id?.message} />
          <Select label="Основная компания" options={companyOptions} {...form.register('default_company_id')} error={form.formState.errors.default_company_id?.message} />
          <div className="flex gap-4">
            <div className="flex flex-col gap-1 flex-1">
              <label className="text-sm font-medium text-gray-700">Дата приёма</label>
              <input type="date" {...form.register('hire_date')} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div className="flex flex-col gap-1 flex-1">
              <label className="text-sm font-medium text-gray-700">Дата увольнения</label>
              <input type="date" {...form.register('dismissal_date')} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" {...form.register('is_active')} className="rounded" />
            Активен
          </label>
        </form>
      </Modal>

      <Confirm
        isOpen={!!deleteTarget}
        onConfirm={onDelete}
        onCancel={() => setDeleteTarget(null)}
        title="Уволить сотрудника"
        message={`Деактивировать карточку сотрудника «${deleteTarget?.full_name}»?`}
        danger
      />
    </div>
  )
}
