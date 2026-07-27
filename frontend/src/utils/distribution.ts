/**
 * Распределение сумм/процентов по компаниям — зеркало бэкенда.
 *
 * Оригинал: backend/app/services/distribution.py. Алгоритм обязан совпадать
 * посимвольно по результату, иначе экран и Excel разойдутся (ровно эту проблему
 * чинит task_distribution_v2 ч.1). Правишь здесь — правь и там.
 *
 * Метод: точная доля → округление до шага (half-even) → весь нераспределённый
 * остаток относится на основную компанию сотрудника (или на компанию с
 * наибольшей долей, если основной в наборе нет). Сумма частей = total ровно.
 */

const EPS = 1e-9

/** Округление half-even (как ROUND_HALF_EVEN в Decimal) до шага step. */
function roundHalfEven(value: number, step: number): number {
  const scaled = value / step
  const floor = Math.floor(scaled)
  const frac = scaled - floor
  let units: number
  if (frac > 0.5 + EPS) units = floor + 1
  else if (frac < 0.5 - EPS) units = floor
  else units = floor % 2 === 0 ? floor : floor + 1
  return units * step
}

/** Убрать «хвосты» плавающей точки на заданном шаге (0.1 + 0.2 → 0.3). */
function clean(value: number, step: number): number {
  const decimals = Math.max(0, Math.round(-Math.log10(step)))
  return Number(value.toFixed(decimals))
}

/**
 * Разложить total по ключам пропорционально весам; сумма частей = total ровно.
 * Нулевые/отрицательные веса отбрасываются, Σвесов ≠ 100 нормализуется.
 */
export function distribute(
  total: number,
  weights: Record<number, number>,
  mainKey?: number | null,
  step = 1,
): Record<number, number> {
  const positive: Record<number, number> = {}
  for (const [k, w] of Object.entries(weights)) {
    if (Number.isFinite(w) && w > 0) positive[Number(k)] = w
  }
  const keys = Object.keys(positive).map(Number)
  if (keys.length === 0) return {}

  const target = clean(roundHalfEven(total, step), step)
  const weightSum = keys.reduce((s, k) => s + positive[k], 0)
  const parts: Record<number, number> = {}
  if (target === 0 || weightSum <= 0) {
    for (const k of keys) parts[k] = 0
    return parts
  }

  let assigned = 0
  for (const k of keys) {
    const part = clean(roundHalfEven((target * positive[k]) / weightSum, step), step)
    parts[k] = part
    assigned += part
  }
  const leftover = clean(target - assigned, step)
  if (leftover !== 0) {
    const key =
      mainKey != null && mainKey in parts
        ? mainKey
        : keys.reduce((best, k) => (positive[k] > positive[best] ? k : best), keys[0])
    parts[key] = clean(parts[key] + leftover, step)
  }
  return parts
}

/**
 * «Разнести поровну»: 100% делится поровну между выбранными компаниями,
 * остаток — основной. Проценты фиксируются как конкретные значения.
 */
export function splitEqually(
  companyIds: number[],
  mainKey?: number | null,
  total = 100,
  step = 0.01,
): Record<number, number> {
  const ids = [...new Set(companyIds)].sort((a, b) => a - b)
  if (ids.length === 0) return {}
  const weights: Record<number, number> = {}
  for (const id of ids) weights[id] = 1
  return distribute(total, weights, mainKey, step)
}
