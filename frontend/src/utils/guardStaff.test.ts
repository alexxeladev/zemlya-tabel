/**
 * Штат охраны на фронте (task_guard_ownership).
 *
 * Запуск:  cd frontend && npm test
 *
 * Решает владение бэк; здесь проверяется отражение на экране: подпись суммы
 * совпадает с типом оплаты бэка, подтверждения перехода из охраны называют
 * причины, а карточка сотрудника действительно прячет правку охранных мест.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  GUARD_CONFIRM_CANCELLED,
  GUARD_KIND_OPTIONS,
  guardAmountLabel,
  guardConfirmMessage,
  isGuardPosition,
  withGuardConfirm,
} from './guardStaff.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p: string) => readFileSync(resolve(here, p), 'utf8')

test('оклад только у начальника охраны — как pay_type_for_kind на бэке', () => {
  assert.equal(guardAmountLabel('chief'), 'Оклад за месяц, ₽')
  for (const kind of ['guard', 'gbr', 'dispatcher'] as const) {
    assert.equal(guardAmountLabel(kind), 'Ставка за смену, ₽')
  }
  const backend = read('../../../backend/app/models/guard_posts.py')
  assert.match(backend, /PAY_TYPE_SALARY if kind == GUARD_KIND_CHIEF else PAY_TYPE_PER_SHIFT/)
})

test('должности формы совпадают с должностями бэка', () => {
  const backend = read('../../../backend/app/models/guard_posts.py')
  for (const { value, label } of GUARD_KIND_OPTIONS) {
    assert.match(backend, new RegExp(`= "${value}"`))
    assert.ok(backend.includes(`"${label}"`), label)
  }
})

test('охранная позиция — по флагу отдела, пришедшему с бэка', () => {
  assert.equal(isGuardPosition({ department: { is_guard_department: true } as never }), true)
  assert.equal(isGuardPosition({ department: { is_guard_department: false } as never }), false)
  assert.equal(isGuardPosition({ department: null }), false)
})

test('перевод из охраны: причины теми же словами, что прислал бэк', () => {
  const msg = guardConfirmMessage({
    error: 'guard_transfer_out_confirmation_required',
    message: 'Рабочее место переходит в «ИТО»',
    issues: ['Не задан график', 'Не задана ставка за смену'],
  })
  assert.ok(msg)
  assert.match(msg!, /не войдёт в расчёт/)
  assert.match(msg!, /• Не задан график/)
  assert.match(msg!, /• Не задана ставка за смену/)
})

test('снятие флага охраны: число мест и сколько не войдут в расчёт', () => {
  const msg = guardConfirmMessage({
    error: 'guard_flag_removal_confirmation_required',
    message: '3 рабочих мест перестанут вестись в модуле «Вахта»',
    position_count: 3,
    not_calculable_count: 2,
    issues: ['Не задан график'],
  })
  assert.match(msg!, /3 рабочих мест/)
  assert.match(msg!, /не войдут в расчёт: 2/)
})

test('чужие 409 не перехватываются', () => {
  assert.equal(guardConfirmMessage({ error: 'employment_period_clearing_required' }), null)
  assert.equal(guardConfirmMessage('строка'), null)
})

test('withGuardConfirm: да — повтор с confirm, нет — отмена без ошибки', async () => {
  const conflict = Object.assign(new Error('409'), {
    detail: { error: 'guard_transfer_out_confirmation_required', issues: [] },
  })
  const calls: boolean[] = []
  const run = async (confirm: boolean) => {
    calls.push(confirm)
    if (!confirm) throw conflict
    return 'ok'
  }
  assert.equal(await withGuardConfirm(run, () => true), 'ok')
  assert.deepEqual(calls, [false, true])
  assert.equal(await withGuardConfirm(run, () => false), GUARD_CONFIRM_CANCELLED)
  await assert.rejects(
    withGuardConfirm(async () => { throw new Error('boom') }, () => true), /boom/,
  )
})

test('карточка: у охранной позиции нет «Изменить»/«Удалить», новая — без отделов охраны', () => {
  const editor = read('../pages/admin/PositionsEditor.tsx')
  assert.match(editor, /!isGuardPosition\(p\) && \(\s*<button[\s\S]*?Изменить/)
  assert.match(editor, /activeCount > 1 && !isGuardPosition\(p\)/)
  assert.match(editor, /departments\.filter\(\(d\) => !isGuardDepartment\(d\)\)/)
  const page = read('../pages/admin/EmployeesPage.tsx')
  assert.match(page, /departments\?\.filter\(\(d\) => !isGuardDepartment\(d\)\)/)
  assert.match(page, /filter\(\(p\) => !isGuardPosition\(p\)\)/)
})

test('экран вахты не заводит своей проверки флага охраны', () => {
  const staff = read('../pages/admin/VahtaStaffPage.tsx')
  // Отделы охраны приходят с бэка списком; флаг читается только для обычных
  // отделов перевода — отдельного правила «охранное или нет» нет.
  assert.doesNotMatch(staff, /pay_type_for_kind|kind === 'chief'/)
  // Флаг отдела читается только через isGuardDepartment — не по месту.
  for (const file of ['../pages/admin/VahtaStaffPage.tsx', '../pages/admin/PositionsEditor.tsx', '../pages/admin/EmployeesPage.tsx']) {
    assert.doesNotMatch(read(file), /\.is_guard_department/, file)
  }
})

// ── Начисления на охранной позиции (аудит 2-Г) ────────────────────────────────

test('общий ввод на охранное место закрыт одним предикатом — в табеле и в карточке', () => {
  const page = read('../pages/TimesheetPage.tsx')
  // Часы: ячейка дня получает признак от того же isGuardPosition, своего правила нет.
  assert.match(page, /guardLocked=\{isGuardPosition\(position\)\}/)
  // Премии/KPI/аванс/правка займа: окно открывается только на удаление.
  assert.match(page, /const guardLocked = isGuardPosition\(position\)/)
  assert.match(page, /disabled=\{busy \|\| guardLocked\}/)
  // Введённое до запрета остаётся снимаемым: крестик чипа не гасится.
  assert.match(page, /removeOnly=\{guardLocked\}/)
  assert.doesNotMatch(page, /\.is_guard_department/, 'флаг отдела читается только через utils/guardStaff')
  // Заём в карточке: по основной позиции, с возможностью снять.
  const card = read('../pages/admin/EmployeesPage.tsx')
  assert.match(card, /const loanLocked = Boolean\(primary && isGuardPosition\(primary\)\)/)
  assert.match(card, /Снять заём/)
})

test('бэкенд: один предикат запрета на все точки входа, снятие не блокируется', () => {
  const staff = read('../../../backend/app/services/guard_staff.py')
  assert.match(staff, /def ensure_no_guard_accrual\(/)
  const ts = read('../../../backend/app/services/timesheet.py')
  // Часы проверяются только при записи (hours != 0) — удаление проходит.
  assert.match(ts, /if hours != Decimal\("0"\):\s*\n\s*check_employment_period[^\n]*\n\s*_ensure_hours_allowed/)
  const router = read('../../../backend/app/routers/timesheet.py')
  assert.equal((router.match(/ensure_no_guard_accrual\(/g) ?? []).length, 2, 'премии и ручная правка займа')
  assert.match(read('../../../backend/app/routers/employees.py'), /ensure_loan_change_allowed\(db, emp, data\)/)
})
