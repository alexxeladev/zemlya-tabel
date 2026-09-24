import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import { loanShortfallHint, paymentsLeftLabel, paymentsWord, shortMonthLabel } from './loan.ts'

test('склонение платежей', () => {
  assert.equal(paymentsWord(1), '1 платёж')
  assert.equal(paymentsWord(2), '2 платежа')
  assert.equal(paymentsWord(5), '5 платежей')
  assert.equal(paymentsWord(11), '11 платежей')
  assert.equal(paymentsWord(21), '21 платёж')
  assert.equal(paymentsWord(0), '0 платежей')
})

test('карточка показывает, что платежей больше исходного срока', () => {
  const label = paymentsLeftLabel({ payments_left: 5, payments_left_by_term: 4, short_months: [] })
  assert.match(label, /Осталось 5 платежей/)
  assert.match(label, /по исходному сроку \(4 платежа\)/)
})

test('без пропусков про исходный срок не пишет', () => {
  const label = paymentsLeftLabel({ payments_left: 4, payments_left_by_term: 4, short_months: [] })
  assert.equal(label, 'Осталось 4 платежа')
})

test('месяц недоудержания называется словами, а не числами', () => {
  assert.equal(
    shortMonthLabel({ year: 2026, month: 7, planned: '5250', actual: '0' }),
    'Июль 2026: удержано 0 ₽ из 5 250 ₽',
  )
})

test('подсказка табеля различает ноль и частичное удержание', () => {
  assert.match(loanShortfallHint('0', '10000'), /Начислений в месяце нет/)
  assert.match(loanShortfallHint('2308', '16667'), /удержано 2 308 ₽ из 16 667 ₽/)
})

test('карточка и табель пользуются этими подписями', () => {
  const card = readFileSync(new URL('../pages/admin/LoanStatusPanel.tsx', import.meta.url), 'utf8')
  assert.match(card, /paymentsLeftLabel/)
  assert.match(card, /shortMonthLabel/)
  const ts = readFileSync(new URL('../pages/TimesheetPage.tsx', import.meta.url), 'utf8')
  assert.match(ts, /loanShortfallHint/)
})
