import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

import { Icon } from './Icon'

/**
 * Меню действий «⋯» у строки или в шапке панели.
 *
 * Правила редизайна (§4.3): действия видны всегда, а не по наведению; мишень не
 * меньше 24 px; разрушающее действие не может быть самым заметным элементом
 * списка — поэтому оно живёт в меню, а не отдельной красной кнопкой в каждой
 * строке.
 *
 * Меню рисуется в портале с `position: fixed`: строки лежат в контейнере с
 * `overflow: auto`, внутри него выпадашку обрезало бы.
 */
export interface MenuItem {
  label: string
  onSelect: () => void
  /** Разрушающее действие — красным, внизу списка. */
  danger?: boolean
  disabled?: boolean
  /** Почему недоступно или что именно произойдёт — вторая строка пункта. */
  hint?: string
}

const MENU_W = 232

export function RowMenu({
  items,
  label,
  className = '',
}: {
  items: MenuItem[]
  /** Подпись кнопки: «Действия со строкой Иванов И.» — диктору нужен контекст. */
  label: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const menuId = useId()

  const close = useCallback((restoreFocus: boolean) => {
    setOpen(false)
    if (restoreFocus) button.current?.focus()
  }, [])

  useLayoutEffect(() => {
    if (!open || !button.current) return
    const r = button.current.getBoundingClientRect()
    const left = Math.max(8, Math.min(r.right - MENU_W, window.innerWidth - MENU_W - 8))
    const below = r.bottom + 4
    // Внизу экрана открываемся вверх, иначе меню уйдёт за край.
    const height = menu.current?.offsetHeight ?? 0
    const top = below + height > window.innerHeight - 8 ? Math.max(8, r.top - height - 4) : below
    setPos({ top, left })
  }, [open])

  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLButtonElement>('[role=menuitem]:not([disabled])')?.focus()
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!menu.current?.contains(t) && !button.current?.contains(t)) close(false)
    }
    // Прокрутка таблицы сдвигает строку — меню, прибитое к экрану, оторвалось бы от неё.
    const onScroll = () => close(false)
    document.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
  }, [open, close])

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const entries = Array.from(
      menu.current?.querySelectorAll<HTMLButtonElement>('[role=menuitem]:not([disabled])') ?? [],
    )
    const i = entries.indexOf(document.activeElement as HTMLButtonElement)
    if (e.key === 'Escape') {
      e.preventDefault()
      close(true)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      entries[(i + 1) % entries.length]?.focus()
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      entries[(i - 1 + entries.length) % entries.length]?.focus()
    } else if (e.key === 'Tab') {
      close(false)
    }
  }

  if (items.length === 0) return null

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
        className={`inline-grid h-7 w-7 flex-none cursor-pointer place-items-center rounded-ds-sm text-ds-muted transition-colors hover:bg-ds-surface-3 hover:text-ds-ink aria-expanded:bg-ds-surface-3 aria-expanded:text-ds-ink ${className}`}
      >
        <Icon name="more" />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            id={menuId}
            role="menu"
            aria-label={label}
            onKeyDown={onMenuKey}
            style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width: MENU_W }}
            // `ds` на портале: он вне корня экрана и иначе потерял бы шрифт и фокус.
            className="ds fixed z-[100] rounded-ds-md border border-ds-line-strong bg-ds-surface p-1 shadow-ds-pop"
          >
            {items.map((item, i) => (
              <div key={item.label}>
                {item.danger && i > 0 && !items[i - 1].danger && (
                  <div className="my-1 h-px bg-ds-line" role="separator" />
                )}
                <button
                  type="button"
                  role="menuitem"
                  disabled={item.disabled}
                  onClick={() => {
                    close(true)
                    item.onSelect()
                  }}
                  className={`flex min-h-8 w-full cursor-pointer flex-col items-start rounded-ds-sm px-2.5 py-1.5 text-left text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${
                    item.danger
                      ? 'text-ds-danger hover:bg-ds-danger-soft focus:bg-ds-danger-soft'
                      : 'text-ds-ink-2 hover:bg-ds-accent-soft hover:text-ds-accent-hi focus:bg-ds-accent-soft'
                  }`}
                >
                  {item.label}
                  {item.hint && (
                    <span className="text-[11.5px] font-normal text-ds-muted">{item.hint}</span>
                  )}
                </button>
              </div>
            ))}
          </div>,
          document.body,
        )}
    </>
  )
}
