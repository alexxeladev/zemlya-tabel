import { create } from 'zustand'

// Режим отображения табеля. Хранится в zustand (не localStorage — в этом
// окружении он ненадёжен), чтобы выбор не сбрасывался при смене месяца/отдела.
export type TimesheetViewMode = 'classic' | 'company'

interface TimesheetViewState {
  mode: TimesheetViewMode
  setMode: (mode: TimesheetViewMode) => void
}

export const useTimesheetViewStore = create<TimesheetViewState>((set) => ({
  // По умолчанию — классический вид, чтобы для текущих пользователей ничего
  // внезапно не поменялось. Новый вид включается тумблером.
  mode: 'classic',
  setMode: (mode) => set({ mode }),
}))
