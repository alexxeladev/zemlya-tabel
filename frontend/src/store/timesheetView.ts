import { create } from 'zustand'

import { isTimesheetDeptChoice } from '../utils/departments'
import { UI_KEYS, loadValidated, saveUiState } from '../utils/persist'

// Состояние экрана табеля: выбранный отдел. Хранится в zustand и дублируется
// в localStorage (task_ux_improvements ч.3), чтобы выбор не сбрасывался ни при
// смене месяца, ни при уходе в другой раздел, ни при перезагрузке страницы.
//
// Здесь же жил режим отображения (`classic` / `company`) — вид «по компаниям»
// снят в task_pilot_ux: за пилот он не прижился, а поддерживать вторую
// отрисовку тех же данных приходилось в каждой правке табеля. Режим «все
// отделы» снят в task_timesheet_dept_only — см. DeptChoice ниже.

/**
 * Выбранный отдел: id отдела, `'none'` — группа «Без отдела», `null` — ещё не
 * выбран (табель не грузим).
 *
 * Режима «все отделы» БОЛЬШЕ НЕТ (task_timesheet_dept_only). Он открывал сотни
 * строк и полный расчёт ЗП по всей компании ради поиска человека, которого
 * ищут в справочнике сотрудников. Табель открывается по ОДНОМУ отделу.
 *
 * Группа «Без отдела» — отдельное значение, а не «фильтр не задан»: иначе
 * сотрудники без отдела и их периоды стали бы недостижимы с экрана вовсе. На
 * бэк она уходит как `?department_id=none`.
 *
 * Выбор сохраняется: после F5 экран не спрашивает отдел заново. Чужой отдел из
 * хранилища не опасен — `TimesheetPage` сбрасывает выбор, которого нет в
 * загруженном списке отделов (после смены пользователя это уже проверялось).
 */
export type DeptChoice = number | 'none' | null

// Сохранённое значение прежней версии экрана (`'all'`) валидатор НЕ принимает,
// и `loadValidated` отдаёт `null` — человек попадает на выбор отдела, а не в
// сломанный режим. Сам валидатор живёт в `utils/departments` вместе с
// остальными правилами про отделы, поэтому проверяется обычным `npm test`.
const isDeptChoice = (v: unknown): boolean => isTimesheetDeptChoice(v)

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
