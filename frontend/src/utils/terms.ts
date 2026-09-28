/**
 * Версии условий труда рабочего места (task_stage3_historicity).
 *
 * Условия — оклад/ставка, тип оплаты, график, коэффициенты, официальная
 * зарплата охранника, распределение по юрлицам — хранятся версиями с датой
 * начала действия. Изменение в карточке спрашивает дату; по умолчанию —
 * 1-е число следующего месяца (решение заказчика). Распределение и ставка
 * налога вахты меняются только с 1-го числа.
 *
 * Зеркало `services/position_terms.default_effective_from` на бэке: бэк
 * отдаёт ту же дату в `default_effective_from`, здесь она нужна до ответа,
 * чтобы форма открылась уже с датой. Разойтись им нельзя — держит тест.
 *
 * Сравнения условий здесь НЕТ (сняли в ADR-001, task_terms_per_field): фронт
 * больше не решает диффом, спрашивать ли дату. Условия меняются группами через
 * диалог «Изменить», который дату спрашивает ВСЕГДА, — иначе правку задним
 * числом тем же значением внести нельзя вовсе. Подписи значений групп — в
 * `utils/positionTerms.ts`.
 *
 * Модуль без JSX — проверяется `npm test`.
 */

/** ISO-дата `YYYY-MM-DD` по локальному календарю (не UTC: полночь по Москве). */
function iso(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

/** 1-е число следующего месяца — дата начала изменения по умолчанию. */
export function defaultEffectiveFrom(today: Date = new Date()): string {
  const y = today.getFullYear()
  const m = today.getMonth() + 1
  return m === 12 ? iso(y + 1, 1, 1) : iso(y, m + 1, 1)
}

/** 1-е число месяца по ISO-дате: распределение и налог — только с 1-го. */
export function firstOfMonth(value: string): string {
  const [y, m] = value.split('-')
  return y && m ? `${y}-${m}-01` : value
}

/** «YYYY-MM» из поля `<input type="month">` → 1-е число этого месяца. */
export function monthInputToIso(value: string): string {
  return value ? `${value}-01` : ''
}

/** Подпись начала версии: первой, перенесённой миграцией, — «с начала». */
export function effectiveLabel(value: string | null | undefined): string {
  if (!value) return 'с начала'
  const [y, m, d] = value.split('-')
  return `с ${d}.${m}.${y}`
}

/** Подпись месяца версии распределения/налога: «с 10.2026» или «с начала». */
export function effectiveMonthLabel(value: string | null | undefined): string {
  if (!value) return 'с начала'
  const [y, m] = value.split('-')
  return `с ${m}.${y}`
}

