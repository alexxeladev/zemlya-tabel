import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { timesheetApi } from '../api/timesheet'
import { toast } from '../store/toasts'
import type { PeriodTask, TasksResponse } from '../types/api'

const MONTH_NAMES_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
}

function periodLabel(t: PeriodTask): string {
  return `${MONTH_NAMES_RU[t.month - 1]} ${t.year}`
}

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  draft: { label: 'Черновик', cls: 'bg-gray-100 text-gray-700' },
  pending_review: { label: 'На проверке', cls: 'bg-yellow-100 text-yellow-800' },
  closed: { label: 'Закрыт', cls: 'bg-green-100 text-green-800' },
}

export function TasksPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<TasksResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [showClosed, setShowClosed] = useState(true)

  useEffect(() => {
    timesheetApi
      .getTasks()
      .then(setData)
      .catch((err: any) => toast.error('Не удалось загрузить задачи: ' + (err?.message ?? err)))
      .finally(() => setLoading(false))
  }, [])

  const openTimesheet = (t: PeriodTask) => {
    const params = new URLSearchParams({ year: String(t.year), month: String(t.month) })
    if (t.department_id !== null) params.set('department_id', String(t.department_id))
    navigate(`/timesheet?${params.toString()}`)
  }

  if (loading) {
    return <div className="p-8 text-gray-500">Загрузка…</div>
  }

  const pending = data?.pending_review ?? []
  const closed = data?.recently_closed ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Задачи на проверку</h1>
        <p className="mt-1 text-sm text-gray-500">
          Табели, ожидающие вашего действия, и недавно закрытые периоды.
        </p>
      </div>

      {/* ── Ждут утверждения ── */}
      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Ждут утверждения {pending.length > 0 && `(${pending.length})`}
        </h2>
        {pending.length === 0 ? (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500">
            Все табели в работе или нет задач.
          </div>
        ) : (
          <div className="space-y-2">
            {pending.map((t) => (
              <div
                key={t.period_id}
                className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-yellow-200 bg-yellow-50/40 p-4"
              >
                <span className="min-w-[180px] font-semibold text-gray-900">{t.department_name}</span>
                <span className="w-28 text-sm text-gray-700">{periodLabel(t)}</span>
                <span
                  className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[t.status]?.cls ?? ''}`}
                >
                  {STATUS_BADGE[t.status]?.label ?? t.status}
                </span>
                <span className="text-sm text-gray-500">
                  Отправил {t.submitted_by_name ?? '—'} · {fmtDateTime(t.submitted_at)}
                </span>
                <span className="font-mono text-sm font-semibold text-gray-700">{t.total_hours} ч</span>
                <button
                  onClick={() => openTimesheet(t)}
                  className="ml-auto rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
                >
                  Открыть табель
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Недавно закрытые ── */}
      <section>
        <button
          onClick={() => setShowClosed((v) => !v)}
          className="mb-3 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-600"
        >
          <span>{showClosed ? '▾' : '▸'}</span>
          Недавно закрытые {closed.length > 0 && `(${closed.length})`}
        </button>
        {showClosed && (
          closed.length === 0 ? (
            <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500">
              Закрытых периодов пока нет.
            </div>
          ) : (
            <div className="space-y-1.5">
              {closed.map((t) => (
                <div
                  key={t.period_id}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-gray-200 bg-white px-4 py-2.5"
                >
                  <span className="min-w-[180px] font-medium text-gray-800">{t.department_name}</span>
                  <span className="w-28 text-sm text-gray-600">{periodLabel(t)}</span>
                  <span className="text-sm text-gray-500">
                    Закрыл {t.closed_by_name ?? '—'} · {fmtDateTime(t.closed_at)}
                  </span>
                  <span className="font-mono text-sm text-gray-600">{t.total_hours} ч</span>
                  <button
                    onClick={() => openTimesheet(t)}
                    className="ml-auto rounded border border-gray-300 px-3 py-1 text-sm text-gray-600 hover:bg-gray-50"
                  >
                    Открыть
                  </button>
                </div>
              ))}
            </div>
          )
        )}
      </section>
    </div>
  )
}

export default TasksPage
