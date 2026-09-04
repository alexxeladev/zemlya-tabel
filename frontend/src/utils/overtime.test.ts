/**
 * Часы переработки в ТАБЕЛЕ (task_overtime_columns, доработка по итогам показа).
 *
 * Запуск:  cd frontend && npm test
 * (node:test + нативный TypeScript в Node 24, дополнительных зависимостей нет)
 *
 * Требование: в табеле, на месте прежней колонки «Δ», должно стоять то же
 * число часов, что и в колонке переработки ведомости, — сверхурочные ПЛЮС
 * работа в выходные и праздники по графику. Дельта «факт − норма» показывала
 * 69 там, где сверху отработано 73 часа: она вычитает недобор нормы.
 *
 * Проверка построена ПО ТРЕБОВАНИЮ: помимо самой функции здесь сверка с
 * правилом бэкенда (`build_payroll_statement`) по его исходнику и проверка,
 * что страница табеля не считает эту сумму своей арифметикой на месте.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { overtimeHours } from './overtime.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p: string) => readFileSync(resolve(here, p), 'utf8')

// Проверочный пример задачи: 28 сверхурочных + 45 в выходные = 73 ч.
const DEEV = {
  overtime_hours: '28',
  off_schedule_hours: '45',
  holiday_hours: '0',
  total_hours: '237',
  norm_hours: '168',
}

test('пример задачи: 28 сверхурочных + 45 в выходные = 73 ч', () => {
  assert.equal(overtimeHours(DEEV), 73)
})

test('это НЕ дельта «факт − норма» (она дала бы 69)', () => {
  assert.notEqual(overtimeHours(DEEV), 237 - 168)
})

test('праздничные часы входят наравне с выходными', () => {
  assert.equal(
    overtimeHours({ overtime_hours: '4', off_schedule_hours: '0', holiday_hours: '8' }),
    12,
  )
})

test('нет переработки — ноль, а не отрицательное число', () => {
  assert.equal(
    overtimeHours({ overtime_hours: '0', off_schedule_hours: '0', holiday_hours: '0' }),
    0,
  )
  // недобор нормы колонку не трогает: она про часы СВЕРХУ
  const underworked = {
    overtime_hours: '0', off_schedule_hours: '0', holiday_hours: '0',
    total_hours: '100', norm_hours: '168',
  }
  assert.equal(overtimeHours(underworked), 0)
})

test('расчёта нет — прочерк (null), а не ноль', () => {
  assert.equal(overtimeHours(null), null)
  assert.equal(overtimeHours(undefined), null)
})

test('дробные часы складываются без потери', () => {
  assert.equal(
    overtimeHours({ overtime_hours: '1.5', off_schedule_hours: '2.5', holiday_hours: '0' }),
    4,
  )
})

test('правило то же, что в ведомости на бэкенде', () => {
  // Бэк собирает колонку переработки ведомости из тех же трёх категорий.
  // Разъехавшись, табель и ведомость снова показали бы разные числа —
  // ровно то, из-за чего задача и появилась.
  const backend = read('../../../backend/app/services/payroll_statement.py')
  const block = backend.slice(backend.indexOf('overtime_hours = ('))
  const expr = block.slice(0, block.indexOf(')') + 1)
  for (const field of ['p.overtime_hours', 'p.off_schedule_hours', 'p.holiday_hours']) {
    assert.ok(expr.includes(field), `бэк перестал складывать ${field}: ${expr}`)
  }
})

test('табель берёт сумму из этой функции, а не считает на месте', () => {
  const page = read('../pages/TimesheetPage.tsx')
  assert.ok(page.includes("from '../utils/overtime'"), 'страница не импортирует правило')
  // Прежней колонки отклонения в табеле не осталось
  assert.ok(!page.includes('DeltaCell'), 'колонка Δ всё ещё на месте')
  assert.ok(!page.includes('delta_hours'), 'страница всё ещё читает delta_hours')
})
