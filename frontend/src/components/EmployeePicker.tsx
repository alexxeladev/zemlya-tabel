import { useEffect, useMemo, useRef, useState } from 'react'
import type { Employee } from '../types/api'

/**
 * Выбор сотрудника ПОИСКОМ по имени или табельному номеру.
 *
 * Обычная выпадашка тут не годится: сотрудников две сотни, и листать их
 * колесом, чтобы отфильтровать журнал, — мучение. Внутренний id пользователю
 * тем более не известен, поэтому «введите id» тоже отпадает.
 *
 * Список приходит уже загруженным (экран всё равно грузит его один раз), отбор
 * идёт на клиенте: сетевой запрос на каждую букву здесь не нужен.
 */

const MAX_SUGGESTIONS = 12

function label(e: Employee): string {
  return e.tab_number ? `${e.full_name} (${e.tab_number})` : e.full_name
}

export function EmployeePicker({
  people,
  value,
  onChange,
  placeholder = 'Начните вводить ФИО…',
}: {
  people: Employee[]
  /** id выбранного сотрудника; пусто — фильтра нет */
  value: number | null
  onChange: (id: number | null) => void
  placeholder?: string
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  const selected = useMemo(
    () => (value == null ? null : people.find((p) => p.id === value) ?? null),
    [people, value],
  )

  // Выбор пришёл извне (сброс фильтров) — поле показывает его же.
  useEffect(() => {
    setQuery(selected ? label(selected) : '')
  }, [selected])

  // Клик мимо закрывает подсказки и возвращает поле к выбранному значению:
  // иначе в поле остаётся набранный, но не выбранный текст, и кажется, что
  // фильтр применён, хотя он нет.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current?.contains(e.target as Node)) return
      setOpen(false)
      setQuery(selected ? label(selected) : '')
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open, selected])

  const needle = query.trim().toLocaleLowerCase('ru')
  const matches = useMemo(() => {
    // Пустой ввод — показываем начало списка, чтобы поле не выглядело мёртвым.
    const source = needle
      ? people.filter((p) => label(p).toLocaleLowerCase('ru').includes(needle))
      : people
    return source
      .slice()
      .sort((a, b) => a.full_name.localeCompare(b.full_name, 'ru'))
      .slice(0, MAX_SUGGESTIONS)
  }, [people, needle])

  const pick = (e: Employee | null) => {
    onChange(e ? e.id : null)
    setQuery(e ? label(e) : '')
    setOpen(false)
  }

  return (
    <div className="relative flex flex-col gap-1" ref={boxRef}>
      <label className="text-sm font-medium text-gray-700">Сотрудник</label>
      <div className="relative">
        <input
          className="w-60 rounded-lg border border-gray-300 px-3 py-2 pr-7 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={query}
          placeholder={placeholder}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') { setOpen(false); return }
            // Enter выбирает единственное совпадение — самый частый случай,
            // когда фамилию дописали целиком.
            if (e.key === 'Enter' && matches.length === 1) {
              e.preventDefault()
              pick(matches[0])
            }
          }}
        />
        {(value != null || query) && (
          <button
            type="button"
            onClick={() => pick(null)}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700"
            title="Сбросить выбор сотрудника"
          >
            ✕
          </button>
        )}
      </div>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 max-h-64 w-72 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
          {matches.length === 0 ? (
            <div className="px-3 py-2 text-sm text-gray-400">Никого не найдено</div>
          ) : (
            <>
              {matches.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => pick(p)}
                  className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-blue-50 ${
                    p.id === value ? 'bg-blue-50 font-medium' : ''
                  }`}
                >
                  {label(p)}
                  {!p.is_active && <span className="ml-1 text-xs text-gray-400">уволен</span>}
                </button>
              ))}
              {/* Честно говорим, что список подрезан, иначе «моего нет» */}
              {people.filter((p) => !needle || label(p).toLocaleLowerCase('ru').includes(needle)).length > MAX_SUGGESTIONS && (
                <div className="border-t border-gray-100 px-3 py-1.5 text-xs text-gray-400">
                  показаны первые {MAX_SUGGESTIONS} — уточните запрос
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
