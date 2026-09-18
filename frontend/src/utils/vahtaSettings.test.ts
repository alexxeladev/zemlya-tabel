/**
 * Адреса настроек вахты (task_vahta_settings_staff).
 *
 * Запуск:  cd frontend && npm test
 *
 * Проверки — по требованию заказчика, а не по реализации: вкладка отражается в
 * адресе и открывается ссылкой, старые адреса ведут во вкладки, ссылка из
 * карточки сотрудника открывает нужное рабочее место, «Состава» нет.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  LEGACY_VAHTA_SETTINGS_REDIRECTS,
  MONTH_DEPENDENT_TABS,
  VAHTA_SETTINGS_TABS,
  VAHTA_SETTINGS_TAB_LABELS,
  formatMonthParam,
  parseMonthParam,
  parseSettingsTab,
  vahtaSettingsPath,
} from './vahtaSettings.ts'
import { GUARD_STAFF_PATH, guardStaffPositionPath } from './guardStaff.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(resolve(here, rel), 'utf8')

test('вкладки — по частоте: сотрудники, зоны, должности, налог; «Состава» нет', () => {
  assert.deepEqual([...VAHTA_SETTINGS_TABS], ['staff', 'zones', 'titles', 'tax'])
  assert.equal(VAHTA_SETTINGS_TAB_LABELS.staff, 'Сотрудники охраны')
  assert.ok(!Object.values(VAHTA_SETTINGS_TAB_LABELS).includes('Состав'))
})

test('вкладка из адреса: известная — она, чужая — null', () => {
  assert.equal(parseSettingsTab('staff'), 'staff')
  assert.equal(parseSettingsTab('tax'), 'tax')
  assert.equal(parseSettingsTab('roster'), null)
  assert.equal(parseSettingsTab(undefined), null)
})

test('месяц в адресе — только у вкладок, которые от него зависят', () => {
  assert.deepEqual([...MONTH_DEPENDENT_TABS], ['staff', 'zones'])
  assert.equal(vahtaSettingsPath('staff', { year: 2026, month: 8 }), '/vahta/settings/staff?month=2026-08')
  assert.equal(vahtaSettingsPath('zones', { year: 2026, month: 8 }), '/vahta/settings/zones?month=2026-08')
  assert.equal(vahtaSettingsPath('titles', { year: 2026, month: 8 }), '/vahta/settings/titles')
  assert.equal(vahtaSettingsPath('tax', { year: 2026, month: 8 }), '/vahta/settings/tax')
})

test('разбор месяца: YYYY-MM, остальное отвергается', () => {
  assert.deepEqual(parseMonthParam('2026-08'), { year: 2026, month: 8 })
  assert.equal(parseMonthParam('2026-13'), null)
  assert.equal(parseMonthParam('август'), null)
  assert.equal(parseMonthParam(null), null)
  assert.equal(formatMonthParam(2026, 8), '2026-08')
})

test('открытое рабочее место — в адресе вкладки сотрудников', () => {
  assert.equal(
    vahtaSettingsPath('staff', { year: 2026, month: 8, positionId: 1812 }),
    '/vahta/settings/staff?month=2026-08&position_id=1812',
  )
  // У других вкладок рабочего места нет — параметр не протекает.
  assert.equal(vahtaSettingsPath('zones', { positionId: 1812 }), '/vahta/settings/zones')
})

test('ссылка «ведётся в модуле „Вахта“» ведёт во вкладку и на само рабочее место', () => {
  assert.equal(GUARD_STAFF_PATH, '/vahta/settings/staff')
  assert.equal(guardStaffPositionPath(1812), '/vahta/settings/staff?position_id=1812')
})

test('старые адреса ведут во вкладки: сотрудники — в сотрудников, посты — в зоны', () => {
  assert.equal(LEGACY_VAHTA_SETTINGS_REDIRECTS['/vahta/staff'], 'staff')
  assert.equal(LEGACY_VAHTA_SETTINGS_REDIRECTS['/vahta/posts'], 'zones')
  const router = read('../routes/AppRouter.tsx')
  // Старые пути по-прежнему зарегистрированы — иначе закладка пользователя упала бы на дашборд.
  for (const path of Object.keys(LEGACY_VAHTA_SETTINGS_REDIRECTS)) {
    assert.match(router, new RegExp(`LEGACY_VAHTA_SETTINGS_REDIRECTS|path="${path}"`))
  }
  assert.doesNotMatch(router, /VahtaStaffPage/)
})

test('входов в сотрудников охраны нет ни на панели табеля, ни в шапке настроек', () => {
  const sheet = read('../pages/VahtaPage.tsx')
  assert.doesNotMatch(sheet, /to="\/vahta\/staff"/)
  assert.doesNotMatch(sheet, />\s*Сотрудники\s*</)
  const settings = read('../pages/admin/VahtaSettingsPage.tsx')
  assert.doesNotMatch(settings, /to="\/vahta\/staff"/)
  assert.doesNotMatch(settings, /CrewRoster/)
})

test('права вкладок те же, что у прежних страниц: admin и manager', () => {
  const router = read('../routes/AppRouter.tsx')
  const block = router.slice(router.indexOf('/vahta/settings'))
  assert.match(block.slice(0, 400), /RoleRoute allow=\{\['admin', 'manager'\]\}/)
})

test('карточка сотрудника: ссылка на само рабочее место, бухгалтеру — текст', async () => {
  const { canOpenGuardStaff } = await import('./guardStaff.ts')
  assert.equal(canOpenGuardStaff('admin'), true)
  assert.equal(canOpenGuardStaff('manager'), true)
  assert.equal(canOpenGuardStaff('accountant'), false)
  assert.equal(canOpenGuardStaff('timekeeper'), false)
  const editor = read('../pages/admin/PositionsEditor.tsx')
  assert.match(editor, /to=\{guardStaffPositionPath\(p\.id\)\}/)
  assert.match(editor, /canOpenGuardStaff\(role\)/)
})
