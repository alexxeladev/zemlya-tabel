import { useEffect, useState } from 'react'
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
  companies, shares, onChange, mainCompanyId, resetKey, disabled,
}: Props) {
  const active = companies.filter((c) => c.is_active)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  useEffect(() => {
    setSelected(new Set(active.filter((c) => num(shares[c.id]) > 0).map((c) => c.id)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, companies.length])

  const sum = active.reduce((acc, c) => acc + num(shares[c.id]), 0)
  const warn = sum > 0 && Math.abs(sum - 100) > 0.5

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

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled || selected.size === 0}
          onClick={splitSelected}
        >
          Разнести поровну
        </Button>
        <span className={`text-xs ${warn ? 'text-amber-600' : 'text-gray-400'}`}>
          Сумма: {Math.round(sum * 100) / 100}% {warn && '(должно быть ≈100%)'}
        </span>
      </div>
      <p className="text-[11px] text-gray-400">
        Отметьте компании галочками и нажмите «Разнести поровну» — 100% поделится
        между ними, остаток достанется основной компании. Проценты фиксируются:
        новые компании в справочнике их не изменят.
      </p>
    </div>
  )
}
