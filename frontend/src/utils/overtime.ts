/**
 * Часы переработки строки табеля — ОДНО место, где это правило живёт на фронте.
 *
 * Переработка = сверхурочные (сверх дневной нормы смены) ПЛЮС работа в свой
 * выходной по графику ПЛЮС работа в праздник, то есть всё, что оплачено по
 * коэффициенту. Ровно эти категории складывает колонка переработки ведомости
 * (`build_payroll_statement` в `app/services/payroll_statement.py`), и число в
 * табеле обязано совпадать с ней — иначе два экрана снова отвечают на один
 * вопрос разными числами.
 *
 * Это НЕ «факт − норма»: дельта вычитает недобор нормы (у примера задачи 73 − 4
 * = 69) и в деньгах не участвует. Прежняя колонка «Δ» показывала именно её.
 */
export interface OvertimeParts {
  overtime_hours?: string | null
  off_schedule_hours?: string | null
  holiday_hours?: string | null
}

const num = (v: string | null | undefined): number => (v == null ? 0 : Number(v) || 0)

/** Часы сверху за месяц; `null` — расчёта нет, показывать прочерк. */
export function overtimeHours(pay: OvertimeParts | null | undefined): number | null {
  if (!pay) return null
  return num(pay.overtime_hours) + num(pay.off_schedule_hours) + num(pay.holiday_hours)
}
