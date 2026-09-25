/**
 * Режим половины: строки без смен не показываются (решение заказчика 25.09.2026).
 *
 * Запуск:  cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  countPeople,
  hasShiftsInPeriod,
  hiddenInPeriodCount,
  showsInPeriod,
} from './vahtaRows.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p: string) => readFileSync(resolve(here, p), 'utf8')

test('нет смен в половине — строки нет', () => {
  assert.equal(showsInPeriod({ shifts: 0 }, 1), false)
  assert.equal(showsInPeriod({ shifts: 0 }, 2), false)
  assert.equal(showsInPeriod({ shifts: 15 }, 2), true)
  assert.equal(showsInPeriod({ shifts: 1 }, 1), true)
})

test('в режиме месяца показываются все строки, как раньше', () => {
  assert.equal(showsInPeriod({ shifts: 0 }, null), true)
  assert.equal(showsInPeriod({ shifts: 30 }, null), true)
})

test('строка без смен прячется и когда за половину есть деньги', () => {
  // Официальная выплата идёт по календарным дням на посту, а не по сменам:
  // у отработавшего только первую половину во второй остаётся банковская
  // выплата и минусовая «к выплате». Заказчику показано, решение — прятать.
  assert.equal(showsInPeriod({ shifts: 0 }, 2), false)
  assert.equal(hasShiftsInPeriod({ shifts: 0 }), false)
})

test('правленая в этом сеансе строка остаётся видимой', () => {
  // Снял последнюю смену — строка не должна исчезнуть под курсором: вернуть
  // её было бы нечем.
  assert.equal(showsInPeriod({ shifts: 0 }, 1, true), true)
  assert.equal(showsInPeriod({ shifts: 0 }, 1, false), false)
})

test('счёт скрытых: столько, сколько пропало с экрана', () => {
  const rows = [{ shifts: 15 }, { shifts: 0 }, { shifts: 0 }, { shifts: 3 }]
  assert.equal(hiddenInPeriodCount(rows, 2), 2)
  assert.equal(hiddenInPeriodCount(rows, null), 0, 'в режиме месяца не скрыто ничего')
  assert.equal(rows.filter((r) => showsInPeriod(r, 2)).length, 2)
})

test('счётчик карточки считает людей по ПОКАЗАННЫМ строкам', () => {
  const all = [
    { employee_id: 1, shifts: 15 },
    { employee_id: 2, shifts: 0 },
    { employee_id: 3, shifts: 0 },
    { employee_id: 1, shifts: 3 },   // тот же человек на втором посту объекта
    { employee_id: null, shifts: 0 }, // пустой слот — не человек
  ]
  assert.equal(countPeople(all), 3, 'в месяце видны все трое')
  const shown = all.filter((r) => showsInPeriod(r, 1))
  assert.equal(countPeople(shown), 1, 'в половине со сменами остался один человек')
  // Ровно то, что ловило ревью: «4 чел.» над двумя строками.
  assert.notEqual(countPeople(all), countPeople(shown))
})

test('экран берёт правило отсюда и объясняет скрытые строки', () => {
  const page = read('../pages/VahtaPage.tsx')
  assert.match(page, /import \{[^}]*showsInPeriod[^}]*\} from '\.\.\/utils\/vahtaRows'/)
  // Фильтр стоит в отборе строк, рядом с остальными фильтрами экрана.
  assert.match(page, /matches\(r, card\) && showsInPeriod\(r, viewHalf, editedRows\.has\(r\.id\)\)/)
  // Скрытые строки названы: иначе пропажа людей читается как поломка, а итоги
  // подвала считаются сервером по ВСЕМ строкам месяца.
  assert.match(page, /скрыто без смен/)
  // Правка дня помечает строку, чтобы она не исчезла прямо под курсором.
  assert.match(page, /setEditedRows\(\(prev\) =>/)
  // Пометки снимаются при уходе на другой месяц или режим, а НЕ в reload:
  // снятие смен само заканчивается перечитыванием месяца, и строка, из которой
  // убрали последнюю смену, иначе пропадала бы сразу после сохранения.
  assert.match(page, /setEditedRows\(new Set\(\)\)\n\s*\}, \[year, month, view\]\)/)
  const reloadBody = page.slice(page.indexOf('const reload = useCallback'), page.indexOf('useEffect(() => {\n    setLoading(true)'))
  assert.doesNotMatch(reloadBody, /setEditedRows/, 'reload не должен снимать пометки')
  // Счётчик карточки места строится по показанным строкам, а не по card.rows.
  assert.match(page, /\{placeCount\(rows\)\}/)
  assert.doesNotMatch(page, /placeCount\(card\)/)
  // Пустой экран называет настоящую причину, когда строки скрыты фильтром.
  assert.match(page, /hiddenInPeriod > 0 \? \(/)
  assert.match(page, /В этой половине смен нет ни у кого/)
})
