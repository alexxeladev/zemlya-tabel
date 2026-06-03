export function formatMoney(value: string | null, options?: { showZero?: boolean }): string {
  if (value === null || value === undefined) return '—'
  const num = parseFloat(value)
  if (isNaN(num)) return '—'
  if (num === 0 && !options?.showZero) return '—'
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(num)
}

export function formatHours(value: string | null): string {
  if (value === null || value === undefined) return '—'
  const num = parseFloat(value)
  if (isNaN(num)) return '—'
  if (num === 0) return '0'
  return num % 1 === 0 ? String(num) : num.toFixed(2).replace(/\.?0+$/, '')
}

export function formatDelta(value: string | null): { text: string; className: string } {
  if (value === null || value === undefined) return { text: '—', className: 'text-gray-400' }
  const num = parseFloat(value)
  if (isNaN(num)) return { text: '—', className: 'text-gray-400' }
  if (num === 0) return { text: '0', className: 'text-gray-500' }
  if (num > 0) return { text: `+${num % 1 === 0 ? num : num.toFixed(2)}`, className: 'text-amber-600 font-medium' }
  return { text: `${num % 1 === 0 ? num : num.toFixed(2)}`, className: 'text-red-600 font-medium' }
}
