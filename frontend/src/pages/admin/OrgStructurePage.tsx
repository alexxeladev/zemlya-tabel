/**
 * Единый экран «Оргструктура» (task_org_structure ч.3).
 *
 * Дерево Компания → Отдел → Сотрудники. Заменяет разрозненные вкладки
 * «Компании» и «Отделы»: справочники, головная компания отдела и назначение
 * менеджеров живут в одном месте.
 *
 * ВАЖНО: головная компания отдела — только группировка узлов дерева. Расчёт ЗП
 * остаётся мультикомпанийным (часы по компаниям + проценты распределения) и на
 * положение отдела в дереве не смотрит.
 *
 * Производительность: сотрудники отдела по умолчанию СВЁРНУТЫ и не рендерятся
 * (в узле только счётчик), иначе при 100+ сотрудниках дерево разворачивается в
 * гигантский DOM.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCompany, updateCompany, deleteCompany } from '../../api/companies'
import {
  createDepartment,
  updateDepartment,
  deleteDepartment,
  setDepartmentManagers,
  getDepartmentShares,
  setDepartmentShares,
  previewDepartmentMove,
  moveDepartment,
} from '../../api/departments'
import { listCompanies } from '../../api/companies'
import { listEmployees } from '../../api/employees'
import { getOrgTree } from '../../api/org'
import { ApiError } from '../../api/client'
import { useApi } from '../../hooks/useApi'
import { toast } from '../../store/toasts'
import type {
  Company,
  CompanyShare,
  DepartmentMovePreview,
  OrgCompany,
  OrgDepartment,
  OrgEmployee,
} from '../../types/api'
import { SharesEditor, type SharesMap } from '../../components/SharesEditor'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { Confirm } from '../../components/Confirm'
import { Modal } from '../../components/Modal'
import { PageHeader } from '../../components/PageHeader'

type CompanyForm = { id?: number; code: string; name: string; inn: string }
type DepartmentForm = {
  id?: number
  name: string
  code: string
  head_company_id: number | null
  /** фонд ночных смен на месяц; из него считаются ставка и лимит числа смен */
  night_shift_fund: string
  /** делить зарплату отдела по заявкам на подбор вместо каскада процентов */
  uses_applications_distribution: boolean
}

/** Ставка ночной смены = фонд ÷ календарные дни месяца, лимит смен = число дней
 *  месяца (task_night_shifts_rework). Считаем здесь только для подсказки в форме —
 *  авторитетно то же самое считает бэк. */
function nightHint(fund: string): string | null {
  const value = Number(String(fund).replace(',', '.'))
  if (!Number.isFinite(value) || value <= 0) return null
  const now = new Date()
  const days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
  const rate = (value / days).toFixed(2)
  return `в этом месяце (${days} дн.) — ${rate} ₽ за смену, не более ${days} смен на отдел`
}

const err = (e: unknown) => (e instanceof ApiError ? e.message : 'Ошибка')

// ── Узлы дерева ──────────────────────────────────────────────────────────────

function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className={`inline-block w-3 text-gray-400 transition-transform ${open ? 'rotate-90' : ''}`}
    >
      ▶
    </span>
  )
}

function EmployeeRow({ emp, onOpen }: { emp: OrgEmployee; onOpen: (id: number) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(emp.id)}
      className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-gray-50"
    >
      <span className="text-gray-300">•</span>
      <span className="text-gray-800">{emp.full_name}</span>
      {emp.tab_number && <span className="font-mono text-xs text-gray-400">{emp.tab_number}</span>}
      {emp.position && <span className="text-xs text-gray-500">{emp.position}</span>}
      {emp.role === 'manager' && <Badge variant="blue">рук.</Badge>}
      {emp.role === 'timekeeper' && <Badge variant="gray">таб.</Badge>}
    </button>
  )
}

interface DeptNodeProps {
  dept: OrgDepartment
  onEdit: (d: OrgDepartment) => void
  onDelete: (d: OrgDepartment) => void
  onManagers: (d: OrgDepartment) => void
  onMove: (d: OrgDepartment) => void
  onOpenEmployee: (id: number) => void
}

