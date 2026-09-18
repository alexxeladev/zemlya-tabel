import type { ReactNode } from 'react'

import { Pill, type PillTone } from './Pill'

/**
 * Заголовок экрана, привязанного к периоду (артборд 02: «Период — это
 * заголовок, а не бейдж»).
 *
 * Статус периода, кто и когда его сдвинул и что из-за этого нельзя — крупно
 * рядом с названием, а не мелкой плашкой между фильтрами. Действия над периодом
 * — справа в той же строке.
 */
export type PeriodState = 'draft' | 'pending_review' | 'closed'

const STATE: Record<PeriodState, { label: string; tone: PillTone }> = {
  draft: { label: 'Черновик', tone: 'neutral' },
  pending_review: { label: 'На проверке', tone: 'warn' },
  closed: { label: 'Закрыт', tone: 'ok' },
}

export function PeriodHeading({
  title,
  period,
  state,
  note,
  actions,
}: {
  title: string
  /** Сам период словами: «Август 2026». */
  period: ReactNode
  state?: PeriodState | null
  /** Что следует из статуса: «правка смен заблокирована до возврата в черновик». */
  note?: ReactNode
  actions?: ReactNode
}) {
  const s = state ? STATE[state] : null
  return (
    <div className="flex flex-wrap items-start gap-x-5 gap-y-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="m-0 text-[23px] font-semibold tracking-[-0.025em] text-ds-ink">{title}</h1>
          <span className="text-[15px] text-ds-ink-2">{period}</span>
          {s && (
            <Pill tone={s.tone} dot>
              {s.label}
            </Pill>
          )}
        </div>
        {note && <p className="mt-1 max-w-[72ch] text-[13px] text-ds-muted">{note}</p>}
      </div>
      {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
