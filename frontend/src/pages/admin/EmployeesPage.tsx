import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  listEmployees, createEmployee, updateEmployee,
  grantAccess, updateRole, resetPassword, revokeAccess,
  dismissEmployee, rehireEmployee,
  getCompanyShares, setCompanyShares,
} from '../../api/employees'
import { listDepartments } from '../../api/departments'
import { listCompanies } from '../../api/companies'
import { listSchedules } from '../../api/schedules'
import { useApi } from '../../hooks/useApi'
import { useAuth } from '../../hooks/useAuth'
import { toast } from '../../store/toasts'
import type {
  Company, Employee, EmployeePosition, EmployeeShares, UserRole,
} from '../../types/api'
import { PageHeader } from '../../components/PageHeader'
import { Table, Th, Td } from '../../components/Table'
import { Badge } from '../../components/Badge'
import { Modal } from '../../components/Modal'
import { Confirm } from '../../components/Confirm'
import { Button } from '../../components/Button'
import { EmployeeHistoryModal } from './EmployeeHistoryModal'
import { Select } from '../../components/Select'
import { SharesEditor } from '../../components/SharesEditor'
import { EmployeeImportModal } from './EmployeeImportModal'
import { PositionsEditor } from './PositionsEditor'
import { ApiError } from '../../api/client'
import { copyText } from '../../utils/clipboard'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Администратор',
  manager: 'Руководитель',
  accountant: 'Бухгалтер',
  timekeeper: 'Табельщик',
  employee: 'Сотрудник',
}

const MANAGER_LOCK_TIP = 'Только администратор может изменить'

// Табельщик (task_timekeeper_role) заполняет время своих отделов и не видит
// финансов; отделы ему привязывают в «Оргструктуре», как и руководителю.
const ROLE_OPTIONS = [
  { value: 'admin', label: 'Администратор' },
  { value: 'manager', label: 'Руководитель' },
  { value: 'accountant', label: 'Бухгалтер' },
  { value: 'timekeeper', label: 'Табельщик' },
  { value: 'employee', label: 'Сотрудник' },
]

