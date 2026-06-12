// Общая палитра — единая для табеля (чипы компаний) и дашборда (графики).
// Цвет компании определяется её позицией в списке компаний с бэка.

export const COMPANY_PALETTE = [
  { bg: '#dbeafe', color: '#1d4ed8' }, // blue
  { bg: '#dcfce7', color: '#15803d' }, // green
  { bg: '#fef3c7', color: '#a16207' }, // amber
  { bg: '#fce7f3', color: '#be185d' }, // pink
  { bg: '#e9d5ff', color: '#7e22ce' }, // purple
  { bg: '#cffafe', color: '#0e7490' }, // cyan
  { bg: '#fed7aa', color: '#c2410c' }, // orange
  { bg: '#fecaca', color: '#b91c1c' }, // red
]

export function companyColorByIndex(idx: number) {
  return COMPANY_PALETTE[Math.max(0, idx) % COMPANY_PALETTE.length]
}

// Статусы периодов — те же тона, что бейджи в табеле
export const PERIOD_STATUS = {
  draft: { label: 'Черновик', badge: 'bg-gray-100 text-gray-700' },
  pending_review: { label: 'На проверке', badge: 'bg-yellow-100 text-yellow-800' },
  closed: { label: 'Закрыт', badge: 'bg-green-100 text-green-800' },
} as const

// Серии графиков дашборда
export const CHART = {
  norm: '#94a3b8',      // slate-400
  worked: '#3b82f6',    // blue-500
  overtime: '#f59e0b',  // amber-500
  payroll: '#15803d',   // green-700
  hours: '#3b82f6',
}
