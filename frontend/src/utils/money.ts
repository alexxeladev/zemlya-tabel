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

/**
 * Подсказка к сумме «К выплате»: как её округлили (task_payout_rounding).
 *
 * Округление МАТЕМАТИЧЕСКОЕ до 1000 ₽, поэтому хвост (точное − округлённое)
 * бывает обоих знаков: плюс — сумма ушла вниз и осела в пользу компании,
 * минус — компания доплатила до ближайшей тысячи. Одна функция на все экраны,
 * чтобы формулировки в табеле и ведомости не разъезжались.
 *
 * Возвращает undefined, когда округлять было нечего (сумма и так кратна 1000).
 */
export function payoutRoundingHint(
  exact: string | null | undefined,
  tail: string | null | undefined,
): string | undefined {
  const t = parseFloat(tail ?? '')
  if (!Number.isFinite(t) || t === 0) return undefined
  const precise = formatMoney(exact ?? null, { showZero: true })
  const amount = formatMoney(String(Math.abs(t)), { showZero: true })
  return t > 0
    ? `Округлено ВНИЗ до 1000 ₽: точная сумма ${precise}, удержано при округлении ${amount}`
    : `Округлено ВВЕРХ до 1000 ₽: точная сумма ${precise}, доплачено при округлении ${amount}`
}
