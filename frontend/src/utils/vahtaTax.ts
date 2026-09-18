/**
 * Живой пример налога на вкладке «Налог» настроек вахты (task_vahta_settings_staff).
 *
 * Это ЗЕРКАЛО `employer_tax` из `backend/app/services/guard_payroll.py`:
 * налог = официальная выплата × ставка / 100, до копейки, по каждой расчётной
 * половине отдельно. Считает он только ПРЕДПРОСМОТР при вводе ставки — сам
 * налог после сохранения считает бэк. Расходиться им нельзя: при текущей
 * ставке предпросмотр обязан дать ровно ту сумму, что пришла с сервера
 * (держит тест, а экран показывает разницу к серверной цифре).
 *
 * Без JSX — проверяется `npm test`.
 */

/** Рубли строкой с бэка («25230.00») → копейки целым, без float-погрешности. */
function toKopecks(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === '') return 0
  const [int, frac = ''] = String(value).split('.')
  const sign = int.startsWith('-') ? -1 : 1
  return sign * (Math.abs(Number(int)) * 100 + Number((frac + '00').slice(0, 2)))
}

/** Округление к чётному (ROUND_HALF_EVEN — как `Decimal.quantize` бэка). */
function roundHalfEven(x: number): number {
  const floor = Math.floor(x)
  const diff = x - floor
  if (Math.abs(diff - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1
  return Math.round(x)
}

/**
 * Налог одной половины в копейках. Ставка — в процентах (40 = 40 %), может
 * быть дробной; её «хвост» (40.5) переводим в сотые доли процента целым числом.
 */
export function halfTaxKopecks(officialPayout: string | number, percent: number): number {
  const off = toKopecks(officialPayout)
  const bp = Math.round(percent * 100) // 40 % → 4000 сотых процента
  return roundHalfEven((off * bp) / 10000)
}

/** Налог по набору половин (строки месяца × две половины), в рублях. */
export function taxForHalves(officialPayouts: (string | number)[], percent: number): number {
  return officialPayouts.reduce<number>((sum, off) => sum + halfTaxKopecks(off, percent), 0) / 100
}

/** Разбор ввода ставки: «40», «40,5», « 35 » → число 0…100; иначе null. */
export function parseTaxPercent(text: string): number | null {
  const t = text.trim().replace(',', '.')
  if (!/^\d+(\.\d+)?$/.test(t)) return null
  const n = Number(t)
  return n >= 0 && n <= 100 ? n : null
}
