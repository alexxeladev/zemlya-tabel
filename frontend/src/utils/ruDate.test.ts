/**
 * Дата по-русски для поля `DateField` дизайн-системы.
 *
 * Запуск:  cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isoToRu, ruToIso, shiftMonth } from './ruDate.ts'

test('ISO показывается как ДД.ММ.ГГГГ', () => {
  assert.equal(isoToRu('2026-08-14'), '14.08.2026')
  assert.equal(isoToRu(null), '')
  assert.equal(isoToRu(''), '')
})

test('русская запись разбирается в ISO, в том числе короткая', () => {
  assert.equal(ruToIso('14.08.2026'), '2026-08-14')
  assert.equal(ruToIso('1.8.26'), '2026-08-01')
  assert.equal(ruToIso(' 01/08/2026 '), '2026-08-01')
})

test('пустое поле — законное «без ограничения», а не ошибка', () => {
  assert.equal(ruToIso(''), '')
  assert.equal(ruToIso('   '), '')
})

test('несуществующая дата и мусор не распознаются', () => {
  assert.equal(ruToIso('31.02.2026'), null)
  assert.equal(ruToIso('08/14/2026'), null) // американский порядок — не наш
  assert.equal(ruToIso('август'), null)
})

test('туда и обратно без потерь', () => {
  for (const iso of ['2026-01-01', '2026-12-31', '2028-02-29']) {
    assert.equal(ruToIso(isoToRu(iso)), iso)
  }
})

test('сдвиг месяца переходит через границу года', () => {
  assert.deepEqual(shiftMonth(2026, 12, 1), { year: 2027, month: 1 })
  assert.deepEqual(shiftMonth(2026, 1, -1), { year: 2025, month: 12 })
  assert.deepEqual(shiftMonth(2026, 8, 0), { year: 2026, month: 8 })
})
