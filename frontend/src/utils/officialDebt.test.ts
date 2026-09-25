/**
 * Тексты переплаты по официальной выплате (task_official_payout_debt).
 *
 * Запуск:  cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  debtLabel,
  debtMovementLabel,
  finalDebtLabel,
  hasDebtToShow,
} from './officialDebt.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p: string) => readFileSync(resolve(here, p), 'utf8')

const row = (over: Partial<Parameters<typeof debtLabel>[0]> = {}) => ({
  year: 2026, month: 9, position_id: 1, position_title: 'Охранник',
  debt: '15000', debt_before_month: '0', repaid_in_month: '0',
  closed_on: null, is_final: false, ...over,
})

test('панель молчит, когда долга нет и не было', () => {
  assert.equal(hasDebtToShow(row({ debt: '0' })), false)
  assert.equal(hasDebtToShow(row()), true)
  assert.equal(hasDebtToShow(row({ debt: '0', repaid_in_month: '15000' })), true)
})

test('подпись называет месяц и сумму', () => {
  assert.match(debtLabel(row()), /Переплата на конец сентября 2026/)
  // formatMoney ставит НЕРАЗРЫВНЫЙ пробел — обычный здесь не совпадёт.
  assert.match(debtLabel(row()), /15\s000/)
})

test('движение за месяц: погашено, выросло, без движения', () => {
  assert.match(
    debtMovementLabel(row({ debt: '0', debt_before_month: '15000', repaid_in_month: '15000' })),
    /Погашено полностью/,
  )
  assert.match(
    debtMovementLabel(row({ debt: '5000', debt_before_month: '20000', repaid_in_month: '15000' })),
    /Погашено за месяц/,
  )
  // Банк заплатил, смен не было — долг вырос.
  assert.match(
    debtMovementLabel(row({ debt: '30000', debt_before_month: '15000' })),
    /Выросла за месяц/,
  )
  assert.equal(
    debtMovementLabel(row({ debt: '15000', debt_before_month: '15000' })),
    'За месяц без движения',
  )
})

test('увольнение: остаток зафиксирован с датой', () => {
  assert.equal(finalDebtLabel(row()), null)
  assert.match(
    finalDebtLabel(row({ is_final: true, closed_on: '2026-09-20' })) ?? '',
    /Место закрыто 20\.09\.2026 — остаток зафиксирован как задолженность/,
  )
})

test('панель и строка табеля берут долг с бэка, своих формул нет', () => {
  const panel = read('../pages/admin/OfficialDebtPanel.tsx')
  assert.match(panel, /getOfficialDebt\(employeeId\)/)
  const page = read('../pages/VahtaPage.tsx')
  // Долг показывается в ячейке «К выплате» и сравнивается в memo строки.
  assert.match(page, /official_debt_after/)
  assert.match(page, /a\.row\.official_debt_after === b\.row\.official_debt_after/)
})
