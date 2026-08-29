import { create } from 'zustand'

import { UI_KEYS, loadValidated, saveUiState } from '../utils/persist'

// Состояние экрана табеля: выбранный отдел. Хранится в zustand и дублируется
// в localStorage (task_ux_improvements ч.3), чтобы выбор не сбрасывался ни при
// смене месяца, ни при уходе в другой раздел, ни при перезагрузке страницы.
//
// Здесь же жил режим отображения (`classic` / `company`) — вид «по компаниям»
// снят в task_pilot_ux: за пилот он не прижился, а поддерживать вторую
// отрисовку тех же данных приходилось в каждой правке табеля.

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

const isDeptChoice = (v: unknown): boolean =>
  v === 'all' || v === null || (typeof v === 'number' && Number.isFinite(v))

interface TimesheetViewState {
  deptChoice: DeptChoice
  setDeptChoice: (choice: DeptChoice) => void
}

export const useTimesheetViewStore = create<TimesheetViewState>((set) => ({
  deptChoice: loadValidated<DeptChoice>(UI_KEYS.timesheetDept, null, isDeptChoice),
  setDeptChoice: (choice) => {
    saveUiState(UI_KEYS.timesheetDept, choice)
    set({ deptChoice: choice })
  },
}))
