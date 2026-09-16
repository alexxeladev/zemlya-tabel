/**
 * Период работы рабочего места на фронте (task_employment_period).
 *
 * Запуск:  cd frontend && npm test
 * (node:test + нативный TypeScript в Node 24, дополнительных зависимостей нет)
 *
 * Проверка построена ПО ТРЕБОВАНИЮ, а не по диффу: помимо самой функции здесь
 * сверка с правилом БЭКЕНДА по его исходнику (правило продублировано намеренно,
 * и разъехаться ему нельзя) и проверка, что страница табеля действительно
 * спрашивает это правило, а не считает границы своей арифметикой на месте.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  employmentBounds,
  employmentHint,
  isWithinAnyEmployment,
  isWithinEmployment,
  isoDay,
  withClearingConfirm,
  CLEARING_CANCELLED,
} from './employment.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p: string) => readFileSync(resolve(here, p), 'utf8')

const NONE = { hire_date: null, dismissal_date: null }

// ── Пустые даты ──────────────────────────────────────────────────────────────

test('без дат не ограничивается ничего — таких сотрудников большинство', () => {
  assert.deepEqual(employmentBounds(NONE, NONE), [null, null])
  for (const day of ['2020-01-01', '2026-06-15', '2099-12-31']) {
    assert.equal(isWithinEmployment(NONE, NONE, day), true)
    assert.equal(employmentHint(NONE, NONE, day), null)
  }
})

test('undefined/null вместо объекта не ломает правило', () => {
  assert.deepEqual(employmentBounds(undefined, null), [null, null])
  assert.equal(isWithinEmployment(undefined, null, '2026-06-15'), true)
})

// ── Границы включительные ────────────────────────────────────────────────────

test('уволен пятнадцатого — пятнадцатое ещё рабочий день', () => {
  const emp = { dismissal_date: '2026-06-15' }
  assert.equal(isWithinEmployment(emp, NONE, '2026-06-15'), true)
  assert.equal(isWithinEmployment(emp, NONE, '2026-06-16'), false)
})

test('принят десятого — десятое уже рабочий день', () => {
  const emp = { hire_date: '2026-06-10' }
  assert.equal(isWithinEmployment(emp, NONE, '2026-06-10'), true)
  assert.equal(isWithinEmployment(emp, NONE, '2026-06-09'), false)
})

// ── Пересечение ──────────────────────────────────────────────────────────────

test('даты человека — внешняя граница: увольнение закрывает позицию', () => {
  const emp = { dismissal_date: '2026-06-10' }
  const pos = { dismissal_date: '2026-06-25' }
  assert.deepEqual(employmentBounds(emp, pos), [null, '2026-06-10'])
  assert.equal(isWithinEmployment(emp, pos, '2026-06-20'), false)
})

test('позиция может закрыться раньше человека', () => {
  const emp = { hire_date: '2026-01-01' }
  const pos = { dismissal_date: '2026-06-10' }
  assert.deepEqual(employmentBounds(emp, pos), ['2026-01-01', '2026-06-10'])
})

test('из двух дат приёма берётся более поздняя', () => {
  const emp = { hire_date: '2026-01-01' }
  const pos = { hire_date: '2026-06-05' }
  assert.equal(employmentBounds(emp, pos)[0], '2026-06-05')
})

test('сравнение идёт по ISO-строкам, без разбора в Date и часовых поясов', () => {
  // Границы месяца и года — там, где Date с таймзоной уехал бы на сутки.
  const emp = { dismissal_date: '2026-12-31' }
  assert.equal(isWithinEmployment(emp, NONE, '2026-12-31'), true)
  assert.equal(isWithinEmployment(emp, NONE, '2027-01-01'), false)
  const src = read('./employment.ts')
  assert.ok(!/new Date\(/.test(src), 'разбирать даты в Date нельзя — уедет день границы')
})

// ── Совместитель ─────────────────────────────────────────────────────────────

test('совместитель: закрытая позиция блокируется, открытая — нет', () => {
  const emp = NONE
  const main = NONE
  const extra = { dismissal_date: '2026-06-15' }
  assert.equal(isWithinEmployment(emp, main, '2026-06-20'), true)
  assert.equal(isWithinEmployment(emp, extra, '2026-06-20'), false)
})

test('отсутствие ставится, пока открыто хотя бы одно рабочее место', () => {
  const closed = { dismissal_date: '2026-06-15' }
  assert.equal(isWithinAnyEmployment(NONE, [closed, NONE], '2026-06-20'), true)
  assert.equal(isWithinAnyEmployment(NONE, [closed], '2026-06-20'), false)
})

test('без позиций правило сводится к датам человека', () => {
  const emp = { dismissal_date: '2026-06-15' }
  assert.equal(isWithinAnyEmployment(emp, [], '2026-06-20'), false)
  assert.equal(isWithinAnyEmployment(emp, [], '2026-06-10'), true)
})

// ── Подсказка ────────────────────────────────────────────────────────────────

test('подсказка называет причину и дату по-русски', () => {
  assert.equal(
    employmentHint({ dismissal_date: '2026-06-15' }, NONE, '2026-06-20'),
    'Вне периода работы: уволен 15.06.2026',
  )
  assert.equal(
    employmentHint({ hire_date: '2026-06-10' }, NONE, '2026-06-05'),
    'Вне периода работы: принят 10.06.2026',
  )
})

test('isoDay собирает дату с ведущими нулями', () => {
  assert.equal(isoDay(2026, 6, 5), '2026-06-05')
  assert.equal(isoDay(2026, 12, 31), '2026-12-31')
})

// ── Сверка с бэкендом: правило продублировано намеренно ──────────────────────

test('бэкенд считает границы тем же пересечением', () => {
  const py = read('../../../backend/app/services/employment_period.py')
  assert.ok(/max\(starts\) if starts else None/.test(py), 'приём — более поздняя дата')
  assert.ok(/min\(ends\) if ends else None/.test(py), 'увольнение — более ранняя дата')
  // Границы включительные с обеих сторон.
  assert.ok(/work_date < start/.test(py))
  assert.ok(/work_date > end/.test(py))
})

test('бэкенд проверяет все четыре точки мутации, а не только автозаполнение', () => {
  const ts = read('../../../backend/app/services/timesheet.py')
  const abs = read('../../../backend/app/services/absences.py')
  const night = read('../../../backend/app/services/night_shifts.py')
  assert.ok(/check_employment_period\(/.test(ts), 'часы: ручной ввод')
  assert.ok(/is_within_employment\(emp, position, work_date\)/.test(ts), 'автозаполнение')
  assert.ok(/check_any_employment_period\(/.test(abs), 'коды отсутствия')
  assert.ok(/check_employment_period\(/.test(night), 'ночные смены')
})

test('вахта берёт то же правило, а не свою копию', () => {
  const guard = read('../../../backend/app/services/guard_duty.py')
  assert.ok(
    /from app\.services\.employment_period import is_within_employment/.test(guard),
    'вахта обязана спрашивать общий модуль правила',
  )
  // Своего сравнения дат в вахте быть не должно — это вторая копия правила.
  assert.ok(!/dismissal_date\s*(<|>)/.test(guard))
})

test('вахта: заполнение фильтруется молча, явный клик получает отказ', () => {
  const guard = read('../../../backend/app/services/guard_duty.py')
  assert.ok(/wanted = \{d for d in wanted if d in current or employment_allows/.test(guard),
    'set_days отбрасывает запрещённые дни, но сохраняет уже отмеченные')
  assert.ok(/if value and not employment_allows\(assignment, day\)/.test(guard),
    'toggle_day отказывает внятно')
})

test('снятие отметки бэкенд не блокирует — иначе часы за границей не убрать', () => {
  const ts = read('../../../backend/app/services/timesheet.py')
  assert.ok(
    /if hours != Decimal\("0"\):\s*\n\s*check_employment_period/.test(ts),
    'проверка стоит только на записи часов, не на удалении',
  )
})

// ── Основание клиентской части ───────────────────────────────────────────────

test('табель спрашивает правило, а не считает границы на месте', () => {
  const page = read('../pages/TimesheetPage.tsx')
  assert.ok(/from '\.\.\/utils\/employment'/.test(page), 'правило берётся из utils/employment')
  assert.ok(/isWithinEmployment|employmentHint/.test(page))
  // Своего сравнения дат на странице быть не должно — это вторая копия правила.
  assert.ok(
    !/dismissal_date\s*(<|>)/.test(page),
    'сравнивать даты увольнения на странице нельзя — только через utils/employment',
  )
})

// ── Подтверждение очистки: «Нет» — это отмена, а не ошибка ───────────────────

const clearing409 = () =>
  Object.assign(new Error('{"error":"employment_period_clearing_required"}'), {
    detail: { error: 'employment_period_clearing_required', days: 10, hours: '80', amount: '38636' },
  })

test('«Нет» возвращает отмену, а НЕ пробрасывает 409 (баг: красный тост с JSON)', async () => {
  const calls: boolean[] = []
  const res = await withClearingConfirm(async (confirm) => {
    calls.push(confirm)
    throw clearing409()
  }, () => false)
  assert.equal(res, CLEARING_CANCELLED)
  assert.deepEqual(calls, [false], 'после отказа повторного запроса с confirm быть не должно')
})

test('«Да» повторяет запрос с confirm=true и отдаёт его результат', async () => {
  const calls: boolean[] = []
  const res = await withClearingConfirm(async (confirm) => {
    calls.push(confirm)
    if (!confirm) throw clearing409()
    return 'ok'
  }, () => true)
  assert.equal(res, 'ok')
  assert.deepEqual(calls, [false, true])
})

test('вопрос показывает числа: дни, часы, сумму', async () => {
  let shown = ''
  await withClearingConfirm(async () => { throw clearing409() }, (m) => { shown = m; return false })
  assert.match(shown, /10 дней/)
  assert.match(shown, /80 ч/)
  assert.match(shown, /38\s?636 ₽/)
})

test('без очистки запрос проходит с первого раза и вопроса нет', async () => {
  let asked = false
  const res = await withClearingConfirm(async () => 'ok', () => { asked = true; return true })
  assert.equal(res, 'ok')
  assert.equal(asked, false)
})

test('прочие ошибки пробрасываются как есть', async () => {
  const other = Object.assign(new Error('Сотрудник уже уволен'), { detail: 'Сотрудник уже уволен' })
  await assert.rejects(
    withClearingConfirm(async () => { throw other }, () => true),
    (e) => e === other,
  )
})

test('экраны не зовут window.confirm сами и не пробрасывают 409 после отказа', () => {
  for (const f of ['../pages/admin/EmployeesPage.tsx', '../pages/admin/PositionsEditor.tsx']) {
    const src = read(f)
    assert.ok(/withClearingConfirm\(/.test(src), `${f}: сохранение идёт через withClearingConfirm`)
    assert.ok(!/window\.confirm\(ask\)\) throw e/.test(src), `${f}: старый шаблон «отказ = throw» убран`)
    assert.ok(/CLEARING_CANCELLED/.test(src), `${f}: отмена обрабатывается явно`)
  }
})
