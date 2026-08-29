import { create } from 'zustand'

/**
 * Личные отметки «строку проверил» (task_pilot_ux ч.3) — ОТДЕЛЬНЫМ стором,
 * а не состоянием `TimesheetPage`.
 *
 * Причина ровно одна и она измеримая: на пилоте (отдел в 70 человек) клик по
 * отметке занимал полсекунды. Отметка лежала в useState страницы, поэтому
 * каждый клик перерисовывал ВЕСЬ табель — 70 строк × 31 день, то есть 2170
 * ячеек: `React.memo` на `DayCell` спасает от их перерисовки, но сравнение
 * пропсов и пересоздание элементов всё равно выполняется на каждой.
 *
 * Со стором на клик перерисовывается ровно два маленьких компонента —
 * `RowCheckBox` отмечаемой строки и счётчик «Проверено N из M»; сама страница
 * не рендерится вовсе. Зелёная подсветка строки идёт CSS-ом от состояния
 * самого чекбокса (`tr:has(input.js-row-check:checked)` в index.css) и React
 * не касается вообще.
 *
 * Сеть остаётся в `TimesheetPage`: стор знает только про экран.
 */
interface RowChecksState {
  /** id отмеченных рабочих мест (только МОИ — бэк чужих не отдаёт) */
  checked: Set<number>
  /** заменить набор целиком: пришёл ответ месяца */
  setAll: (positionIds: number[]) => void
  /** оптимистичное переключение одной отметки (и откат при ошибке запроса) */
  set: (positionId: number, value: boolean) => void
}

export const useRowChecksStore = create<RowChecksState>((set) => ({
  checked: new Set<number>(),
  setAll: (positionIds) => set({ checked: new Set(positionIds) }),
  set: (positionId, value) =>
    set((state) => {
      if (state.checked.has(positionId) === value) return state
      // Новый Set обязателен: селекторы сравнивают по ссылке, мутация на месте
      // осталась бы незамеченной.
      const next = new Set(state.checked)
      if (value) next.add(positionId)
      else next.delete(positionId)
      return { checked: next }
    }),
}))
