import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import {
  NOT_SET,
  money,
  termGroupSummary,
  termPlannedText,
  termValueText,
  workplacePeriodText,
} from './positionTerms.ts'

const SCHEDULES = { '43': '5/2', '44': '6/1' }

/** Intl ставит НЕразрывный пробел между разрядами — сравниваем по обычному. */
const plain = (v: string | null) => (v == null ? null : v.replace(/[\u00a0\u202f]/g, ' '))
const summary = (...args: Parameters<typeof termGroupSummary>) =>
  plain(termGroupSummary(...args))
const valueText = (...args: Parameters<typeof termValueText>) => plain(termValueText(...args))

test('тип оплаты и ставка — одной подписью', () => {
  assert.equal(
    summary('pay', { pay_type: 'salary', rate: '60000.00' }),
    'Окладная · 60 000 ₽/мес',
  )
  assert.equal(
    summary('pay', { pay_type: 'per_shift', shift_rate: '2500.00' }),
    'Посменная · 2 500 ₽/смена',
  )
  assert.equal(
    summary('pay', { pay_type: 'hourly', hour_rate: '450.00' }),
    'Почасовая · 450 ₽/час',
  )
})

test('не заданное значение — словом, а не пустотой', () => {
  assert.equal(termGroupSummary('pay', { pay_type: 'salary', rate: null }), `Окладная · ${NOT_SET}`)
  assert.equal(termGroupSummary('overtime', { overtime_coefficient: null }), NOT_SET)
  assert.equal(termGroupSummary('schedule', { schedule_id: null }), 'не указан')
})

test('график — названием, в том числе снятого с учёта', () => {
  assert.equal(termGroupSummary('schedule', { schedule_id: 43 }, SCHEDULES), '5/2')
  assert.equal(termGroupSummary('schedule', { schedule_id: 99 }, SCHEDULES), '#99')
})

test('коэффициент и фикс-ставка различаются', () => {
  assert.equal(
    termGroupSummary('weekend', {
      weekend_pay_type: 'coefficient', weekend_coefficient: '1.50',
    }),
    '×1.5',
  )
  assert.equal(
    termGroupSummary('holiday', {
      holiday_pay_type: 'fixed_rate', holiday_fixed_rate: '740.00',
    }),
    '740 ₽/ч (фикс.)',
  )
})

test('официальное трудоустройство: признак и сумма на руки', () => {
  assert.equal(termGroupSummary('official', { is_official: false }), 'Неофициально')
  assert.equal(
    summary('official', { is_official: true, official_salary: '50000.00' }),
    'Официально · 50 000 ₽ на руки',
  )
})

test('запланированное читается поверх действующего', () => {
  // Изменение может нести не все поля группы (форма вахты правит только оф.
  // зарплату) — остальное берётся из действующих значений.
  const current = { pay_type: 'salary', rate: '60000.00' }
  assert.equal(
    plain(termPlannedText('pay', current, {
      effective_label: 'с 01.12.2026', values: { rate: '100000.00' },
    })),
    'с 01.12.2026 будет Окладная · 100 000 ₽/мес',
  )
})

test('значение одного условия в истории', () => {
  assert.equal(valueText('rate', '90000.00'), '90 000 ₽')
  assert.equal(valueText('overtime_coefficient', '1.50'), '×1.5')
  assert.equal(valueText('schedule_id', 44, SCHEDULES), '6/1')
  assert.equal(valueText('pay_type', 'per_shift'), 'Посменная')
  assert.equal(valueText('is_official', true), 'да')
  assert.equal(valueText('shift_rate', null), NOT_SET)
  assert.equal(valueText('weekend_pay_type', 'fixed_rate'), 'фикс. ставка за час')
})

test('деньги форматируются разрядами', () => {
  assert.equal(plain(money('1234567.89')), '1 234 567,89')
  assert.equal(money(null), null)
})

test('группы условий совпадают с бэком: один список на систему', () => {
  const src = readFileSync(
    new URL('../../../backend/app/services/position_terms.py', import.meta.url), 'utf8',
  )
  const groups = [...src.matchAll(/^    "(\w+)": \(/gm)].map((m) => m[1])
  assert.deepEqual(groups.slice(0, 6), [
    'pay', 'schedule', 'weekend', 'holiday', 'overtime', 'official',
  ])
})

test('фронт не диффит условия: помощника сравнения больше нет', () => {
  const src = readFileSync(new URL('./terms.ts', import.meta.url), 'utf8')
  assert.equal(/export function termsChanged/.test(src), false)
})

test('период работы на месте: обе границы, одна, ни одной', () => {
  assert.equal(workplacePeriodText('2026-01-01', '2026-12-31'), 'с 01.01.2026 по 31.12.2026')
  assert.equal(workplacePeriodText('2026-01-01', null), 'с 01.01.2026')
  assert.equal(workplacePeriodText(null, '2026-12-31'), 'по 31.12.2026')
  assert.equal(workplacePeriodText(null, null), 'без ограничения')
})
