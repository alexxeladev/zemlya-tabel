import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type { DayInfo, MonthSummary, ProductionCalendar } from '../../types/api'
import { getCalendar, getMonthSummary, importCalendar, loadCalendar } from '../../api/calendar'
import { ApiError } from '../../api/client'
import { Modal } from '../../components/Modal'

const MONTH_NAMES = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]
const WEEKDAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

function formatRelativeDate(isoString: string): string {
  const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000)
  if (diff < 60) return 'только что'
  if (diff < 3600) return `${Math.floor(diff / 60)} мин. назад`
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч. назад`
  const days = Math.floor(diff / 86400)
  if (days === 1) return 'вчера'
  return `${days} дн. назад`
}

interface MonthCardProps {
  summary: MonthSummary
  monthIndex: number
}

function MonthCard({ summary, monthIndex }: MonthCardProps) {
  const firstDay = summary.days[0]?.weekday ?? 0

  const cells: Array<DayInfo | null> = [
    ...Array(firstDay).fill(null),
    ...summary.days,
  ]

  while (cells.length % 7 !== 0) cells.push(null)

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-gray-800">{MONTH_NAMES[monthIndex]}</h3>
        <span className="text-xs text-gray-400">{summary.workdays} раб.</span>
      </div>

      <div className="grid grid-cols-7 gap-px text-center">
        {WEEKDAY_LABELS.map((d) => (
          <div key={d} className="py-0.5 text-[10px] font-medium text-gray-400">{d}</div>
        ))}
        {cells.map((cell, i) => {
          if (!cell) return <div key={i} />
          const bg =
            cell.type === 'holiday'
              ? 'bg-red-100 text-red-700'
              : cell.type === 'short'
                ? 'bg-yellow-100 text-yellow-800'
                : 'text-gray-700'
          const title =
            cell.type === 'holiday'
              ? 'Праздник / выходной'
              : cell.type === 'short'
                ? 'Сокращённый день (−1 час)'
                : 'Рабочий день'
          return (
            <div
              key={i}
              title={title}
              className={`rounded py-0.5 text-xs ${bg}`}
            >
              {cell.day}
            </div>
          )
        })}
      </div>

      <div className="mt-2 text-right text-[11px] text-gray-400">
        {summary.norm_hours_8h} ч / 8ч-смена
      </div>
    </div>
  )
}

interface ImportModalProps {
  year: number
  onClose: () => void
  onSuccess: () => void
}

function ImportModal({ year, onClose, onSuccess }: ImportModalProps) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async () => {
    setError(null)
    let parsed: { year?: number; months?: unknown[] }
    try {
      parsed = JSON.parse(text)
    } catch {
      setError('Невалидный JSON')
      return
    }
    if (!parsed.year || !Array.isArray(parsed.months)) {
      setError('Ожидается объект с полями year и months[]')
      return
    }
    setLoading(true)
    try {
      await importCalendar({ year: parsed.year, months: parsed.months as never })
      toast.success(`Календарь ${parsed.year} импортирован`)
      onSuccess()
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'Ошибка импорта'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal isOpen title={`Импорт календаря ${year}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-gray-600">
          Вставьте содержимое файла <code>calendar.json</code> от xmlcalendar.ru.{' '}
          <a
            href={`https://xmlcalendar.ru/data/ru/${year}/calendar.json`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 underline"
          >
            Скачать с xmlcalendar.ru →
          </a>
        </p>
        <textarea
          className="w-full rounded-lg border border-gray-300 p-3 font-mono text-xs focus:border-blue-500 focus:outline-none"
          rows={12}
          placeholder='{"year": 2026, "months": [...]}'
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
          >
            Отмена
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !text.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
          >
            {loading ? 'Импорт...' : 'Импортировать'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

export function CalendarPage() {
  const user = useAuthStore((s) => s.user)
  const isAdmin = user?.role === 'admin'

  const [year, setYear] = useState(new Date().getFullYear())
  const [calendar, setCalendar] = useState<ProductionCalendar | null>(null)
  const [summaries, setSummaries] = useState<MonthSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const fetchCalendar = async (y: number) => {
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    setLoading(true)
    setNotFound(false)
    setCalendar(null)
    setSummaries([])
    try {
      const cal = await getCalendar(y)
      setCalendar(cal)
      const results = await Promise.all(
        Array.from({ length: 12 }, (_, i) => getMonthSummary(y, i + 1))
      )
      setSummaries(results)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true)
      } else if (e instanceof ApiError) {
        toast.error(e.message)
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchCalendar(year)
  }, [year])

  const handleReload = async () => {
    setReloading(true)
    try {
      await loadCalendar(year)
      toast.success(`Календарь ${year} обновлён`)
      fetchCalendar(year)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'Ошибка обновления'
      toast.error(msg)
    } finally {
      setReloading(false)
    }
  }

  const workdays = calendar?.workdays_total ?? 0
  const shortDays = calendar?.short_days_total ?? 0
  const daysInYear = (year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)) ? 366 : 365
  const holidays = daysInYear - workdays
  const normHours = workdays * 8 - shortDays

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">
          Производственный календарь {year}
        </h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setYear((y) => y - 1)}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
          >
            ←
          </button>
          <span className="w-12 text-center font-semibold text-gray-800">{year}</span>
          <button
            onClick={() => setYear((y) => y + 1)}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
          >
            →
          </button>
        </div>
      </div>

      {/* Status card */}
      {calendar && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-sm">
                {calendar.source === 'remote' ? '🌐 xmlcalendar.ru' : '📝 Загружен вручную'}
              </span>
              <span className="text-sm text-gray-500">
                обновлён {formatRelativeDate(calendar.loaded_at)}
              </span>
            </div>
            {isAdmin && (
              <div className="ml-auto flex gap-2">
                <button
                  onClick={handleReload}
                  disabled={reloading}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 cursor-pointer"
                >
                  {reloading ? 'Загрузка...' : '🔄 Обновить с xmlcalendar.ru'}
                </button>
                <button
                  onClick={() => setShowImport(true)}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 cursor-pointer"
                >
                  📥 Импорт JSON
                </button>
                <a
                  href={`https://xmlcalendar.ru/data/ru/${year}/calendar.json`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-lg border border-blue-300 px-3 py-1.5 text-sm text-blue-600 hover:bg-blue-50"
                >
                  xmlcalendar.ru ↗
                </a>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Not found banner */}
      {notFound && !loading && (
        <div className="rounded-xl border border-yellow-300 bg-yellow-50 p-6">
          <h2 className="text-lg font-semibold text-yellow-800">Календарь {year} не загружен</h2>
          <p className="mt-1 text-sm text-yellow-700">
            Возможные причины: нет связи с xmlcalendar.ru, или год ещё не опубликован.
          </p>
          {isAdmin && (
            <div className="mt-4 flex gap-3">
              <button
                onClick={() => fetchCalendar(year)}
                className="rounded-lg bg-yellow-600 px-4 py-2 text-sm font-medium text-white hover:bg-yellow-700 cursor-pointer"
              >
                Попробовать загрузить
              </button>
              <button
                onClick={() => setShowImport(true)}
                className="rounded-lg border border-yellow-400 px-4 py-2 text-sm text-yellow-800 hover:bg-yellow-100 cursor-pointer"
              >
                Импорт JSON
              </button>
            </div>
          )}
        </div>
      )}

      {loading && (
        <div className="py-16 text-center text-sm text-gray-400">Загрузка календаря...</div>
      )}

      {/* Metrics */}
      {calendar && !loading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {[
            { label: 'Рабочих дней', value: workdays, color: 'text-green-700 bg-green-50' },
            { label: 'Сокращённых дней', value: shortDays, color: 'text-yellow-700 bg-yellow-50' },
            { label: 'Выходных / праздников', value: holidays, color: 'text-red-700 bg-red-50' },
            { label: 'Норма часов (8ч/смена)', value: normHours, color: 'text-blue-700 bg-blue-50' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <p className="text-xs text-gray-500">{label}</p>
              <p className={`mt-1 text-3xl font-bold ${color.split(' ')[0]}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Monthly grid */}
      {summaries.length === 12 && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {summaries.map((s) => (
              <MonthCard key={s.month} summary={s} monthIndex={s.month - 1} />
            ))}
          </div>

          {/* Legend */}
          <div className="flex flex-wrap gap-4 text-sm text-gray-600">
            <div className="flex items-center gap-2">
              <span className="inline-block h-4 w-4 rounded bg-white border border-gray-300" />
              Рабочий день
            </div>
            <div className="flex items-center gap-2">
              <span className="inline-block h-4 w-4 rounded bg-yellow-100 border border-yellow-300" />
              Сокращённый день (−1 час)
            </div>
            <div className="flex items-center gap-2">
              <span className="inline-block h-4 w-4 rounded bg-red-100 border border-red-300" />
              Праздник / выходной
            </div>
          </div>
        </>
      )}

      {showImport && (
        <ImportModal
          year={year}
          onClose={() => setShowImport(false)}
          onSuccess={() => {
            setShowImport(false)
            fetchCalendar(year)
          }}
        />
      )}
    </div>
  )
}
