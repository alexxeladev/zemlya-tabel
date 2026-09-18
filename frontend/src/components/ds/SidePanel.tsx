import { useEffect, useId, useRef, type ReactNode } from 'react'

import { DsIconButton } from './Button'

/**
 * Боковая панель деталей вместо модалки (артборд 05, §4.2 п.5).
 *
 * Список остаётся виден и доступен — поэтому панель НЕ модальная: фокус не
 * запирается, фон не гасится. Что открыто, экран держит в адресе
 * (`?position_id=`): ссылкой можно поделиться, перезагрузка панель не закрывает.
 *
 * Панель — часть раскладки экрана (правая колонка), а не слой поверх: экран
 * ставит её рядом со списком сам.
 */
export function SidePanel({
  title,
  subtitle,
  onClose,
  actions,
  tabs,
  footer,
  children,
  width = 520,
}: {
  title: ReactNode
  subtitle?: ReactNode
  onClose: () => void
  /** Кнопки в шапке слева от крестика — обычно меню «⋯». */
  actions?: ReactNode
  /** Вкладки под заголовком. */
  tabs?: ReactNode
  /** Нижняя полоса: одна кнопка сохранения и подпись, что изменено. */
  footer?: ReactNode
  children: ReactNode
  width?: number
}) {
  const titleId = useId()
  const root = useRef<HTMLElement>(null)
  const opener = useRef<Element | null>(null)

  // Фокус — в панель при открытии и обратно туда, откуда открыли, при закрытии:
  // иначе клавиатура теряет место в списке.
  useEffect(() => {
    opener.current = document.activeElement
    root.current?.focus()
    return () => {
      if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus()
    }
  }, [])

  return (
    <aside
      ref={root}
      tabIndex={-1}
      aria-labelledby={titleId}
      onKeyDown={(e) => {
        // Esc внутри открытой выпадашки или меню закрывает их, а не панель.
        if (e.key === 'Escape' && !e.defaultPrevented) {
          e.preventDefault()
          onClose()
        }
      }}
      style={{ width }}
      className="flex h-full min-h-0 flex-none flex-col border-l border-ds-line bg-ds-surface shadow-[-14px_0_34px_-28px_rgba(19,28,26,.5)] outline-none"
    >
      <div className="border-b border-ds-line px-5 pt-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="m-0 text-[18px] font-semibold tracking-tight text-ds-ink">
              {title}
            </h2>
            {subtitle && <p className="mt-0.5 text-[12.5px] text-ds-muted">{subtitle}</p>}
          </div>
          {actions}
          <DsIconButton icon="close" label="Закрыть панель" onClick={onClose} />
        </div>
        {tabs ? <div className="mt-3">{tabs}</div> : <div className="h-4" />}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-5 pb-6 pt-4">{children}</div>
      {footer && (
        <div className="flex flex-wrap items-center gap-2.5 border-t border-ds-line bg-ds-surface-2 px-5 py-3">
          {footer}
        </div>
      )}
    </aside>
  )
}
