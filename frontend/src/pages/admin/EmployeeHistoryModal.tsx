import { useEffect, useState } from 'react'
import { fetchAudit, type ReferenceChange } from '../../api/audit'
import { AuditTable } from '../../components/AuditTable'
import { Button } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { toast } from '../../store/toasts'

/**
 * История изменений по одному сотруднику (task_audit_log).
 *
 * Показывает записи и по самому человеку, и по ЕГО РАБОЧИМ МЕСТАМ: оклад,
 * график, отдел и юрлицо живут на позиции, и история без них отвечала бы на
 * вопрос «кто поменял оклад» пустым экраном. Сужение делает бэк по
 * `employee_id` — у записи о позиции он проставлен от её владельца.
 *
 * Постранично, как и общий журнал: у сотрудника с многолетней историей записей
 * тоже накапливаются сотни.
 */

const PAGE_SIZE = 25

export function EmployeeHistoryModal({
  employeeId,
  employeeName,
  isOpen,
  onClose,
}: {
  employeeId: number | null
  employeeName: string
  isOpen: boolean
  onClose: () => void
}) {
  const [rows, setRows] = useState<ReferenceChange[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)

  // Новый сотрудник — начинаем с первой страницы, иначе откроется пустой
  // «хвост» от предыдущего.
  useEffect(() => { setOffset(0) }, [employeeId])

  useEffect(() => {
    if (!isOpen || employeeId == null) return
    let cancelled = false
    setLoading(true)
    fetchAudit({ employee_id: employeeId, limit: PAGE_SIZE, offset })
      .then((page) => {
        if (cancelled) return
        setRows(page.items)
        setTotal(page.total)
      })
      .catch(() => { if (!cancelled) toast.error('Не удалось загрузить историю') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [isOpen, employeeId, offset])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`История изменений: ${employeeName}`}
      size="5xl"
      actions={<Button type="button" onClick={onClose}>Закрыть</Button>}
    >
      <p className="mb-3 text-sm text-gray-500">
        Изменения карточки и рабочих мест сотрудника. Часы, отсутствия и ночные
        смены здесь не показываются — это операционные данные.
      </p>

      {/* Своя прокрутка: список длинный, а модалка не должна тянуть страницу. */}
      <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-gray-200">
        <AuditTable
          rows={rows}
          loading={loading}
          emptyText="По этому сотруднику изменений ещё не было"
        />
      </div>

      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
          <span>Всего: <b>{total}</b> · страница {page} из {pages}</span>
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
      )}
    </Modal>
  )
}
