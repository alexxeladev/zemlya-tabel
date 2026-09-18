/**
 * Подписи вкладки «Сотрудники охраны».
 *
 * Запуск:  cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  amountUnit,
  changedFields,
  monthFactHint,
  monthFactsByPosition,
  officialLabel,
  placePeriod,
  plural,
  ruDate,
} from './staffFormat.ts'

test('числительные', () => {
  assert.equal(plural(1, 'человек', 'человека', 'человек'), '1 человек')
  assert.equal(plural(3, 'человек', 'человека', 'человек'), '3 человека')
  assert.equal(plural(96, 'человек', 'человека', 'человек'), '96 человек')
  assert.equal(plural(119, 'рабочее место', 'рабочих места', 'рабочих мест'), '119 рабочих мест')
  assert.equal(plural(12, 'смена', 'смены', 'смен'), '12 смен')
})

test('пустое — словами', () => {
  assert.equal(placePeriod(null, null), 'без ограничений')
  assert.equal(officialLabel(null), 'не на посту')
  assert.equal(officialLabel(true), 'да')
  assert.equal(officialLabel(false), 'нет')
})

test('период на месте по-русски', () => {
  assert.equal(ruDate('2026-09-30'), '30.09.2026')
  assert.equal(placePeriod('2026-03-01', '2026-09-30'), 'с 01.03.2026 по 30.09.2026')
  assert.equal(placePeriod('2026-03-01', null), 'с 01.03.2026')
  assert.equal(placePeriod(null, '2026-09-30'), 'по 30.09.2026')
})

test('единица суммы и изменённые поля', () => {
  assert.equal(amountUnit('salary'), 'в месяц')
  assert.equal(amountUnit('per_shift'), 'за смену')
  assert.deepEqual(
    changedFields({ a: '1', b: '2' }, { a: '1', b: '3' }, { a: 'ставка', b: 'дата' }),
    ['дата'],
  )
})

test('факт месяца: смены и зарплата по строкам табеля, а не по ставке рабочего места', () => {
  // Ставка рабочего места 5 000, но строка на посту 4 750 — платится 4 750.
  const map = monthFactsByPosition([
    { position_id: 7, days: [1, 2, 3], salary: '14250.00' },
    { position_id: 7, days: [20, 21], salary: '10000.00' }, // вторая строка — своя ставка
    { position_id: null, days: [5], salary: '5000.00' }, // вакансия не считается
  ])
  assert.deepEqual(map.get(7), { shifts: 5, pay: 24250 })
  assert.equal(map.size, 1)
  assert.equal(monthFactHint(map.get(7)!, 'сентябре'), 'В сентябре 5 смен · 24 250 ₽ по ставкам строк табеля')
})

test('факт месяца: нет смен, грузится, не загрузилось — разные слова', () => {
  assert.equal(monthFactHint({ shifts: 0, pay: 0 }, 'сентябре'), 'В сентябре смен нет')
  assert.equal(monthFactHint(null, 'сентябре'), undefined)
  assert.equal(monthFactHint('error', 'сентябре'), 'Смены месяца не загрузились — обновите страницу')
})
