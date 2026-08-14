import { create } from 'zustand'

import { UI_KEYS, isYearMonth, loadValidated, saveUiState } from '../utils/persist'

/**
 * Выбранный месяц — ОБЩИЙ для разделов, работающих с одним периодом
 * (табель и «Расчёт ЗП»): выбрал май в табеле — в ведомости открылся май
 * (task_ux_improvements ч.3).
 *
 * Хранится в localStorage, поэтому переживает и переход между разделами, и
 * перезагрузку страницы. Хранилище недоступно или значение битое — берётся
 * текущий месяц, экран работает как раньше.
 *
 * Дашборд сюда НЕ входит: у него период — диапазон месяцев, со своей семантикой
 * и своим ключом хранения.
 */
type YearMonth = { year: number; month: number }

const currentMonth = (): YearMonth => {
  const now = new Date()
  return { year: now.getFullYear(), month: now.getMonth() + 1 }
}

interface PeriodState extends YearMonth {
  setPeriod: (year: number, month: number) => void
  setYear: (year: number) => void
  setMonth: (month: number) => void
}

export const usePeriodStore = create<PeriodState>((set, get) => {
  const persist = (next: YearMonth) => {
    saveUiState(UI_KEYS.period, next)
    set(next)
  }
  return {
    ...loadValidated<YearMonth>(UI_KEYS.period, currentMonth(), isYearMonth),
    setPeriod: (year, month) => persist({ year, month }),
    setYear: (year) => persist({ year, month: get().month }),
    setMonth: (month) => persist({ year: get().year, month }),
  }
})
