import type { ReactNode } from 'react'

/**
 * Пустое состояние: что это, почему пусто и что сделать (ux-copy).
 *
 * «Пусто» и «не задано» — разные вещи (правила интерфейса CLAUDE.md): пустой
 * экран без объяснения читается как «данных нет» или поломка.
 */
export function EmptyState({
  title,
  children,
  action,
  compact = false,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div
      className={`rounded-ds-lg border border-dashed border-ds-line-strong text-center ${
        compact ? 'px-4 py-5' : 'px-6 py-10'
      }`}
    >
      <p className="m-0 text-[14px] font-medium text-ds-ink-2">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-[60ch] text-[13px] text-ds-muted">{children}</p>}
      {action && <div className="mt-3 flex justify-center gap-2">{action}</div>}
    </div>
  )
}
