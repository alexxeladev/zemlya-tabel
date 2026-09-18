/**
 * Иконки дизайн-системы — локальный набор SVG вместо эмодзи.
 *
 * Эмодзи (🌙, 📋) на части машин не отрисовываются вовсе — на их месте пустой
 * прямоугольник (task_ui_redesign §4.3). Набор нарочно маленький: новая иконка
 * добавляется сюда, а не рисуется по месту.
 */

const PATHS = {
  more: (
    <g fill="currentColor" stroke="none">
      <circle cx="3.5" cy="8" r="1.4" />
      <circle cx="8" cy="8" r="1.4" />
      <circle cx="12.5" cy="8" r="1.4" />
    </g>
  ),
  close: <path d="m4 4 8 8M12 4l-8 8" />,
  search: (
    <>
      <circle cx="7.2" cy="7.2" r="4.7" />
      <path d="m10.7 10.7 3 3" />
    </>
  ),
  chevronDown: <path d="m4 6 4 4 4-4" />,
  chevronLeft: <path d="m10 4-4 4 4 4" />,
  chevronRight: <path d="m6 4 4 4-4 4" />,
  plus: <path d="M8 3v10M3 8h10" />,
  settings: (
    <>
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 1.8v1.8M8 12.4v1.8M1.8 8h1.8M12.4 8h1.8M3.6 3.6l1.3 1.3M11.1 11.1l1.3 1.3M3.6 12.4l1.3-1.3M11.1 4.9l1.3-1.3" />
    </>
  ),
  download: <path d="M8 2.5v7.5M4.8 7 8 10.2 11.2 7M3 13h10" />,
  copy: (
    <>
      <rect x="5" y="5" width="8.5" height="8.5" rx="1.5" />
      <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" />
    </>
  ),
  lock: (
    <>
      <rect x="3" y="7" width="10" height="7" rx="1.5" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
    </>
  ),
  alert: (
    <>
      <path d="M8 2 14.5 13.5h-13z" />
      <path d="M8 6.5v3M8 11.6v.1" />
    </>
  ),
  check: <path d="m3.5 8.5 3 3 6-7" />,
  arrowLeft: <path d="M13 8H3.5M7.5 4 3.5 8l4 4" />,
} as const

export type IconName = keyof typeof PATHS

export function Icon({
  name,
  size = 15,
  className = '',
}: {
  name: IconName
  size?: number
  className?: string
}) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={`flex-none ${className}`}
    >
      {PATHS[name]}
    </svg>
  )
}