const schema = z.object({
  tab_number: z.string().optional(),
  full_name: z.string().min(1, 'Обязательное поле'),
  position: z.string().optional(),
  department_id: z.coerce.number().optional(),
  schedule_id: z.coerce.number().optional(),
  default_company_id: z.coerce.number().optional(),
  pay_type: z.enum(['salary', 'per_shift', 'hourly']).default('salary'),
  rate: z.string().optional(),
  shift_rate: z.string().optional(),
  hour_rate: z.string().optional(),
  weekend_pay_type: z.enum(['coefficient', 'fixed_rate']).default('coefficient'),
  weekend_coefficient: z.string().optional(),
  weekend_fixed_rate: z.string().optional(),
  holiday_pay_type: z.enum(['coefficient', 'fixed_rate']).default('coefficient'),
  holiday_coefficient: z.string().optional(),
  holiday_fixed_rate: z.string().optional(),
  overtime_coefficient: z.string().optional(),
  loan_amount: z.string().optional(),
  loan_term_months: z.string().optional(),
  loan_start_date: z.string().optional(),
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

// ── Сводка по рабочим местам для списка (task_positions ч.B) ──
// Отдел и график живут на ПОЗИЦИИ, поэтому у совместителя их может быть
// несколько. Показываем уникальные значения через запятую.

const activePositionsOf = (e: Employee): EmployeePosition[] =>
  (e.positions ?? []).filter((p) => p.is_active)

const extraPositions = (e: Employee): number => Math.max(0, activePositionsOf(e).length - 1)

function uniqueNames(e: Employee, pick: (p: EmployeePosition) => string | undefined): string {
  const names = Array.from(
    new Set(activePositionsOf(e).map(pick).filter((n): n is string => !!n)),
  )
  return names.length ? names.join(', ') : '—'
}

const deptSummary = (e: Employee): string =>
  activePositionsOf(e).length ? uniqueNames(e, (p) => p.department?.name) : e.department?.name ?? '—'

const scheduleSummary = (e: Employee): string =>
  activePositionsOf(e).length ? uniqueNames(e, (p) => p.schedule?.name) : e.schedule?.name ?? '—'

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
  // История изменений по сотруднику (task_audit_log): открывается из строки
  // списка и из карточки. Только admin — журнал показывает оклады и доступы.
  const [historyTarget, setHistoryTarget] = useState<Employee | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [dismissTarget, setDismissTarget] = useState<Employee | null>(null)
  const [dismissDate, setDismissDate] = useState('')
  const [resetTarget, setResetTarget] = useState<Employee | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<Employee | null>(null)
  const [tempPassword, setTempPassword] = useState<string | null>(null)

  const form = useForm<FormInput, unknown, FormData>({ resolver: zodResolver(schema) })
  const hasAccess = form.watch('has_access')
  const weekendType = form.watch('weekend_pay_type')
  const holidayType = form.watch('holiday_pay_type')
  const payType = form.watch('pay_type')

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
      pay_type: 'salary', rate: '', shift_rate: '', hour_rate: '',
      weekend_pay_type: 'coefficient', weekend_coefficient: '1.5', weekend_fixed_rate: '',
      holiday_pay_type: 'coefficient', holiday_coefficient: '1.5', holiday_fixed_rate: '',
      overtime_coefficient: '1.5',
      loan_amount: '', loan_term_months: '', loan_start_date: '',
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
      pay_type: e.pay_type ?? 'salary',
      rate: e.rate ?? '',
      shift_rate: e.shift_rate ?? '',
      hour_rate: e.hour_rate ?? '',
      weekend_pay_type: e.weekend_pay_type ?? 'coefficient',
      weekend_coefficient: e.weekend_coefficient ?? '',
      weekend_fixed_rate: e.weekend_fixed_rate ?? '',
      holiday_pay_type: e.holiday_pay_type ?? 'coefficient',
      holiday_coefficient: e.holiday_coefficient ?? '',
      holiday_fixed_rate: e.holiday_fixed_rate ?? '',
      overtime_coefficient: e.overtime_coefficient ?? '1.5',
      loan_amount: e.loan_amount ?? '',
      loan_term_months: e.loan_term_months != null ? String(e.loan_term_months) : '',
      loan_start_date: e.loan_start_date ?? '',
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

  // Переход из дерева оргструктуры (?employee_id=N) — сразу открыть карточку.
  // Параметр снимаем, чтобы карточка не открывалась заново после закрытия.
  const [searchParams, setSearchParams] = useSearchParams()
  useEffect(() => {
    const id = Number(searchParams.get('employee_id'))
    if (!id || !employees?.length) return
    const target = employees.find((e) => e.id === id)
    if (target) openEdit(target)
    searchParams.delete('employee_id')
    setSearchParams(searchParams, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees, searchParams])

  const onSubmit = async (data: FormData) => {
    try {
      // Поля рабочего места (отдел/график/компания/оплата) при СОЗДАНИИ уходят
      // в основную позицию через compat-аксессоры бэка. При редактировании их
      // не шлём: там ими управляет PositionsEditor, и стаpые значения формы
      // затёрли бы только что сохранённую позицию.
      const positionFields = editTarget ? {} : {
        department_id: data.department_id || null,
        schedule_id: data.schedule_id || null,
        default_company_id: data.default_company_id || null,
        pay_type: data.pay_type,
        // Поле чужого типа оплаты не отправляем — бэк его всё равно обнулит.
        rate: data.pay_type === 'salary' ? (data.rate || null) : null,
        shift_rate: data.pay_type === 'per_shift' ? (data.shift_rate || null) : null,
        hour_rate: data.pay_type === 'hourly' ? (data.hour_rate || null) : null,
        weekend_pay_type: data.weekend_pay_type,
        weekend_coefficient: data.weekend_pay_type === 'coefficient' ? (data.weekend_coefficient || null) : null,
        weekend_fixed_rate: data.weekend_pay_type === 'fixed_rate' ? (data.weekend_fixed_rate || null) : null,
        holiday_pay_type: data.holiday_pay_type,
        holiday_coefficient: data.holiday_pay_type === 'coefficient' ? (data.holiday_coefficient || null) : null,
        holiday_fixed_rate: data.holiday_pay_type === 'fixed_rate' ? (data.holiday_fixed_rate || null) : null,
        overtime_coefficient: data.overtime_coefficient || null,
      }

      const payload = {
        tab_number: data.tab_number || null,
        full_name: data.full_name,
        position: data.position || null,
        ...positionFields,
        loan_amount: data.loan_amount || null,
        loan_term_months: data.loan_term_months ? Number(data.loan_term_months) : null,
        loan_start_date: data.loan_start_date || null,
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
        action={canAdmin() ? (
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setShowImport(true)}>Импорт из Excel</Button>
            <Button onClick={openCreate}>Добавить сотрудника</Button>
          </div>
        ) : undefined}
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
              {/* Совместитель — несколько рабочих мест; в списке показываем
                  основную и счётчик остальных, полный состав — в карточке. */}
              <Td>
                {e.position ?? e.positions?.find((p) => p.is_primary)?.display_title ?? '—'}
                {extraPositions(e) > 0 && (
                  <span
                    className="ml-2 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700"
                    title="Совместительство: ещё рабочие места (см. карточку)"
                  >
                    +{extraPositions(e)}
                  </span>
                )}
              </Td>
              <Td>{deptSummary(e)}</Td>
              <Td>{scheduleSummary(e)}</Td>
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
                    {canAdmin() && (
                      <Button size="sm" variant="ghost" onClick={() => setHistoryTarget(e)} title="Кто и когда менял карточку и рабочие места">История</Button>
                    )}
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

      {/* История изменений по сотруднику и его рабочим местам (task_audit_log) */}
      <EmployeeHistoryModal
        employeeId={historyTarget?.id ?? null}
        employeeName={historyTarget?.full_name ?? ''}
        isOpen={!!historyTarget}
        onClose={() => setHistoryTarget(null)}
      />

      {/* Импорт из Excel (task_employee_import) */}
      <EmployeeImportModal
        isOpen={showImport}
        onClose={() => setShowImport(false)}
        onImported={refetch}
      />

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

          {/* ── Должности (позиции) — task_positions ч.B ──
              Только в режиме редактирования: у существующего сотрудника рабочих
              мест может быть несколько, и каждое со своими условиями. При
              создании ниже идут «плоские» поля — они заводят основную позицию. */}
          {editTarget && (
            <PositionsEditor
              employeeId={editTarget.id}
              departments={departments ?? []}
              companies={companies ?? []}
              schedules={schedules ?? []}
              readOnly={readOnly}
              onChanged={refetch}
            />
          )}

          {/* Section 2 — Structure (только при создании: это основная позиция) */}
          {!editTarget && (
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
          )}

          {/* Section 3 — Employment */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Трудовая занятость</p>
            <div className="flex flex-col gap-3">
              {/* Тип оплаты и его база — свойство ПОЗИЦИИ; при редактировании
                  они живут в секции «Должности (позиции)» выше. */}
              {!editTarget && (
              <div className="flex flex-col gap-2">
                <label className="text-sm font-medium text-gray-700">Тип оплаты</label>
                <div className="flex gap-4 text-sm">
                  <label className="flex items-center gap-1.5 text-gray-700 cursor-pointer">
                    <input type="radio" value="salary" {...form.register('pay_type')} />
                    Окладная
                  </label>
                  <label className="flex items-center gap-1.5 text-gray-700 cursor-pointer">
                    <input type="radio" value="per_shift" {...form.register('pay_type')} />
                    Посменная
                  </label>
                  <label className="flex items-center gap-1.5 text-gray-700 cursor-pointer">
                    <input type="radio" value="hourly" {...form.register('pay_type')} />
                    Почасовая
                  </label>
                </div>
              </div>
              )}
              {!editTarget && payType === 'per_shift' && (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Ставка за смену (₽)</label>
                  <input {...form.register('shift_rate')} placeholder="2500" className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                  <p className="text-xs text-gray-400">
                    Оклада нет: база — смены плановых дней графика. Смена в выходной
                    или праздник оплачивается ставкой × коэффициент.
                  </p>
                </div>
              )}
              {!editTarget && payType === 'hourly' && (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Ставка за час (₽)</label>
                  <input {...form.register('hour_rate')} placeholder="450" className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                  <p className="text-xs text-gray-400">
                    Платим за фактические часы; отпускные и больничные не начисляются.
                  </p>
                </div>
              )}
              {!editTarget && payType === 'salary' && (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Оклад (₽)</label>
                  <input {...form.register('rate')} placeholder="50000" className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
              )}
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

          {/* Section 3b — оплата выхода в свой выходной по графику.
              Коэффициенты — свойство ПОЗИЦИИ: при редактировании они в секции
              «Должности (позиции)», здесь остаются только для создания. */}
          {!editTarget && (
          <>
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Оплата работы вне графика</p>
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
              <p className="text-xs text-gray-400">
                Выход в свой законный выходной по графику. Работа в праздник считается
                отдельно — настройка ниже.
              </p>
            </div>
          </div>

          {/* Section 3b-1 — оплата работы в нерабочий праздничный день */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Оплата праздничных</p>
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-2">
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="radio" value="coefficient" {...form.register('holiday_pay_type')} />
                  По коэффициенту
                </label>
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="radio" value="fixed_rate" {...form.register('holiday_pay_type')} />
                  Фиксированная ставка за час
                </label>
              </div>
              {holidayType === 'fixed_rate' ? (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Ставка за час в праздник (₽)</label>
                  <input
                    {...form.register('holiday_fixed_rate')}
                    placeholder="740"
                    className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              ) : (
                <div className="flex flex-col gap-1">
                  <label className="text-sm font-medium text-gray-700">Коэффициент оплаты праздничных</label>
                  <input
                    {...form.register('holiday_coefficient')}
                    placeholder="1.5"
                    className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <p className="text-xs text-gray-400">1.5 = полуторный, 2 = двойной, 0 = не оплачивается дополнительно</p>
                </div>
              )}
            </div>
          </div>

          {/* Section 3b-2 — Коэффициент переработки (задача 3.11b п.0) */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Переработка</p>
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">Коэффициент переработки</label>
              <input
                {...form.register('overtime_coefficient')}
                placeholder="1.5"
                className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-400">
                Часы переработки считаются по дням (сверх дневной нормы смены), оплата: (оклад/норма) × часы переработки × коэффициент. 1.5 = полуторный, 1 = одинарный, 0 = не оплачивается
              </p>
            </div>
          </div>
          </>
          )}

          {/* Section 3b-3 — Распределение затрат по юрлицам по умолчанию (3.11b п.1).
              Проценты задаются РАБОЧЕМУ МЕСТУ: у совместителя каждое разносится
              по юрлицам отдельно. */}
          {editTarget && !isMgr && (
            <CompanySharesEditor
              employeeId={editTarget.id}
              companies={companies ?? []}
              positions={editTarget.positions ?? []}
            />
          )}

          {/* Section 3c — Заём (задача 3.11a). Гасится равными долями автоматически. */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">Заём</p>
            <div className="grid grid-cols-3 gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">Сумма (₽)</label>
                <input {...form.register('loan_amount')} placeholder="12000" className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">Срок (мес.)</label>
                <input type="number" min={1} {...form.register('loan_term_months')} placeholder="12" className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">Начало погашения</label>
                <input type="date" {...form.register('loan_start_date')} className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
            </div>
            <p className="mt-1 text-xs text-gray-400">
              Гасится равными долями (сумма ÷ срок) автоматически с месяца начала. Удержание за конкретный месяц можно скорректировать в табеле.
            </p>
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
              onClick={async () => {
                const ok = await copyText(tempPassword ?? '')
                if (ok) toast.success('Скопировано')
                else toast.error('Не удалось скопировать')
              }}
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

// ── Распределение затрат по юрлицам по умолчанию (задача 3.11b п.1) ──
// Проценты принадлежат РАБОЧЕМУ МЕСТУ (task_positions): у совместителя каждое
// разносится по юрлицам отдельно, поэтому при нескольких позициях появляется
// селектор. С одной позицией экран выглядит как раньше.
function CompanySharesEditor({
  employeeId, companies, positions,
}: { employeeId: number; companies: Company[]; positions: EmployeePosition[] }) {
  const activePositions = positions.filter((p) => p.is_active)
  const [positionId, setPositionId] = useState<number | null>(
    activePositions.find((p) => p.is_primary)?.id ?? activePositions[0]?.id ?? null,
  )
  const [shares, setShares] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadedAt, setLoadedAt] = useState(0)
  const [inherited, setInherited] = useState<EmployeeShares | null>(null)

  const mainCompanyId =
    activePositions.find((p) => p.id === positionId)?.company_id ?? null

  const load = useCallback(() => {
    setLoading(true)
    getCompanyShares(employeeId, positionId)
      .then((data) => {
        const map: Record<number, string> = {}
        for (const s of data.shares) map[s.company_id] = s.percent
        setShares(map)
        setInherited(data)
        setLoadedAt((n) => n + 1)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [employeeId, positionId])

  useEffect(load, [load])

  const save = async () => {
    const list = Object.entries(shares)
      .filter(([, v]) => (Number(v) || 0) > 0)
      .map(([cid, v]) => ({ company_id: Number(cid), percent: String(Number(v)) }))
    try {
      setSaving(true)
      const saved = await setCompanyShares(employeeId, list, positionId)
      setInherited(saved)
      toast.success(
        list.length === 0 && saved.inherits_department
          ? 'Своё распределение убрано — используется дефолт отдела'
          : 'Распределение по умолчанию сохранено',
      )
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Ошибка сохранения')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return null

  // Наследование от отдела (task_distribution_v2 ч.3): своё распределение пусто —
  // значит применяется дефолт отдела; показываем какой именно.
  const companyName = (id: number) => companies.find((c) => c.id === id)?.name ?? `#${id}`
  const deptHint = inherited?.inherits_department
    ? inherited.department_shares
        .map((s) => `${companyName(s.company_id)} — ${Number(s.percent)}%`)
        .join(', ')
    : null

  return (
    <div>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
        Распределение затрат по юрлицам (по умолчанию)
      </p>
      {activePositions.length > 1 && (
        <select
          value={positionId ?? ''}
          onChange={(e) => setPositionId(e.target.value ? Number(e.target.value) : null)}
          className="mb-2 rounded-lg border border-gray-300 px-2 py-1.5 text-sm"
          title="Проценты задаются отдельно каждому рабочему месту"
        >
          {activePositions.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_title}{p.is_primary ? ' (основная)' : ''}
            </option>
          ))}
        </select>
      )}
      {deptHint && (
        <div className="mb-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          Своё распределение не задано — используется дефолт отдела
          {inherited?.department_name ? ` «${inherited.department_name}»` : ''}: {deptHint}.
          Заполните проценты ниже, чтобы задать индивидуальное (оно перекроет отдел).
        </div>
      )}
      {inherited && !inherited.inherits_department && inherited.department_shares.length > 0
        && Number(inherited.percent_sum) > 0 && (
        <div className="mb-2 text-[11px] text-gray-400">
          Задано индивидуальное распределение — дефолт отдела
          {inherited.department_name ? ` «${inherited.department_name}»` : ''} не применяется.
          Очистите проценты и сохраните, чтобы вернуться к отделу.
        </div>
      )}
      <div className="flex flex-col gap-2">
        <SharesEditor
          companies={companies}
          shares={shares}
          onChange={setShares}
          mainCompanyId={mainCompanyId}
          resetKey={`${employeeId}-${positionId ?? 0}-${loadedAt}`}
        />
        <div>
          <Button type="button" variant="secondary" size="sm" onClick={save} disabled={saving}>
            Сохранить распределение
          </Button>
        </div>
      </div>
    </div>
  )
}
