import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { Company } from '../types/api'
import { splitEqually } from '../utils/distribution'
import { Button } from './Button'

export type SharesMap = Record<number, string> // company_id → percent (строка из инпута)

type Props = {
  companies: Company[]
  shares: SharesMap
  onChange: (next: SharesMap) => void
  /** Основная компания — ей достаётся остаток при делении поровну. */
  mainCompanyId?: number | null
  /** Смена значения переинициализирует галочки из shares (после загрузки с сервера). */
  resetKey?: string | number
  disabled?: boolean
  /** Сумму показывает сам экран (у своего заголовка) — здесь её не рисуем. */
  hideSum?: boolean
  /** Что дорисовать в ряд с «Разнести поровну»: дата начала, кнопка сохранения.
   *  Одним рядом, а не тремя: у каждого экрана свои действия, но ряд общий. */
  trailing?: ReactNode
}

/** Сумма процентов набора — одно правило на редактор и на экраны, которые
 *  показывают её у своего заголовка. */
export function sharesSum(companies: Company[], shares: SharesMap): number {
  return companies
    .filter((c) => c.is_active)
    .reduce((acc, c) => acc + num(shares[c.id]), 0)
}

/** Набор «почти 100%»? Ниже этого порога сумму подсвечивают предупреждением. */
export function sharesWarn(sum: number): boolean {
  return sum > 0 && Math.abs(sum - 100) > 0.5
}

const num = (v: string | undefined): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/**
 * Редактор распределения по юрлицам: галочки выбора компаний, ручной ввод %,
 * кнопка «Разнести поровну» (task_distribution_v2 ч.2).
 *
 * Проценты ФИКСИРУЮТСЯ как конкретные значения: добавление новой компании в
 * справочник уже сохранённое распределение не меняет. Деление поровну — через
 * общий алгоритм (utils/distribution), сумма ровно 100%.
 */
export function SharesEditor({
  companies, shares, onChange, mainCompanyId, resetKey, disabled, trailing, hideSum,
}: Props) {
  const active = companies.filter((c) => c.is_active)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  useEffect(() => {
    setSelected(new Set(active.filter((c) => num(shares[c.id]) > 0).map((c) => c.id)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, companies.length])

  const sum = sharesSum(companies, shares)
  const warn = sharesWarn(sum)

  const toggle = (id: number) => {
    const next = new Set(selected)
    if (next.has(id)) {
      next.delete(id)
      onChange({ ...shares, [id]: '' }) // снятая галочка = 0%
    } else {
      next.add(id)
    }
    setSelected(next)
  }

  const setPercent = (id: number, value: string) => {
    if (num(value) > 0 && !selected.has(id)) setSelected(new Set(selected).add(id))
    onChange({ ...shares, [id]: value })
  }

  const splitSelected = () => {
    const ids = active.filter((c) => selected.has(c.id)).map((c) => c.id)
    const parts = splitEqually(ids, mainCompanyId ?? undefined)
    const next: SharesMap = {}
    for (const c of active) next[c.id] = parts[c.id] !== undefined ? String(parts[c.id]) : ''
    onChange(next)
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Юрлиц у группы восемь: в одну колонку список занимал треть карточки.
          На узком контейнере колонка остаётся одна. */}
      <div className="grid gap-2 sm:grid-cols-2">
      {active.map((c) => (
        <div key={c.id} className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={selected.has(c.id)}
            disabled={disabled}
            onChange={() => toggle(c.id)}
            className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            title="Участвует в распределении"
          />
          <span className="w-40 truncate text-sm text-gray-700" title={c.name}>
            {c.name}
            {mainCompanyId === c.id && (
              <span className="ml-1 text-[10px] uppercase text-gray-400">осн.</span>
            )}
          </span>
          <input
            type="number"
            min={0}
            max={100}
            step="0.01"
            disabled={disabled}
            value={shares[c.id] ?? ''}
            onChange={(e) => setPercent(c.id, e.target.value)}
            className="w-20 rounded-lg border border-gray-300 px-2 py-1 text-right text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
            placeholder="0"
          />
          <span className="text-sm text-gray-400">%</span>
        </div>
      ))}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled || selected.size === 0}
          onClick={splitSelected}
          // Пояснение — в подсказке кнопки: тремя строками оно стояло в каждом
          // месте, где есть редактор процентов, и занимало больше самой кнопки.
          title="100% делится между отмеченными компаниями, остаток достаётся основной. Проценты фиксируются: новые компании в справочнике их не изменят."
        >
          Разнести поровну
        </Button>
        {!hideSum && (
          <span className={`text-xs ${warn ? 'text-amber-600' : 'text-gray-400'}`}>
            Сумма: {Math.round(sum * 100) / 100}% {warn && '(должно быть ≈100%)'}
          </span>
        )}
        {trailing}
      </div>
    </div>
  )
}
