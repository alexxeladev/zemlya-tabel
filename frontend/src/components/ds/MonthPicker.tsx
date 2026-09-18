import { MONTHS_RU, shiftMonth } from '../../utils/ruDate'
import { Icon } from './Icon'

/**
 * Выбор месяца по-русски (`.ctx` макетов): ‹ Август 2026 ›.
 *
 * Вместо `<input type="month">`: тот рисует месяц по локали БРАУЗЕРА —
 * «September 2026» прямо под русской подписью (§4.3 «Русский интерфейс —
 * русские форматы»). Здесь формат задаём мы.
 */
export function MonthPicker({
  year,
  month,
  onChange,
  label = 'Месяц',
}: {
  year: number
  month: number
  onChange: (year: number, month: number) => void
  label?: string
}) {
  const go = (delta: number) => {
    const next = shiftMonth(year, month, delta)
    onChange(next.year, next.month)
  }
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex h-8 items-center gap-0.5 rounded-ds-md border border-ds-control-line bg-ds-surface p-0.5"
    >
      <button
        type="button"
        onClick={() => go(-1)}
        aria-label="Предыдущий месяц"
        className="grid h-[26px] w-[26px] cursor-pointer place-items-center rounded-ds-sm text-ds-muted hover:bg-ds-surface-3 hover:text-ds-ink"
      >
        <Icon name="chevronLeft" size={13} />
      </button>
      {/* Нативные select внутри: месяц и год выбираются и с клавиатуры, и
          прыжком через несколько месяцев, а подпись остаётся русской. */}
      <select
        value={month}
        onChange={(e) => onChange(year, Number(e.target.value))}
        aria-label="Месяц"
        className="h-[26px] cursor-pointer appearance-none rounded-ds-sm bg-transparent px-1.5 text-[13.5px] font-medium text-ds-ink hover:bg-ds-surface-3"
      >
        {MONTHS_RU.map((name, i) => (
          <option key={name} value={i + 1}>
            {name}
          </option>
        ))}
      </select>
      <select
        value={year}
        onChange={(e) => onChange(Number(e.target.value), month)}
        aria-label="Год"
        className="h-[26px] cursor-pointer appearance-none rounded-ds-sm bg-transparent px-1 text-[13.5px] font-medium tabular-nums text-ds-ink hover:bg-ds-surface-3"
      >
        {Array.from({ length: 7 }, (_, i) => year - 3 + i).map((y) => (
          <option key={y} value={y}>
            {y}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => go(1)}
        aria-label="Следующий месяц"
        className="grid h-[26px] w-[26px] cursor-pointer place-items-center rounded-ds-sm text-ds-muted hover:bg-ds-surface-3 hover:text-ds-ink"
      >
        <Icon name="chevronRight" size={13} />
      </button>
    </div>
  )
}
