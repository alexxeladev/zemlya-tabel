import { create } from 'zustand'

import { UI_KEYS, loadValidated, saveUiState } from '../utils/persist'

// Режим отображения табеля. Хранится в zustand и дублируется в localStorage
// (task_ux_improvements ч.3), чтобы выбор не сбрасывался ни при смене
// месяца/отдела, ни при уходе в другой раздел, ни при перезагрузке страницы.
export type TimesheetViewMode = 'classic' | 'company'

/**
 * Выбранный отдел: id отдела, `'all'` — все отделы явным выбором,
 * `null` — ещё не выбран (табель не грузим).
 *
 * Табель на 200 сотрудников — это тысячи ячеек в DOM и полный расчёт ЗП по всем;
 * грузить это при каждом заходе незачем, поэтому отдел выбирается явно. «Все
 * отделы» остаются доступны как отдельный пункт — сводные итоги в подвале нужны
 * бухгалтеру.
 *
 * Выбор тоже сохраняется: после F5 экран не спрашивает отдел заново. Чужой отдел
 * из хранилища не опасен — `TimesheetPage` сбрасывает выбор, которого нет в
 * загруженном списке отделов (после смены пользователя это уже проверялось).
 */
export type DeptChoice = number | 'all' | null

const isMode = (v: unknown): boolean => v === 'classic' || v === 'company'
const isDeptChoice = (v: unknown): boolean =>
  v === 'all' || v === null || (typeof v === 'number' && Number.isFinite(v))

interface TimesheetViewState {
  mode: TimesheetViewMode
  setMode: (mode: TimesheetViewMode) => void
  deptChoice: DeptChoice
  setDeptChoice: (choice: DeptChoice) => void
}

export const useTimesheetViewStore = create<TimesheetViewState>((set) => ({
  // По умолчанию — классический вид, чтобы для текущих пользователей ничего
  // внезапно не поменялось. Новый вид включается тумблером.
  mode: loadValidated<TimesheetViewMode>(UI_KEYS.timesheetView, 'classic', isMode),
  setMode: (mode) => {
    saveUiState(UI_KEYS.timesheetView, mode)
    set({ mode })
  },
  deptChoice: loadValidated<DeptChoice>(UI_KEYS.timesheetDept, null, isDeptChoice),
  setDeptChoice: (choice) => {
    saveUiState(UI_KEYS.timesheetDept, choice)
    set({ deptChoice: choice })
  },
}))
