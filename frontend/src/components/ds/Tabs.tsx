import { useRef, type KeyboardEvent, type ReactNode } from 'react'

/**
 * Вкладки (`.tabs` артборда 05): `role=tablist`, `aria-selected`, стрелки.
 *
 * Компонент управляемый: какая вкладка открыта, решает экран, — обычно по
 * адресу. Состояние вкладки в URL (ссылка открывает её сразу, перезагрузка её
 * сохраняет) — правило редизайна «состояние адресуемо», и хранить его здесь, в
 * useState, значило бы его нарушить.
 *
 * Вкладки, на которые у роли нет прав, экран в `items` не передаёт вовсе — не
 * показывает их пустыми и не открывает с ошибкой.
 */
export interface TabItem<K extends string> {
  id: K
  label: string
  /** Число рядом с подписью (например, сколько записей). */
  count?: number
}

export function Tabs<K extends string>({
  items,
  value,
  onChange,
  label,
  idPrefix = 'tab',
  className = '',
}: {
  items: TabItem<K>[]
  value: K
  onChange: (id: K) => void
  /** Подпись для экранного диктора: что переключают эти вкладки. */
  label: string
  idPrefix?: string
  className?: string
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  // Шаблон WAI-ARIA Tabs: в порядке Tab стоит только выбранная вкладка,
  // между вкладками ходят стрелками, Home/End — к краям.
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = items.length - 1
    const next =
      e.key === 'ArrowRight' ? (index === last ? 0 : index + 1)
      : e.key === 'ArrowLeft' ? (index === 0 ? last : index - 1)
      : e.key === 'Home' ? 0
      : e.key === 'End' ? last
      : null
    if (next === null) return
    e.preventDefault()
    refs.current[next]?.focus()
    onChange(items[next].id)
  }

  return (
    <div role="tablist" aria-label={label} className={`flex gap-0.5 border-b border-ds-line ${className}`}>
      {items.map((item, index) => {
        const selected = item.id === value
        return (
          <button
            key={item.id}
            ref={(el) => {
              refs.current[index] = el
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-${item.id}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${item.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.id)}
            onKeyDown={(e) => onKeyDown(e, index)}
            className={`-mb-px cursor-pointer border-b-2 px-3 py-2 text-[13px] transition-colors ${
              selected
                ? 'border-ds-accent font-medium text-ds-ink'
                : 'border-transparent text-ds-muted hover:text-ds-ink-2'
            }`}
          >
            {item.label}
            {item.count !== undefined && (
              <b className="ml-1.5 rounded-full bg-ds-surface-3 px-1.5 text-[10.5px] font-semibold text-ds-muted tabular-nums">
                {item.count}
              </b>
            )}
          </button>
        )
      })}
    </div>
  )
}

/** Панель содержимого вкладки — связывает её с кнопкой для диктора. */
export function TabPanel({
  id,
  idPrefix = 'tab',
  children,
  className = '',
}: {
  id: string
  idPrefix?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${id}`}
      aria-labelledby={`${idPrefix}-${id}`}
      className={className}
    >
      {children}
    </div>
  )
}
