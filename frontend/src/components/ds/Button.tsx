import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'

import { Icon, type IconName } from './Icon'

/**
 * Кнопки дизайн-системы (`.btn` / `.iconbtn` макетов).
 *
 * Отдельно от старого `components/Button`: тот живёт на экранах, которые на
 * редизайн ещё не переведены, и перекрашивать их нельзя. Экран, переходящий на
 * редизайн, берёт эту кнопку, а старую не трогает.
 */

type Variant = 'primary' | 'soft' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

const VARIANT: Record<Variant, string> = {
  primary: 'bg-ds-accent text-ds-on-accent hover:bg-ds-accent-hi',
  soft: 'border-ds-line bg-ds-surface-3 text-ds-ink-2 hover:bg-ds-line hover:text-ds-ink',
  ghost: 'text-ds-muted hover:bg-ds-surface-3 hover:text-ds-ink',
  danger: 'bg-ds-danger text-white hover:opacity-90',
}

// Высота не ниже 28 px: мишень клика ≥ 24×24 (task_ui_redesign §4.3).
const SIZE: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-[12.5px]',
  md: 'h-8 px-3.5 text-[13px]',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  icon?: IconName
  loading?: boolean
  children: ReactNode
  ref?: Ref<HTMLButtonElement>
}

export function DsButton({
  variant = 'soft',
  size = 'md',
  icon,
  loading = false,
  disabled,
  className = '',
  children,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-ds-md border border-transparent font-medium transition-colors active:translate-y-px disabled:cursor-not-allowed disabled:opacity-55 ${VARIANT[variant]} ${SIZE[size]} ${className}`}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  )
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName
  /** Подпись обязательна: у кнопки-иконки нет текста, экранный диктор иначе молчит. */
  label: string
}

export function DsIconButton({ icon, label, className = '', type = 'button', ...props }: IconButtonProps) {
  return (
    <button
      {...props}
      type={type}
      aria-label={label}
      title={label}
      className={`inline-grid h-7 w-7 flex-none cursor-pointer place-items-center rounded-ds-sm text-ds-muted transition-colors hover:bg-ds-surface-3 hover:text-ds-ink disabled:cursor-not-allowed disabled:opacity-55 ${className}`}
    >
      <Icon name={icon} />
    </button>
  )
}