function DepartmentNode({
  dept,
  onEdit,
  onDelete,
  onManagers,
  onMove,
  onOpenEmployee,
}: DeptNodeProps) {
  const [open, setOpen] = useState(false)

  return (
    <div className="border-l border-gray-200 pl-3">
      <div className="group flex items-center gap-2 py-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left"
          disabled={dept.employee_count === 0}
        >
          <Chevron open={open} />
          <span className="text-sm font-medium text-gray-900">{dept.name}</span>
          <span className="font-mono text-xs text-gray-400">{dept.code}</span>
          <span className="text-xs text-gray-500">
            {dept.employee_count} чел.
          </span>
          {dept.night_shift_fund != null && (
            <span
              className="text-xs text-indigo-500"
              title="Фонд ночных смен: из него считаются ставка смены и лимит их числа за месяц"
            >
              🌙 {Math.round(Number(dept.night_shift_fund)).toLocaleString('ru-RU')} ₽/мес
            </span>
          )}
          {dept.uses_applications_distribution && (
            <span
              className="text-xs text-emerald-600"
              title="Зарплата отдела распределяется по числу заявок на подбор за месяц, а не по каскаду процентов"
            >
              📋 по заявкам
            </span>
          )}
        </button>

        <div className="ml-auto flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <Button size="sm" variant="ghost" onClick={() => onManagers(dept)}>
            Ответственные
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onMove(dept)}>
            Перенести
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onEdit(dept)}>
            Изменить
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onDelete(dept)}>
            Удалить
          </Button>
        </div>
      </div>

      {/* Ответственные видны всегда: это ответ на вопрос «кто ведёт отдел».
          Руководитель и табельщик сидят в одной связи, различает их роль
          (task_timekeeper_role), поэтому чипы подписаны. */}
      <div className="flex flex-wrap items-center gap-1 pl-5 pb-1 text-xs">
        <span className="text-gray-400">Ответственные:</span>
        {dept.managers.length === 0 ? (
          <button
            type="button"
            onClick={() => onManagers(dept)}
            className="text-gray-400 italic hover:text-blue-600"
          >
            не назначены
          </button>
        ) : (
          dept.managers.map((m) =>
            m.role === 'timekeeper' ? (
              <span key={m.id} className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">
                {m.full_name} · табельщик
              </span>
            ) : (
              <span key={m.id} className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-700">
                {m.full_name}
              </span>
            ),
          )
        )}
      </div>

      {open && (
        <div className="pl-5 pb-1">
          {dept.employees.map((e) => (
            <EmployeeRow key={e.id} emp={e} onOpen={onOpenEmployee} />
          ))}
        </div>
      )}
    </div>
  )
}

interface CompanyNodeProps extends Omit<DeptNodeProps, 'dept'> {
  company: OrgCompany
  onEditCompany: (c: OrgCompany) => void
  onDeleteCompany: (c: OrgCompany) => void
  onAddDepartment: (companyId: number) => void
}

function CompanyNode({
  company,
  onEditCompany,
  onDeleteCompany,
  onAddDepartment,
  ...deptProps
}: CompanyNodeProps) {
  const [open, setOpen] = useState(true)
  const total = company.departments.reduce((sum, d) => sum + d.employee_count, 0)

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="group flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-left"
        >
          <Chevron open={open} />
          <span className="font-semibold text-gray-900">{company.name}</span>
          <span className="font-mono text-xs text-gray-400">{company.code}</span>
          <span className="text-xs text-gray-500">
            {company.departments.length} отд. · {total} чел.
          </span>
        </button>

        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="secondary" onClick={() => onAddDepartment(company.id)}>
            + отдел
          </Button>
          <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <Button size="sm" variant="ghost" onClick={() => onEditCompany(company)}>
              Изменить
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onDeleteCompany(company)}>
              Удалить
            </Button>
          </div>
        </div>
      </div>

      {open && (
        <div className="px-3 pb-2 pl-6">
          {company.departments.length === 0 ? (
            <p className="py-1 text-sm text-gray-400 italic">Отделов нет</p>
          ) : (
            company.departments.map((d) => (
              <DepartmentNode key={d.id} dept={d} {...deptProps} />
            ))
          )}
        </div>
      )}
    </div>
  )
}

// ── Страница ─────────────────────────────────────────────────────────────────

