import type { ReactNode } from 'react'

interface Props {
  isOpen: boolean
  onClose: () => void
  title: string
  children: ReactNode
  actions?: ReactNode
  /** Ширина окна. По умолчанию как была — существующие модалки не меняются. */
  size?: 'lg' | 'xl' | '3xl' | '5xl'
}

const WIDTHS: Record<NonNullable<Props['size']>, string> = {
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '3xl': 'max-w-3xl',
  '5xl': 'max-w-5xl',
}

export function Modal({ isOpen, onClose, title, children, actions, size = 'lg' }: Props) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className={`relative w-full ${WIDTHS[size]} rounded-xl bg-white shadow-xl mx-4`}>
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            ✕
          </button>
        </div>
        <div className="px-6 py-4">{children}</div>
        {actions && (
          <div className="flex justify-end gap-2 border-t border-gray-200 px-6 py-4">{actions}</div>
        )}
      </div>
    </div>
  )
}
