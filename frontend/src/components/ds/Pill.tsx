import type { ReactNode } from 'react'

/** Статусная пилюля (`.pill` макетов). Цвет — только для состояния. */
export type PillTone = 'neutral' | 'warn' | 'ok' | 'danger' | 'accent'

const TONE: Record<PillTone, string> = {
  neutral: 'border-ds-line bg-ds-surface-3 text-ds-muted',
  warn: 'border-ds-warn-line bg-ds-warn-soft text-ds-warn',
  ok: 'border-ds-ok-line bg-ds-ok-soft text-ds-ok',
  danger: 'border-ds-danger-line bg-ds-danger-soft text-ds-danger',
  accent: 'border-ds-accent/30 bg-ds-accent-soft text-ds-accent-hi',
}

export function Pill({
  tone = 'neutral',
  dot = false,
  children,
  className = '',
}: {
  tone?: PillTone
  dot?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11.5px] font-medium ${TONE[tone]} ${className}`}
    >
      {dot && <i className="h-1.5 w-1.5 flex-none rounded-full bg-current" aria-hidden="true" />}
      {children}
    </span>
  )
}
