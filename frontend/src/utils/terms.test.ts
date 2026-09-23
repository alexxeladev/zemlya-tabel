import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import {
  defaultEffectiveFrom,
  effectiveLabel,
  effectiveMonthLabel,
  firstOfMonth,
  monthInputToIso,
  termsChanged,
} from './terms.ts'

test('дата по умолчанию — 1-е число следующего месяца', () => {
  assert.equal(defaultEffectiveFrom(new Date(2026, 8, 23)), '2026-10-01')
  assert.equal(defaultEffectiveFrom(new Date(2026, 8, 1)), '2026-10-01')
})

test('декабрь переходит в январь следующего года', () => {
  assert.equal(defaultEffectiveFrom(new Date(2026, 11, 31)), '2027-01-01')
})

test('распределение и налог — только с 1-го числа', () => {
  assert.equal(firstOfMonth('2026-10-15'), '2026-10-01')
  assert.equal(monthInputToIso('2026-10'), '2026-10-01')
  assert.equal(monthInputToIso(''), '')
})

test('подписи версий: первая — «с начала»', () => {
  assert.equal(effectiveLabel(null), 'с начала')
  assert.equal(effectiveLabel('2026-05-15'), 'с 15.05.2026')
  assert.equal(effectiveMonthLabel('2026-10-01'), 'с 10.2026')
})

test('изменение условий распознаётся, числа сравниваются как числа', () => {
  const a = { rate: '60000.00', overtime_coefficient: '1.50', title: 'Инженер' }
  assert.equal(termsChanged(a, { ...a, rate: '60000' }), false)
  assert.equal(termsChanged(a, { ...a, title: 'Старший инженер' }), false)
  assert.equal(termsChanged(a, { ...a, rate: '65000' }), true)
})

test('зеркало бэка: то же правило «1-е число следующего месяца»', () => {
  const src = readFileSync(
    new URL('../../../backend/app/services/position_terms.py', import.meta.url), 'utf8',
  )
  assert.match(src, /def default_effective_from/)
  assert.match(src, /date\(today\.year \+ 1, 1, 1\)/)
  assert.match(src, /date\(today\.year, today\.month \+ 1, 1\)/)
})