export function OrgStructurePage() {
  const navigate = useNavigate()
  const { data: tree, isLoading, refetch } = useApi(getOrgTree)

  const [companyForm, setCompanyForm] = useState<CompanyForm | null>(null)
  const [deptForm, setDeptForm] = useState<DepartmentForm | null>(null)
  const [managersFor, setManagersFor] = useState<OrgDepartment | null>(null)
  // Перенос отдела в другую компанию (task_move_department).
  const [moveFor, setMoveFor] = useState<OrgDepartment | null>(null)
  // Дефолт распределения по юрлицам на уровне отдела (task_distribution_v2 ч.3):
  // редактируется здесь же, чтобы вместе с вкладкой «Отделы» не потерялся.
  const { data: allCompanies } = useApi(listCompanies)
  const [shares, setShares] = useState<SharesMap>({})
  const [sharesKey, setSharesKey] = useState(0)
  const [deleteCompanyTarget, setDeleteCompanyTarget] = useState<OrgCompany | null>(null)
  const [deleteDeptTarget, setDeleteDeptTarget] = useState<OrgDepartment | null>(null)
  const [saving, setSaving] = useState(false)

  const companies = useMemo(() => tree?.companies ?? [], [tree])

  const openEmployee = (id: number) => navigate(`/admin/employees?employee_id=${id}`)

  const saveCompany = async () => {
    if (!companyForm) return
    const payload = {
      code: companyForm.code.trim(),
      name: companyForm.name.trim(),
      inn: companyForm.inn.trim() || null,
    }
    if (!payload.code || !payload.name) {
      toast.error('Код и название обязательны')
      return
    }
    setSaving(true)
    try {
      if (companyForm.id) {
        await updateCompany(companyForm.id, payload)
        toast.success('Компания обновлена')
      } else {
        await createCompany(payload)
        toast.success('Компания создана')
      }
      setCompanyForm(null)
      refetch()
    } catch (e) {
      toast.error(err(e))
    } finally {
      setSaving(false)
    }
  }

  const saveDepartment = async () => {
    if (!deptForm) return
    const payload = {
      name: deptForm.name.trim(),
      code: deptForm.code.trim(),
      head_company_id: deptForm.head_company_id,
      night_shift_fund: String(Number(deptForm.night_shift_fund.replace(',', '.')) || 0),
      uses_applications_distribution: deptForm.uses_applications_distribution,
    }
    if (!payload.name || !payload.code) {
      toast.error('Название и код обязательны')
      return
    }
    const shareList: CompanyShare[] = Object.entries(shares)
      .filter(([, v]) => (Number(v) || 0) > 0)
      .map(([cid, v]) => ({ company_id: Number(cid), percent: String(Number(v)) }))

    setSaving(true)
    try {
      if (deptForm.id) {
        await updateDepartment(deptForm.id, payload)
        // Пустой список очищает дефолт отдела — сотрудники уходят на авто по часам.
        await setDepartmentShares(deptForm.id, shareList)
        toast.success('Отдел обновлён')
      } else {
        const created = await createDepartment(payload)
        if (shareList.length > 0) await setDepartmentShares(created.id, shareList)
        toast.success('Отдел создан')
      }
      setDeptForm(null)
      refetch()
    } catch (e) {
      toast.error(err(e))
    } finally {
      setSaving(false)
    }
  }

  const onDeleteCompany = async () => {
    if (!deleteCompanyTarget) return
    try {
      await deleteCompany(deleteCompanyTarget.id)
      toast.success('Компания деактивирована')
      refetch()
    } catch (e) {
      toast.error(err(e))
    } finally {
      setDeleteCompanyTarget(null)
    }
  }

  const onDeleteDept = async () => {
    if (!deleteDeptTarget) return
    try {
      await deleteDepartment(deleteDeptTarget.id)
      toast.success('Отдел деактивирован')
      refetch()
    } catch (e) {
      toast.error(err(e))
    } finally {
      setDeleteDeptTarget(null)
    }
  }

  const openDeptForm = (form: DepartmentForm) => {
    setDeptForm(form)
    setShares({})
    if (!form.id) {
      setSharesKey((n) => n + 1)
      return
    }
    getDepartmentShares(form.id)
      .then((d) => {
        const map: SharesMap = {}
        for (const sh of d.shares) map[sh.company_id] = sh.percent
        setShares(map)
      })
      .catch(() => {})
      .finally(() => setSharesKey((n) => n + 1))
  }

  const deptNodeProps = {
    onEdit: (d: OrgDepartment) =>
      openDeptForm({
        id: d.id,
        name: d.name,
        code: d.code,
        head_company_id: d.head_company_id,
        night_shift_fund: d.night_shift_fund ?? '100000',
        uses_applications_distribution: d.uses_applications_distribution,
      }),
    onDelete: setDeleteDeptTarget,
    onManagers: setManagersFor,
    onMove: setMoveFor,
    onOpenEmployee: openEmployee,
  }

  const orphanDepts = tree?.departments_without_company ?? []
  const orphanEmployees = tree?.employees_without_department ?? []

  return (
    <div>
      <PageHeader
        title="Оргструктура"
        description="Компания → Отдел → Сотрудники. Головная компания отдела — группировка в дереве; на расчёт ЗП и мультикомпанию сотрудников не влияет."
        action={
          <Button onClick={() => setCompanyForm({ code: '', name: '', inn: '' })}>
            + Компания
          </Button>
        }
      />

      {isLoading && <p className="text-sm text-gray-500">Загрузка…</p>}

      <div className="flex flex-col gap-2">
        {companies.map((c) => (
          <CompanyNode
            key={c.id}
            company={c}
            onEditCompany={(comp) =>
              setCompanyForm({
                id: comp.id,
                code: comp.code,
                name: comp.name,
                inn: comp.inn ?? '',
              })
            }
            onDeleteCompany={setDeleteCompanyTarget}
            onAddDepartment={(companyId) =>
              openDeptForm({
                name: '', code: '', head_company_id: companyId,
                night_shift_fund: '100000', uses_applications_distribution: false,
              })
            }
            {...deptNodeProps}
          />
        ))}

        {/* Отделы без головной компании — иначе их не видно и не починить */}
        {orphanDepts.length > 0 && (
          <div className="rounded-lg border border-dashed border-gray-300 bg-white">
            <div className="px-3 py-2 text-sm font-semibold text-gray-500">
              Без головной компании
            </div>
            <div className="px-3 pb-2 pl-6">
              {orphanDepts.map((d) => (
                <DepartmentNode key={d.id} dept={d} {...deptNodeProps} />
              ))}
            </div>
          </div>
        )}

        {orphanEmployees.length > 0 && (
          <div className="rounded-lg border border-dashed border-gray-300 bg-white">
            <div className="px-3 py-2 text-sm font-semibold text-gray-500">
              Сотрудники без отдела ({orphanEmployees.length})
            </div>
            <div className="px-3 pb-2 pl-6">
              {orphanEmployees.map((e) => (
                <EmployeeRow key={e.id} emp={e} onOpen={openEmployee} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Компания ── */}
      <Modal
        isOpen={!!companyForm}
        onClose={() => setCompanyForm(null)}
        title={companyForm?.id ? 'Изменить компанию' : 'Новая компания'}
        actions={
          <>
            <Button variant="ghost" onClick={() => setCompanyForm(null)}>
              Отмена
            </Button>
            <Button onClick={saveCompany} loading={saving}>
              Сохранить
            </Button>
          </>
        }
      >
        {companyForm && (
          <div className="flex flex-col gap-3">
            <Field label="Название">
              <input
                className={inputCls}
                value={companyForm.name}
                onChange={(e) => setCompanyForm({ ...companyForm, name: e.target.value })}
                placeholder="ЗемляМО"
              />
            </Field>
            <Field label="Код (до 5 символов)">
              <input
                className={inputCls}
                value={companyForm.code}
                maxLength={5}
                onChange={(e) => setCompanyForm({ ...companyForm, code: e.target.value })}
                placeholder="zmo"
              />
            </Field>
            <Field label="ИНН">
              <input
                className={inputCls}
                value={companyForm.inn}
                onChange={(e) => setCompanyForm({ ...companyForm, inn: e.target.value })}
                placeholder="необязательно"
              />
            </Field>
          </div>
        )}
      </Modal>

      {/* ── Отдел ── */}
      <Modal
        isOpen={!!deptForm}
        onClose={() => setDeptForm(null)}
        title={deptForm?.id ? 'Изменить отдел' : 'Новый отдел'}
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeptForm(null)}>
              Отмена
            </Button>
            <Button onClick={saveDepartment} loading={saving}>
              Сохранить
            </Button>
          </>
        }
      >
        {deptForm && (
          <div className="flex flex-col gap-3">
            <Field label="Название">
              <input
                className={inputCls}
                value={deptForm.name}
                onChange={(e) => setDeptForm({ ...deptForm, name: e.target.value })}
                placeholder="ИТО"
              />
            </Field>
            <Field label="Код">
              <input
                className={inputCls}
                value={deptForm.code}
                onChange={(e) => setDeptForm({ ...deptForm, code: e.target.value })}
                placeholder="ITO"
              />
            </Field>
            <Field label="Головная компания">
              <select
                className={inputCls}
                value={deptForm.head_company_id ?? ''}
                onChange={(e) =>
                  setDeptForm({
                    ...deptForm,
                    head_company_id: e.target.value === '' ? null : Number(e.target.value),
                  })
                }
              >
                <option value="">— без компании —</option>
                {companies.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-[11px] text-gray-400">
                Только группировка в дереве. Сотрудники отдела по-прежнему могут работать
                на любые юрлица — часы и распределение процентов это не ограничивает.
              </p>
            </Field>

            {/* Фонд ночных смен (task_night_shifts_rework): задаёт и цену смены,
                и сколько их всего можно отметить по отделу за месяц. */}
            <Field label="Фонд ночных смен, ₽/мес">
              <input
                className={inputCls}
                value={deptForm.night_shift_fund}
                onChange={(e) => setDeptForm({ ...deptForm, night_shift_fund: e.target.value })}
                placeholder="100000"
                inputMode="decimal"
              />
              <p className="mt-1 text-[11px] text-gray-400">
                Ставка ночной смены вычисляется как фонд ÷ календарные дни месяца и
                вручную не задаётся. Столько же смен — предел на весь отдел за месяц:
                больше отметить нельзя.
                {nightHint(deptForm.night_shift_fund) && (
                  <span className="block text-gray-500">
                    {nightHint(deptForm.night_shift_fund)}
                  </span>
                )}
              </p>
            </Field>

            {/* Распределение по заявкам на подбор (task_hr_applications).
                Флаг, а не имя отдела: правило может понадобиться не только HR. */}
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-gray-300"
                checked={deptForm.uses_applications_distribution}
                onChange={(e) =>
                  setDeptForm({
                    ...deptForm,
                    uses_applications_distribution: e.target.checked,
                  })
                }
              />
              <span className="text-sm text-gray-700">
                Распределение по заявкам на подбор
                <span className="mt-0.5 block text-[11px] text-gray-400">
                  Зарплата сотрудников отдела делится между юрлицами по числу заявок,
                  отработанных за месяц (заявки вводятся в табеле отдела). Каскад ниже
                  для такого отдела НЕ применяется — он остаётся запасным вариантом на
                  месяцы, когда заявки не заведены.
                </span>
              </span>
            </label>

            {/* Дефолт распределения затрат по юрлицам (task_distribution_v2 ч.3).
                Это и есть «на кого работают» в деньгах — в отличие от головной
                компании выше, которая на расчёт не влияет. */}
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
                Распределение по компаниям по умолчанию
              </p>
              <SharesEditor
                companies={allCompanies ?? []}
                shares={shares}
                onChange={setShares}
                resetKey={sharesKey}
              />
              <p className="mt-2 text-[11px] text-gray-400">
                Наследуют сотрудники отдела, у которых НЕ задано своё распределение
                (в карточке или правкой на месяц). Пусто — распределение считается
                автоматически по фактическим часам табеля.
              </p>
            </div>
          </div>
        )}
      </Modal>

      {moveFor && (
        <MoveDepartmentModal
          dept={moveFor}
          companies={allCompanies ?? []}
          onClose={() => setMoveFor(null)}
          onMoved={() => {
            setMoveFor(null)
            refetch()
          }}
        />
      )}

      {managersFor && (
        <ManagersModal
          dept={managersFor}
          onClose={() => setManagersFor(null)}
          onSaved={() => {
            setManagersFor(null)
            refetch()
          }}
        />
      )}

      <Confirm
        isOpen={!!deleteCompanyTarget}
        onConfirm={onDeleteCompany}
        onCancel={() => setDeleteCompanyTarget(null)}
        title="Удалить компанию"
        message={`Деактивировать компанию «${deleteCompanyTarget?.name}»?`}
        danger
      />
      <Confirm
        isOpen={!!deleteDeptTarget}
        onConfirm={onDeleteDept}
        onCancel={() => setDeleteDeptTarget(null)}
        title="Удалить отдел"
        message={`Деактивировать отдел «${deleteDeptTarget?.name}»?`}
        danger
      />
    </div>
  )
}

// ── Назначение менеджеров отдела (ч.2) ───────────────────────────────────────

function ManagersModal({
  dept,
  onClose,
  onSaved,
}: {
  dept: OrgDepartment
  onClose: () => void
  onSaved: () => void
}) {
  const { data: employees, isLoading } = useApi(() => listEmployees())
  const [selected, setSelected] = useState<number[]>(dept.managers.map((m) => m.id))
  const [saving, setSaving] = useState(false)

  // Привязать к отделу можно только руководителя или табельщика — бэк отвергает
  // остальных, поэтому и в списке их нет. Связь у обоих одна и та же
  // (managed_departments), разница в правах: табельщик не видит финансов и не
  // отправляет период на проверку (task_timekeeper_role).
  const candidates = useMemo(
    () =>
      (employees ?? []).filter(
        (e) => (e.role === 'manager' || e.role === 'timekeeper') && e.is_active,
      ),
    [employees],
  )

  const toggle = (id: number) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  const save = async () => {
    setSaving(true)
    try {
      await setDepartmentManagers(dept.id, selected)
      toast.success('Менеджеры отдела сохранены')
      onSaved()
    } catch (e) {
      toast.error(err(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={`Руководители и табельщики отдела «${dept.name}»`}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button onClick={save} loading={saving}>
            Сохранить
          </Button>
        </>
      }
    >
      <p className="mb-3 text-xs text-gray-500">
        Руководитель видит табель, расчёт ЗП и сотрудников всех отделов, которыми
        руководит, и отправляет период на проверку. Табельщик ведёт время тех же
        отделов, но не видит финансов и период не отправляет. Отдел, где они сами
        числятся, к этому отношения не имеет.
      </p>

      {isLoading && <p className="text-sm text-gray-500">Загрузка…</p>}
      {!isLoading && candidates.length === 0 && (
        <p className="text-sm text-gray-500">
          Нет сотрудников с ролью «Руководитель» или «Табельщик». Выдайте роль в карточке
          сотрудника.
        </p>
      )}

      <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
        {candidates.map((e) => (
          <label
            key={e.id}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-gray-50"
          >
            <input
              type="checkbox"
              checked={selected.includes(e.id)}
              onChange={() => toggle(e.id)}
            />
            <span className="text-gray-800">{e.full_name}</span>
            {e.role === 'timekeeper' ? (
              <Badge variant="gray">табельщик</Badge>
            ) : (
              <Badge variant="blue">руководитель</Badge>
            )}
            {e.position && <span className="text-xs text-gray-500">{e.position}</span>}
            {e.department && (
              <span className="ml-auto text-xs text-gray-400">числится: {e.department.name}</span>
            )}
          </label>
        ))}
      </div>
    </Modal>
  )
}

// ── Мелочи ───────────────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-gray-700">{label}</label>
      {children}
    </div>
  )
}


// ── Перенос отдела в другую компанию (task_move_department) ──────────────────

/** Месяц закрытого периода — «05.2026». */
function fmtMonth(m: { year: number; month: number }): string {
  return `${String(m.month).padStart(2, '0')}.${m.year}`
}

/**
 * Диалог переноса. Предпросмотр тянется с бэка при каждом выборе компании —
 * считать «сколько позиций переедет» на фронте нельзя: дерево знает про
 * сотрудников отдела, но не про их рабочие места в других отделах, а именно они
 * и НЕ должны переехать.
 */
function MoveDepartmentModal({
  dept,
  companies,
  onClose,
  onMoved,
}: {
  dept: OrgDepartment
  companies: Company[]
  onClose: () => void
  onMoved: () => void
}) {
  const [target, setTarget] = useState<number | null>(null)
  const [preview, setPreview] = useState<DepartmentMovePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  // Целевая компания — любая активная, кроме текущей головной: перенос «в себя»
  // бэк отвергает.
  const options = useMemo(
    () => companies.filter((c) => c.is_active && c.id !== dept.head_company_id),
    [companies, dept.head_company_id],
  )

  useEffect(() => {
    if (target == null) {
      setPreview(null)
      return
    }
    let cancelled = false
    setLoading(true)
    previewDepartmentMove(dept.id, target)
      .then((p) => {
        if (!cancelled) setPreview(p)
      })
      .catch((e) => {
        if (!cancelled) {
          setPreview(null)
          toast.error(err(e))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [dept.id, target])

  const submit = async () => {
    if (target == null) return
    setSaving(true)
    try {
      const res = await moveDepartment(dept.id, target)
      toast.success(
        `Отдел перенесён: рабочих мест ${res.positions_moved}` +
          (res.entries_reattributed > 0
            ? `, ячеек часов ${res.entries_reattributed}`
            : '') +
          (res.closed_months_frozen > 0
            ? `, закреплено закрытых месяцев ${res.closed_months_frozen}`
            : ''),
      )
      onMoved()
    } catch (e) {
      toast.error(err(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={`Перенести отдел «${dept.name}»`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Отмена
          </Button>
          <Button onClick={submit} disabled={target == null || saving || loading}>
            {saving ? 'Переносим…' : 'Перенести'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label="Целевая компания">
          <select
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
            value={target ?? ''}
            onChange={(e) => setTarget(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">— выберите компанию —</option>
            {options.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>

        {loading && <p className="text-sm text-gray-500">Считаем, что будет затронуто…</p>}

        {preview && !loading && (
          <>
            <div className="rounded border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
              <p className="text-gray-900">
                Отдел <b>{preview.department_name}</b>
                {preview.source_company_name && (
                  <> из «{preview.source_company_name}»</>
                )}{' '}
                → компания <b>{preview.target_company_name}</b>
              </p>
              <p className="mt-1 text-gray-600">
                Сотрудников: <b>{preview.employee_count}</b>, рабочих мест переедет:{' '}
                <b>{preview.position_count}</b>
              </p>
              {preview.untouched_position_count > 0 && (
                <p className="mt-1 text-gray-600">
                  Рабочих мест этих же людей в других отделах:{' '}
                  <b>{preview.untouched_position_count}</b> — останутся на своих компаниях
                </p>
              )}
            </div>

            {/* Смена расчётной привязки — главное, что нужно понимать до нажатия */}
            <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <p>
                Смена действует <b>с текущего месяца вперёд</b>: незакрытый и будущие
                периоды будут считаться на новую компанию.
              </p>
              {preview.entries_to_reattribute > 0 && (
                <p className="mt-1">
                  Уже введённых ячеек часов сменят юрлицо на «
                  {preview.target_company_name}»: <b>{preview.entries_to_reattribute}</b>.
                  Часы, отработанные на другие юрлица, остаются как есть.
                </p>
              )}
              {preview.closed_months.length > 0 && (
                <p className="mt-1">
                  Закрытых месяцев: <b>{preview.closed_months.length}</b> (
                  {preview.closed_months.map(fmtMonth).join(', ')}) — их часы и расклад
                  по юрлицам не тронем, они останутся как посчитаны.
                </p>
              )}
            </div>

            {(preview.stale_share_position_count > 0 || preview.department_shares_stale) && (
              <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                <p className="font-medium">Проверьте распределение по юрлицам</p>
                {preview.stale_share_position_count > 0 && (
                  <p className="mt-1">
                    У {preview.stale_share_position_count} рабочих мест в карточке задан
                    явный процент, и «{preview.target_company_name}» в него не входит.
                    Перенос проценты не меняет, а стоят они в каскаде выше расчёта по
                    часам — зарплата продолжит уходить на прежние юрлица, пока их не
                    поправят вручную.
                  </p>
                )}
                {preview.department_shares_stale && (
                  <p className="mt-1">
                    У самого отдела задан дефолт распределения без «
                    {preview.target_company_name}» — его тоже стоит пересмотреть.
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
