import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { DsButton } from './Button'

/**
 * Подтверждение действия вместо `window.confirm`.
 *
 * Системное окно не даёт подписать кнопки — пользователь видит «ОК / Отмена» и
 * должен помнить, что из них что. Здесь кнопка называет действие: «Снять с
 * поста» / «Оставить» (ux-copy: «Delete files / Keep files», не «OK / Cancel»).
 *
 * Модальное по-настоящему: `role=dialog` + `aria-modal`, фокус заперт внутри,
 * Esc — отказ, фокус возвращается туда, откуда открыли. Старый
 * `components/Modal` этого не умеет (аудит project-audit.md §4) и остаётся для
 * экранов, ещё не переведённых на редизайн.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel = 'Отмена',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string
  /** Последствия: что именно изменится и что останется. */
  children: ReactNode
  confirmLabel: string
  cancelLabel?: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const titleId = useId()
  const bodyId = useId()
  const dialog = useRef<HTMLDivElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const opener = document.activeElement
    // Фокус — на безопасный вариант: Enter по привычке не должен удалять.
    cancel.current?.focus()
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus()
    }
  }, [])

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      if (!busy) onCancel()
      return
    }
    if (e.key !== 'Tab') return
    const focusable = Array.from(
      dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, select, textarea') ?? [],
    )
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div className="ds fixed inset-0 z-[110] flex items-start justify-center bg-ds-ink/35 px-4 pt-[14vh]">
      <div
        ref={dialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onKeyDown={onKeyDown}
        className="w-full max-w-[440px] rounded-ds-lg border border-ds-line-strong bg-ds-surface shadow-ds-pop"
      >
        <div className="px-5 pb-4 pt-5">
          <h2 id={titleId} className="m-0 text-[16px] font-semibold tracking-tight">
            {title}
          </h2>
          <div id={bodyId} className="mt-2 text-[13.5px] leading-relaxed text-ds-ink-2">
            {children}
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-ds-line bg-ds-surface-2 px-5 py-3">
          <DsButton ref={cancel} variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </DsButton>
          <DsButton variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </DsButton>
        </div>
      </div>
    </div>,
    document.body,
  )
}
