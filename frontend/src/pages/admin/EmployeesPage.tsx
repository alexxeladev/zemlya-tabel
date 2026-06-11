import { useCallback, useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  listEmployees, createEmployee, updateEmployee,
  grantAccess, updateRole, resetPassword, revokeAccess,
  dismissEmployee, rehireEmployee,
} from '../../api/employees'
import { listDepartments } from '../../api/departments'
import { listCompanies } from '../../api/companies'
import { listSchedules } from '../../api/schedules'
import { useApi } from '../../hooks/useApi'
import { useAuth } from '../../hooks/useAuth'
import { toast } from '../../store/toasts'
import type { Employee, UserRole } from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { Select } from '../../components/Select'
import { ApiError } from '../../api/client'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  employee: 'Сотрудник',
}

const MANAGER_LOCK_TIP = 'Только администратор может изменить'

const ROLE_OPTIONS = [
  { value: 'admin', label: 'Администратор' },
  { value: 'manager', label: 'Руководитель' },
  { value: 'accountant', label: 'Бухгалтер' },
  { value: 'employee', label: 'Сотрудник' },
]

const schema = z.object({
  tab_number: z.string().optional(),
  full_name: z.string().min(1, 'Обязательное поле'),
  position: z.string().optional(),
  department_id: z.coerce.number().optional(),
  schedule_id: z.coerce.number().optional(),
  default_company_id: z.coerce.number().optional(),
  rate: z.string().optional(),
  weekend_pay_type: z.enum(['coefficient', 'fixed_rate']).default('coefficient'),
  weekend_coefficient: z.string().optional(),
  weekend_fixed_rate: z.string().optional(),
  is_active: z.boolean().default(true),
  hire_date: z.string().optional(),
  dismissal_date: z.string().optional(),
  has_access: z.boolean().default(false),
  email: z.string().optional(),
  role: z.string().optional(),
  initial_password: z.string().optional(),
  is_system_admin: z.boolean().default(false),
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

  const fetchFn = useCallback(
    () => listEmployees({
      search: debouncedSearch || undefined,
      department_id: isManager() ? undefined : filterDept,
      is_active: filterActive,
    }),
    [debouncedSearch, filterDept, filterActive],
  )
  const { data: employees, isLoading, refetch } = useApi(fetchFn, [debouncedSearch, filterDept, filterActive])
  const { data: departments } = useApi(listDepartments)
  const { data: companies } = useApi(listCompanies)
  const { data: schedules } = useApi(listSchedules)

  const [editTarget, setEditTarget] = useState<Employee | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [dismissTarget, setDismissTarget] = useState<Employee | null>(null)
  const [dismissDate, setDismissDate] = useState('')
  const [resetTarget, setResetTarget] = useState<Employee | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<Employee | null>(null)
  const [tempPassword, setTempPassword] = useState<string | null>(null)

  const form = useForm<FormInput, unknown, FormData>({ resolver: zodResolver(schema) })
  const hasAccess = form.watch('has_access')
  const weekendType = form.watch('weekend_pay_type')

  const deptOptions = [
    { value: 0, label: '— без отдела —' },
    ...(departments?.map((d) => ({ value: d.id, label: d.name })) ?? []),
  ]
  const companyOptions = [
    { value: 0, label: '— не указана —' },
    ...(companies?.map((c) => ({ value: c.id, label: c.name })) ?? []),
  ]
  const scheduleOptions = [
    { value: 0, label: '— не указан —' },
    ...(schedules?.map((s) => ({ value: s.id, label: s.name })) ?? []),
  ]

  const openCreate = () => {
    form.reset({
      tab_number: '', full_name: '', position: '',
      department_id: isManager() ? (user?.department_id ?? undefined) : undefined,
      schedule_id: undefined, default_company_id: undefined,
      rate: '', weekend_pay_type: 'coefficient', weekend_coefficient: '1.5', weekend_fixed_rate: '',
      is_active: true, hire_date: '', dismissal_date: '',
      has_access: false, email: '', role: 'employee', initial_password: '', is_system_admin: false,
    })
    setShowCreate(true)
  }

  const openEdit = (e: Employee) => {
    setEditTarget(e)
    form.reset({
      tab_number: e.tab_number ?? '',
      full_name: e.full_name,
      position: e.position ?? '',
      department_id: e.department_id ?? undefined,
      schedule_id: e.schedule_id ?? undefined,
      default_company_id: e.default_company_id ?? undefined,
      rate: e.rate ?? '',
      weekend_pay_type: e.weekend_pay_type ?? 'coefficient',
      weekend_coefficient: e.weekend_coefficient ?? '',
      weekend_fixed_rate: e.weekend_fixed_rate ?? '',
      is_active: e.is_active,
      hire_date: e.hire_date ?? '',
      dismissal_date: e.dismissal_date ?? '',
      has_access: e.has_access,
      email: e.email ?? '',
      role: e.role ?? 'employee',
      initial_password: '',
      is_system_admin: e.is_system_admin,
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
        tab_number: data.tab_number || null,
        full_name: data.full_name,
        position: data.position || null,
        department_id: data.department_id || null,
        schedule_id: data.schedule_id || null,
        default_company_id: data.default_company_id || null,
        rate: data.rate || null,
        weekend_pay_type: data.weekend_pay_type,
        weekend_coefficient: data.weekend_pay_type === 'coefficient' ? (data.weekend_coefficient || null) : null,
        weekend_fixed_rate: data.weekend_pay_type === 'fixed_rate' ? (data.weekend_fixed_rate || null) : null,
        is_active: data.is_active,
        hire_date: data.hire_date || null,
        dismissal_date: data.dismissal_date || null,
        is_system_admin: data.is_system_admin,
        access: data.has_access && data.email && data.role
          ? { email: data.email, role: data.role as UserRole, initial_password: data.initial_password ?? '' }
          : null,
      }

      if (editTarget) {
        await updateEmployee(editTarget.id, payload)

        // Handle access changes for edit
        if (data.has_access && !editTarget.has_access && data.email && data.role) {
          await grantAccess(editTarget.id, {
            email: data.email,
            role: data.role as UserRole,
            initial_password: data.initial_password ?? '',
          })
        } else if (data.has_access && editTarget.has_access && data.role && data.role !== editTarget.role && !editTarget.is_system_admin) {
          await updateRole(editTarget.id, { role: data.role as UserRole })
        }

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

  const onDismiss = async () => {
    if (!dismissTarget || !dismissDate) return
    try {
      await dismissEmployee(dismissTarget.id, dismissDate)
      toast.success('Сотрудник уволен')
      setDismissTarget(null)
      setDismissDate('')
      refetch()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
    }
  }

  const onRehire = async (emp: Employee) => {
    try {
      await rehireEmployee(emp.id)
      toast.success('Сотрудник принят обратно')
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
      setResetTarget(null)
    }
  }

  const onRevoke = async () => {
    if (!revokeTarget) return
    try {
      await revokeAccess(revokeTarget.id)
      toast.success('Доступ отозван')
      setRevokeTarget(null)
      refetch()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка')
      setRevokeTarget(null)
    }
  }

  const noDepMsg = isManager() && !user?.department_id
  const isMgr = isManager()
  // Правка 3.9-1: manager только просматривает. Редактировать карточку может только admin.
  const readOnly = !canAdmin()
  const canEdit = canAdmin() || isMgr  // admin правит, manager открывает карточку на просмотр

  return (
    <div>
      <PageHeader
        title="Сотрудники"
        action={canAdmin() ? <Button onClick={openCreate}>Добавить сотрудника</Button> : undefined}
      />

      {noDepMsg && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          У вас не задан отдел. Обратитесь к администратору для назначения отдела.
        </div>
      )}

      {!noDepMsg && (
        <div className="mb-4 flex flex-wrap gap-3">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по ФИО или табельному №"
            className="w-64 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
      )}

      <Table isLoading={isLoading} isEmpty={!employees?.length} emptyText="Сотрудников не найдено" skeletonCols={8}>
        <thead>
          <tr>
            <Th>Таб. №</Th>
            <Th>ФИО</Th>
            <Th>Должность</Th>
            <Th>Отдел</Th>
            <Th>График</Th>
            <Th>Доступ</Th>
            <Th>Статус</Th>
            {canEdit && <Th>Действия</Th>}
          </tr>
        </thead>
        <tbody>
          {employees?.map((e) => (
            <tr key={e.id} className="border-b border-gray-100 last:border-0">
              <Td><span className="font-mono text-xs">{e.tab_number ?? '—'}</span></Td>
              <Td className="font-medium">
                {e.full_name}
                {e.is_system_admin && (
                  <span className="ml-2 rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700">Системный</span>
                )}
                {!e.is_system_admin && !e.schedule_id && (
                  <span
                    className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700"
                    title="График не задан, автозаполнение по графику недоступно"
                  >
                    Нет графика
                  </span>
                )}
              </Td>
              <Td>{e.position ?? '—'}</Td>
              <Td>{e.department?.name ?? '—'}</Td>
              <Td>{e.schedule?.name ?? '—'}</Td>
              <Td>
                {e.is_system_admin
                  ? <Badge variant="blue">Системный</Badge>
                  : e.has_access && e.role
                    ? <Badge variant="green">{ROLE_LABELS[e.role] ?? e.role}</Badge>
                    : <Badge variant="gray">Нет</Badge>
                }
              </Td>
              <Td>
                {e.is_active
                  ? <Badge variant="green">Работает</Badge>
                  : <Badge variant="gray">Уволен {e.dismissal_date ? `с ${e.dismissal_date}` : ''}</Badge>
                }
              </Td>
              {canEdit && (
                <Td>
                  <div className="flex gap-2">
                    <Button size="sm" variant="secondary" onClick={() => openEdit(e)}>{readOnly ? 'Просмотр' : 'Изменить'}</Button>
                    {canAdmin() && !e.is_system_admin && e.is_active && (
                      <Button size="sm" variant="danger" onClick={() => { setDismissTarget(e); setDismissDate(new Date().toISOString().slice(0, 10)) }}>Уволить</Button>
                    )}
                    {canAdmin() && !e.is_system_admin && !e.is_active && (
                      <Button size="sm" variant="secondary" onClick={() => onRehire(e)}>Принять обратно</Button>
                    )}
                  </div>
                </Td>
              )}
            </tr>
          ))}
        </tbody>
      </Table>

      {/* Create / Edit modal */}
      <Modal
        isOpen={showCreate || !!editTarget}
        onClose={closeModal}
        title={editTarget ? `${readOnly ? 'Просмотр' : 'Изменить'}: ${editTarget.full_name}` : 'Добавить сотрудника'}
        actions={
          readOnly ? (
            <Button type="button" onClick={closeModal}>Закрыть</Button>
          ) : (
            <>
              <Button type="button" variant="ghost" onClick={closeModal}>Отмена</Button>
              <Button type="submit" form="emp-form" loading={form.formState.isSubmitting}>Сохранить</Button>
            </>
          )
        }
      >
        <form id="emp-form" onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4 max-h-[70vh] overflow-y-auto pr-1">
          <fieldset disabled={readOnly} className="contents">
          {/* Section 1 — Personal */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Личная информация</p>
            <div className="flex flex-col gap-3">
              {[
                { name: 'tab_number' as const, label: 'Табельный номер', locked: isMgr },
                { name: 'full_name' as const, label: 'ФИО *', locked: false },
                { name: 'position' as const, label: 'Должность', locked: false },
              ].map(({ name, label, locked }) => (
                <div key={name} className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">{label}</label>
                  <input
                    {...form.register(name)}
                    disabled={locked}
                    title={locked ? MANAGER_LOCK_TIP : undefined}
                    className={`rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 ${locked ? 'bg-gray-100 text-gray-500 cursor-not-allowed' : ''}`}
                  />
                  {form.formState.errors[name] && <p className="text-xs text-red-600">{form.formState.errors[name]?.message}</p>}
                </div>
              ))}
            </div>
          </div>

          {/* Section 2 — Structure */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Структура</p>
            <div className="flex flex-col gap-3">
              <Select
                label="Отдел"
                options={deptOptions}
                {...form.register('department_id')}
                disabled={isMgr}
                title={isMgr ? MANAGER_LOCK_TIP : undefined}
                className={isMgr ? 'bg-gray-100 text-gray-500 cursor-not-allowed' : ''}
              />
              <Select label="График" options={scheduleOptions} {...form.register('schedule_id')} />
              <Select
                label="Основная компания"
                options={companyOptions}
                {...form.register('default_company_id')}
                disabled={isMgr}
                title={isMgr ? MANAGER_LOCK_TIP : undefined}
                className={isMgr ? 'bg-gray-100 text-gray-500 cursor-not-allowed' : ''}
              />
            </div>
          </div>

          {/* Section 3 — Employment */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Трудовая занятость</p>
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">Оклад (₽)</label>
                <input {...form.register('rate')} placeholder="50000" className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">Дата приёма</label>
                <input type="date" {...form.register('hire_date')} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              {editTarget && !editTarget.is_active && (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Дата увольнения</label>
                  <input readOnly value={editTarget.dismissal_date ?? ''} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
                </div>
              )}
              {!isMgr && editTarget && !editTarget.is_system_admin && editTarget.is_active && (
                <div className="pt-1">
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => { closeModal(); setDismissTarget(editTarget); setDismissDate(new Date().toISOString().slice(0, 10)) }}
                  >
                    Уволить
                  </Button>
                </div>
              )}
              {!isMgr && editTarget && !editTarget.is_system_admin && !editTarget.is_active && (
                <div className="pt-1">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => { closeModal(); onRehire(editTarget) }}
                  >
                    Принять обратно
                  </Button>
                </div>
              )}
            </div>
          </div>

          {/* Section 3b — Weekend / holiday pay (правка 3.9-3) */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Оплата выходных и праздничных</p>
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-2">
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="radio" value="coefficient" {...form.register('weekend_pay_type')} />
                  По коэффициенту
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="radio" value="fixed_rate" {...form.register('weekend_pay_type')} />
                  Фиксированная ставка за час
                </label>
              </div>
              {weekendType === 'fixed_rate' ? (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Ставка за час в выходной (₽)</label>
                  <input
                    {...form.register('weekend_fixed_rate')}
                    placeholder="740"
                    className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Коэффициент оплаты выходных</label>
                  <input
                    {...form.register('weekend_coefficient')}
                    placeholder="1.5"
                    className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <p className="text-xs text-gray-400">1.5 = полуторный, 2 = двойной, 0 = не оплачивается дополнительно</p>
                </div>
              )}
            </div>
          </div>

          {/* Section 4 — Access (manager не управляет доступом) */}
          {!isMgr && (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Доступ в систему</p>
            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer mb-3">
              <input type="checkbox" {...form.register('has_access')} className="rounded" />
              Есть доступ в систему
            </label>

            {hasAccess && (
              <div className="flex flex-col gap-3 pl-2 border-l-2 border-blue-200">
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Email</label>
                  <input
                    type="email"
                    {...form.register('email')}
                    className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <Select
                  label="Роль"
                  options={ROLE_OPTIONS}
                  {...form.register('role')}
                  disabled={editTarget?.is_system_admin}
                />
                {editTarget?.is_system_admin && (
                  <p className="text-xs text-gray-400">Системный администратор — роль изменить нельзя</p>
                )}
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input
                    type="checkbox"
                    {...form.register('is_system_admin')}
                    disabled={editTarget?.is_system_admin}
                    className="rounded"
                  />
                  Системный пользователь (скрыт из табеля)
                </label>
                {(!editTarget || !editTarget.has_access) && (
                  <div className="flex flex-col gap-1">
                    <label className="text-sm font-medium text-gray-700">
                      Начальный пароль{editTarget && !editTarget.has_access ? ' *' : ''}
                    </label>
                    <input
                      type="password"
                      {...form.register('initial_password')}
                      placeholder={editTarget && !editTarget.has_access ? 'Обязательно для нового доступа' : ''}
                      className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                )}
                {editTarget && editTarget.has_access && (
                  <div className="flex gap-2 mt-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => { closeModal(); setResetTarget(editTarget) }}
                    >
                      Сбросить пароль
                    </Button>
                    {!editTarget.is_system_admin && (
                      <Button
                        type="button"
                        size="sm"
                        variant="danger"
                        onClick={() => { closeModal(); setRevokeTarget(editTarget) }}
                      >
                        Отобрать доступ
                      </Button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
          )}
          </fieldset>
        </form>
      </Modal>

      {/* Dismiss modal */}
      {dismissTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-lg font-semibold text-gray-900">Уволить {dismissTarget.full_name}?</h2>
            <div className="mb-4 flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">Дата увольнения</label>
              <input
                type="date"
                value={dismissDate}
                onChange={(e) => setDismissDate(e.target.value)}
                className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400"
              />
            </div>
            <p className="mb-4 text-sm text-amber-700 bg-amber-50 rounded-lg p-3">
              Часы сотрудника останутся в системе. Доступ в систему будет заблокирован. Сотрудник перестанет отображаться в табеле в новых периодах.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setDismissTarget(null); setDismissDate('') }}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
              >
                Отмена
              </button>
              <button
                onClick={onDismiss}
                disabled={!dismissDate}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                Уволить
              </button>
            </div>
          </div>
        </div>
      )}

      <Confirm
        isOpen={!!resetTarget}
        onConfirm={onReset}
        onCancel={() => setResetTarget(null)}
        title="Сбросить пароль"
        message={`Сгенерировать временный пароль для «${resetTarget?.full_name}»?`}
      />

      <Confirm
        isOpen={!!revokeTarget}
        onConfirm={onRevoke}
        onCancel={() => setRevokeTarget(null)}
        title="Отобрать доступ"
        message={`Отобрать системный доступ у «${revokeTarget?.full_name}»? Email и роль будут обнулены.`}
        danger
      />

      {/* Temp password modal */}
      <Modal
        isOpen={!!tempPassword}
        onClose={() => setTempPassword(null)}
        title="Временный пароль"
        actions={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => { navigator.clipboard.writeText(tempPassword ?? ''); toast.success('Скопировано') }}
            >
              Скопировать
            </Button>
            <Button type="button" onClick={() => setTempPassword(null)}>Закрыть</Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-gray-600">Передайте пользователю этот пароль. Он будет обязан сменить его при входе.</p>
        <div className="rounded-lg bg-gray-100 px-4 py-3 font-mono text-lg tracking-widest text-gray-900">
          {tempPassword}
        </div>
      </Modal>
    </div>
  )
}
