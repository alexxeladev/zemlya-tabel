import { create } from 'zustand'

// Режим отображения табеля. Хранится в zustand (не localStorage — в этом
// окружении он ненадёжен), чтобы выбор не сбрасывался при смене месяца/отдела.
export type TimesheetViewMode = 'classic' | 'company'

/**
 * Выбранный отдел: id отдела, `'all'` — все отделы явным выбором,
 * `null` — ещё не выбран (табель не грузим).
 *
 * Табель на 200 сотрудников — это тысячи ячеек в DOM и полный расчёт ЗП по всем;
 * грузить это при каждом заходе незачем, поэтому отдел выбирается явно. «Все
 * отделы» остаются доступны как отдельный пункт — сводные итоги в подвале нужны
 * бухгалтеру.
 */
export type DeptChoice = number | 'all' | null

interface TimesheetViewState {
  mode: TimesheetViewMode
  setMode: (mode: TimesheetViewMode) => void
  // Хранится здесь, чтобы выбор не сбрасывался при уходе с экрана и возврате.
  deptChoice: DeptChoice
  setDeptChoice: (choice: DeptChoice) => void
}

export const useTimesheetViewStore = create<TimesheetViewState>((set) => ({
  // По умолчанию — классический вид, чтобы для текущих пользователей ничего
  // внезапно не поменялось. Новый вид включается тумблером.
  mode: 'classic',
  setMode: (mode) => set({ mode }),
  deptChoice: null,
  setDeptChoice: (choice) => set({ deptChoice: choice }),
}))
