import type { ReferenceChange } from '../api/audit'

/**
 * Таблица записей журнала изменений — ОДНА на оба места, где журнал виден:
 * экран «Журнал изменений» и вкладка истории в карточке сотрудника. Две копии
 * разошлись бы в оформлении «было → стало», а именно его и читают.
 */

const ACTION_LABELS: Record<string, string> = {
  create: 'создано',
  update: 'изменено',
  delete: 'удалено',
}

const ACTION_STYLES: Record<string, string> = {
  create: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  update: 'bg-blue-50 text-blue-700 border-blue-200',
  delete: 'bg-rose-50 text-rose-700 border-rose-200',
}

/** Массовые операции и импорт подсвечены: «уехало само» ≠ «человек поправил». */
const SOURCE_STYLES: Record<string, string> = {
  ui: 'bg-gray-100 text-gray-600 border-gray-200',
  import: 'bg-violet-50 text-violet-700 border-violet-200',
  bulk: 'bg-amber-50 text-amber-800 border-amber-200',
  cli: 'bg-gray-100 text-gray-500 border-gray-200',
  system: 'bg-gray-100 text-gray-500 border-gray-200',
}

export function formatMoment(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

/** Пустое значение показываем прочерком, иначе «было → стало» читается как обрыв. */
function Value({ text, tone }: { text: string | null; tone: 'old' | 'new' }) {
  if (text === null || text === '') {
    return <span className="text-gray-400 italic">пусто</span>
  }
  return (
    <span className={tone === 'old' ? 'text-gray-500 line-through decoration-gray-300' : 'font-medium text-gray-900'}>
      {text}
    </span>
  )
}

export function AuditTable({
  rows,
  loading,
  emptyText = 'Изменений нет',
  onPickOperation,
  showEntity = true,
}: {
  rows: ReferenceChange[]
  loading?: boolean
  emptyText?: string
  /** клик по метке массовой операции — показать всю операцию целиком */
  onPickOperation?: (operationId: string) => void
  /** в карточке сотрудника колонка «Объект» лишняя — это и так он */
  showEntity?: boolean
}) {
  if (loading) return <div className="p-6 text-sm text-gray-500">Загрузка…</div>
  if (rows.length === 0) return <div className="p-6 text-sm text-gray-500">{emptyText}</div>

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <th className="whitespace-nowrap px-3 py-2 font-medium">Когда</th>
            <th className="whitespace-nowrap px-3 py-2 font-medium">Кто</th>
            {showEntity && <th className="px-3 py-2 font-medium">Объект</th>}
            <th className="px-3 py-2 font-medium">Поле</th>
            <th className="px-3 py-2 font-medium">Было → стало</th>
            <th className="whitespace-nowrap px-3 py-2 font-medium">Источник</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-gray-100 align-top hover:bg-gray-50">
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">
                {formatMoment(r.created_at)}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-700">
                {r.actor_name ?? <span className="text-gray-400 italic">система</span>}
              </td>
              {showEntity && (
                <td className="px-3 py-2">
                  <div className="text-gray-900">{r.entity_label ?? `#${r.entity_id ?? '—'}`}</div>
                  <div className="text-xs text-gray-500">{r.entity_type_label}</div>
                </td>
              )}
              <td className="px-3 py-2">
                {r.field ? (
                  <span className="text-gray-800">{r.field_label ?? r.field}</span>
                ) : (
                  <span
                    className={`inline-block rounded border px-1.5 py-0.5 text-xs ${ACTION_STYLES[r.action] ?? ''}`}
                  >
                    {ACTION_LABELS[r.action] ?? r.action}
                  </span>
                )}
              </td>
              <td className="px-3 py-2">
                {r.field ? (
                  <span className="inline-flex flex-wrap items-center gap-1.5">
                    <Value text={r.old_value} tone="old" />
                    <span className="text-gray-400">→</span>
                    <Value text={r.new_value} tone="new" />
                  </span>
                ) : (
                  <span className="text-gray-500">
                    {r.action === 'create' ? 'запись заведена' : 'запись удалена'}
                  </span>
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-2">
                <span
                  className={`inline-block rounded border px-1.5 py-0.5 text-xs ${SOURCE_STYLES[r.source] ?? SOURCE_STYLES.ui}`}
                >
                  {r.source_label}
                </span>
                {/* Массовая операция открывается целиком: одиночная строка
                    переноса отдела без остальных ни о чём не говорит. */}
                {r.operation_id && r.source !== 'ui' && onPickOperation && (
                  <button
                    type="button"
                    className="ml-1.5 text-xs text-blue-600 underline decoration-dotted hover:text-blue-800"
                    onClick={() => onPickOperation(r.operation_id!)}
                    title="Показать все изменения этой операции"
                  >
                    вся операция
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
