/**
 * Предпросмотр налога вахты — зеркало `employer_tax` бэка.
 *
 * Запуск:  cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { halfTaxKopecks, parseTaxPercent, taxForHalves } from './vahtaTax.ts'

const here = dirname(fileURLToPath(import.meta.url))

test('пример из ТЗ налогов: 25 230 × 40 % = 10 092', () => {
  assert.equal(halfTaxKopecks('25230.00', 40), 1009200)
  assert.equal(taxForHalves(['25230.00', '0.00'], 40), 10092)
})

test('копейки и дробная ставка — как Decimal бэка', () => {
  assert.equal(halfTaxKopecks('1000.01', 35), 35000) // 350.0035 → 350.00
  assert.equal(halfTaxKopecks('1000.00', 40.5), 40500)
  // Ровно половина копейки — к чётному (ROUND_HALF_EVEN), а не вверх.
  assert.equal(halfTaxKopecks('0.25', 10), 2) // 2.5 коп. → 2
  assert.equal(halfTaxKopecks('0.35', 10), 4) // 3.5 коп. → 4
})

test('налог считается по каждой половине отдельно, потом складывается', () => {
  assert.equal(taxForHalves(['1000.01', '1000.01'], 35), 700)
  assert.equal(taxForHalves([], 40), 0)
})

test('ввод ставки: 0…100, запятая как точка, мусор — null', () => {
  assert.equal(parseTaxPercent('40'), 40)
  assert.equal(parseTaxPercent(' 40,5 '), 40.5)
  assert.equal(parseTaxPercent('0'), 0)
  assert.equal(parseTaxPercent('101'), null)
  assert.equal(parseTaxPercent('-1'), null)
  assert.equal(parseTaxPercent('сорок'), null)
  assert.equal(parseTaxPercent(''), null)
})

test('формула та же, что у бэка: официальная выплата × ставка / 100 до копейки', () => {
  const backend = readFileSync(
    resolve(here, '../../../backend/app/services/guard_payroll.py'),
    'utf8',
  )
  assert.match(backend, /return \(official_payout \* _money\(tax_percent\) \/ 100\)\.quantize\(KOPECK\)/)
})
