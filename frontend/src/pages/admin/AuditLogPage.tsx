import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchAudit,
  fetchAuditFilters,
  type AuditFilters,
  type AuditQuery,
  type ReferenceChange,
} from '../../api/audit'
import { listEmployees } from '../../api/employees'
import { AuditTable } from '../../components/AuditTable'
import { Button } from '../../components/Button'
import { EmployeePicker } from '../../components/EmployeePicker'
import { PageHeader } from '../../components/PageHeader'
import { Select } from '../../components/Select'
import { toast } from '../../store/toasts'
import type { Employee } from '../../types/api'

/**
 * Журнал изменений справочных данных (task_audit_log), только admin.
 *
 * Экран ВСЕГДА постраничный и всегда ходит на сервер: журнал растёт быстро, и
 * «загрузить всё и фильтровать на клиенте» — как сделано в табеле — здесь
 * работало бы ровно до первого месяца эксплуатации. Все фильтры уходят в
 * запрос, каждый лежит на своём индексе.
 */

const PAGE_SIZE = 50

/** Пустая строка в `<select>` = «фильтра нет»: у Select нет отдельного «сброса». */
const ANY = ''

export function AuditLogPage() {
  const [filters, setFilters] = useState<AuditFilters | null>(null)
  // Сотрудников выбирают ПО ИМЕНИ: внутренний id никто наизусть не знает, а
  // поле «введите id» на экране журнала — это тупик для пользователя.
  const [people, setPeople] = useState<Employee[]>([])
  const [rows, setRows] = useState<ReferenceChange[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)

  const [entityType, setEntityType] = useState(ANY)
  const [source, setSource] = useState(ANY)
  const [actorId, setActorId] = useState(ANY)
  const [employeeId, setEmployeeId] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  /** Выбранная массовая операция: экран сужается до неё одной. */
  const [operationId, setOperationId] = useState<string | null>(null)

  const query = useMemo<AuditQuery>(() => {
    // Внутри операции остальные фильтры не применяем: «покажи всё, что сделал
    // этот перенос» — это ответ целиком, а не его пересечение с чем-то ещё.
    if (operationId) return { operation_id: operationId, limit: PAGE_SIZE, offset }
    const q: AuditQuery = { limit: PAGE_SIZE, offset }
    if (entityType) q.entity_type = entityType
    if (source) q.source = source
    if (actorId) q.actor_id = Number(actorId)
    if (employeeId.trim()) q.employee_id = Number(employeeId.trim())
    if (dateFrom) q.date_from = dateFrom
    if (dateTo) q.date_to = dateTo
    return q
  }, [operationId, offset, entityType, source, actorId, employeeId, dateFrom, dateTo])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await fetchAudit(query)
      setRows(page.items)
      setTotal(page.total)
    } catch {
      toast.error('Не удалось загрузить журнал изменений')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    fetchAuditFilters().then(setFilters).catch(() => {
      // Справочник фильтров не критичен: лента работает и без выпадашек.
    })
    // Список для выбора сотрудника — включая уволенных: их карточки правили
    // тоже, и искать эти правки надо уметь.
    listEmployees().then(setPeople).catch(() => {})
  }, [])

  /** Любая смена фильтра возвращает на первую страницу: иначе offset от
   *  прошлой выдачи покажет пустой экран и это выглядит как поломка. */
  const withReset = <T,>(setter: (v: T) => void) => (v: T) => {
    setOffset(0)
    setter(v)
  }

  const resetAll = () => {
    setOperationId(null)
    setEntityType(ANY)
    setSource(ANY)
    setActorId(ANY)
    setEmployeeId('')
    setDateFrom('')
    setDateTo('')
    setOffset(0)
  }

  const filtersActive =
    Boolean(operationId || entityType || source || actorId || employeeId || dateFrom || dateTo)
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="p-6">
      <PageHeader
        title="Журнал изменений"
        description="Кто, когда и что менял в справочниках: сотрудники, рабочие места, отделы, юрлица, графики, ответственные. Часы и отсутствия сюда не попадают."
      />

      {operationId ? (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm">
          <span className="font-medium text-amber-900">
            Показана одна массовая операция целиком
          </span>
          <code className="rounded bg-white px-1.5 py-0.5 text-xs text-amber-800">
            {operationId}
          </code>
          <Button size="sm" variant="secondary" onClick={resetAll}>
            Вернуться ко всему журналу
          </Button>
        </div>
      ) : (
        <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-4">
          <Select
            label="Тип объекта"
            value={entityType}
            onChange={(e) => withReset(setEntityType)(e.target.value)}
            options={[
              { value: ANY, label: 'Все объекты' },
              ...(filters?.entity_types.map((o) => ({ value: o.value, label: o.label })) ?? []),
            ]}
          />
          <Select
            label="Источник"
            value={source}
            onChange={(e) => withReset(setSource)(e.target.value)}
            options={[
              { value: ANY, label: 'Любой источник' },
              ...(filters?.sources.map((o) => ({ value: o.value, label: o.label })) ?? []),
            ]}
          />
          <Select
            label="Кто менял"
            value={actorId}
            onChange={(e) => withReset(setActorId)(e.target.value)}
            options={[
              { value: ANY, label: 'Все пользователи' },
              ...(filters?.actors.map((o) => ({ value: o.value, label: o.label })) ?? []),
            ]}
          />
          {/* Поиском, а не выпадашкой: сотрудников две сотни, листать их колесом
              ради фильтра невозможно, а внутренний id пользователю не известен. */}
          <EmployeePicker
            people={people}
            value={employeeId ? Number(employeeId) : null}
            onChange={(id) => withReset(setEmployeeId)(id == null ? '' : String(id))}
          />
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">С даты</label>
            <input
              type="date"
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={dateFrom}
              onChange={(e) => withReset(setDateFrom)(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">По дату</label>
            <input
              type="date"
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={dateTo}
              onChange={(e) => withReset(setDateTo)(e.target.value)}
            />
          </div>
          {filtersActive && (
            <Button variant="secondary" onClick={resetAll}>Сброс</Button>
          )}
        </div>
      )}

      <div className="rounded-lg border border-gray-200 bg-white">
        <AuditTable
          rows={rows}
          loading={loading}
          emptyText={filtersActive ? 'По этим фильтрам записей нет' : 'Журнал пока пуст'}
          // Внутри операции ссылка «вся операция» на каждой строке бессмысленна —
          // мы уже в ней.
          onPickOperation={
            operationId ? undefined : (id) => { setOffset(0); setOperationId(id) }
          }
        />
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
        <span>
          Всего записей: <b>{total}</b>
          {total > 0 && <> · страница {page} из {pages}</>}
        </span>
        <span className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            ← Назад
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={offset + PAGE_SIZE >= total || loading}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Вперёд →
          </Button>
        </span>
      </div>
    </div>
  )
}
