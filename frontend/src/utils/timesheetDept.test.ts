/**
 * Табель открывается только по ОДНОМУ отделу (task_timesheet_dept_only).
 *
 * Запуск:  cd frontend && npm test
 *
 * Проверка построена ПО ТРЕБОВАНИЮ, а не по диффу. Требование состояло из
 * шести пунктов, и на каждый здесь свой тест:
 *   1. режима «все отделы» нет в выборе отдела;
 *   2. без выбранного отдела показывается экран выбора, а не загрузка всего;
 *   3. сохранённое `'all'` не ломает экран, а сбрасывается на выбор;
 *   4. ссылка `?department_id=` с несуществующим отделом ведёт на выбор;
 *   5. переходы с дашборда и из «Задач» несут отдел всегда;
 *   6. ролям с ОДНИМ отделом экран выбора не показывается.
 *
 * Тексты экранов читаются из исходников: чистой функции мало — «убрать режим»
 * значит убрать его из ВСЕХ мест, где он предлагался, а их три (экран выбора,
 * выпадашка в шапке, сброс устаревшего выбора).
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { isTimesheetDeptChoice, departmentChoiceIsStale } from './departments.ts'

const here = dirname(fileURLToPath(import.meta.url))
const source = (rel: string) => readFileSync(resolve(here, '..', rel), 'utf8')

/** Без комментариев: в них история правки упоминает снятый режим по имени,
 *  и сторож «режима больше нет» спотыкался бы о собственное объяснение. */
const code = (rel: string) =>
  source(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

// ── 3. Сохранённый выбор ──────────────────────────────────────────────────────

test('сохранённое «все отделы» не принимается — экран уходит на выбор', () => {
  // Ключ хранилища общий со старой версией экрана, значение осталось в
  // браузерах у людей. Отвергнутое значение loadValidated превращает в null,
  // то есть в «отдел не выбран».
  assert.equal(isTimesheetDeptChoice('all'), false)
})

test('годные значения выбора: отдел, группа «Без отдела», «не выбран»', () => {
  assert.equal(isTimesheetDeptChoice(7), true)
  assert.equal(isTimesheetDeptChoice('none'), true)
  assert.equal(isTimesheetDeptChoice(null), true)
})

test('прочий мусор в хранилище тоже отвергается', () => {
  for (const bad of ['', 'ИТО', {}, [], NaN, undefined, true]) {
    assert.equal(isTimesheetDeptChoice(bad), false, String(bad))
  }
})

test('стор табеля проверяет сохранённое значение именно этим правилом', () => {
  const src = code('store/timesheetView.ts')
  assert.ok(
    src.includes('isTimesheetDeptChoice'),
    'store/timesheetView: валидатор не общий — старое «all» снова может пролезть',
  )
  assert.ok(!src.includes("'all'"), 'store/timesheetView: осталось рабочее значение «all»')
})

// ── 1. Режима «все отделы» нет в местах выбора ───────────────────────────────

test('экран выбора отдела не предлагает «все отделы»', () => {
  const src = code('pages/TimesheetPage.tsx')
  const gate = src.slice(src.indexOf('function DepartmentGate'), src.indexOf('function daysInMonth'))
  assert.ok(gate.length > 0, 'DepartmentGate не найден')
  assert.ok(!gate.includes('Все отделы'), 'на экране выбора осталась кнопка «Все отделы»')
  assert.ok(!gate.includes('Все мои отделы'), 'на экране выбора осталось «Все мои отделы»')
  assert.ok(gate.includes("onPick('none')"), 'пропал пункт «Без отдела» — группа станет недостижимой')
})

test('выпадашка отдела в шапке не предлагает «все отделы»', () => {
  const src = code('pages/TimesheetPage.tsx')
  assert.ok(!src.includes('value="all"'), 'в выпадашке остался пункт «Все отделы»')
  assert.ok(!src.includes("=== 'all'"), 'в табеле осталась ветка режима «все отделы»')
  assert.ok(!src.includes("setDeptChoice('all')"), 'куда-то ещё сбрасываем выбор во «все отделы»')
})

test('группировка по отделам убрана вместе с режимом', () => {
  // Она существовала ТОЛЬКО ради «всех отделов» и была самой тяжёлой частью
  // разметки: заголовок-разделитель на каждый отдел плюс его строки.
  const src = code('pages/TimesheetPage.tsx')
  assert.ok(!src.includes('renderGroupDivider'), 'осталась отрисовка заголовков отделов')
  assert.ok(!src.includes('const grouped'), 'остался признак группировки')
})

// ── 2 и 6. Экран выбора: кому показывается, кому нет ─────────────────────────

test('экран выбора показывается только тем, у кого есть выбор', () => {
  const code = source('pages/TimesheetPage.tsx')
  assert.match(
    code,
    /const needsDeptChoice = canSelectDept && \w+ === null/,
    'условие показа экрана выбора изменилось — проверьте роли с одним отделом',
  )
  // canSelectDept: admin/accountant всегда, руководитель и табельщик — только
  // если отделов больше одного. С одним отделом выбирать нечего.
  assert.ok(
    code.includes('managedDeptCount > 1'),
    'роль с ОДНИМ отделом снова попадает на экран выбора',
  )
})

test('роль без выбора отдела шлёт ПУСТОЙ фильтр — набор отделов знает сервер', () => {
  // Соблазн подставить номер отдела из профиля был, и он стоил бы 403:
  // профиль во фронте кэширован, после перевода человека в другой отдел он
  // устаревает, и каждый запрос уходил бы за недоступный отдел. Подпись Т-13
  // («Все отделы» при одном отделе в файле) чинится на бэке, по содержимому.
  const src = code('pages/TimesheetPage.tsx')
  assert.ok(
    !src.includes('managed_department_ids?.[0]'),
    'номер отдела снова берётся из кэшированного профиля — это 403 после перевода',
  )
  assert.match(
    src,
    /const departmentFilter[^=]*=\s*canSelectDept\s*\?[\s\S]{0,120}?:\s*null;/,
    'у роли без выбора отдела фильтр перестал быть пустым',
  )
})

test('экран выбора различает «не загрузилось» и «отделов нет»', () => {
  // Пустой список из-за сбоя запроса давал тупик с неверным текстом
  // «отделы не заведены» и без способа повторить.
  const src = code('pages/TimesheetPage.tsx')
  assert.ok(src.includes("deptsState"), 'состояние загрузки справочника отделов пропало')
  assert.ok(src.includes('Не удалось загрузить список отделов'), 'пропал текст ошибки загрузки')
  assert.ok(src.includes('onRetryDepartments'), 'с экрана выбора нельзя повторить загрузку')
})

test('пункт «Без отдела» не предлагается роли, ограниченной отделами', () => {
  // У группы отдела нет, а доступ у них выдан ПО ОТДЕЛАМ: бэк ответит 403.
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(
    code.includes('withoutDepartment={!isDeptScoped}'),
    'экран выбора предлагает «Без отдела» менеджеру/табельщику — это 403',
  )
  assert.ok(
    code.includes('{!isDeptScoped && <option value="none">'),
    'выпадашка предлагает «Без отдела» менеджеру/табельщику — это 403',
  )
})

test('выпадашка отдела состоит из отделов и «Без отдела» — больше ни из чего', () => {
  const code = source('pages/TimesheetPage.tsx')
  const select = code.slice(
    code.indexOf('{canSelectDept && departments.length > 0 && ('),
    code.indexOf('{payrollStale && ('),
  )
  assert.ok(select.length > 0, 'выпадашка отдела не найдена')
  const options = [...select.matchAll(/<option[^>]*value=\{?"?([^"}>\s]*)/g)].map((m) => m[1])
  assert.deepEqual(
    options.filter((v) => v !== 'd.id' && !v.startsWith('{')),
    ['none'],
    `в выпадашке появились лишние пункты: ${options.join(', ')}`,
  )
})

test('на экране выбора есть выход из фильтра юрлица', () => {
  // Юрлицо без отделов давало тупик: кнопок нет, шапки с фильтром нет,
  // а прежним выходом была снятая кнопка «Все отделы».
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(code.includes('onClearCompany'), 'из экрана выбора нельзя снять фильтр юрлица')
  assert.ok(code.includes('Показать все юрлица'), 'пропала кнопка снятия фильтра юрлица')
})

test('табель не грузится, пока отдел не выбран', () => {
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(code.includes('if (!deptChosen)'), 'загрузка месяца больше не ждёт выбора отдела')
})

// ── 4. Ссылки с отделом ──────────────────────────────────────────────────────

test('несуществующий отдел из ссылки сбрасывается на экран выбора', () => {
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(
    code.includes("!departments.some((d) => d.id === deptChoice)")
    && code.includes('setDeptChoice(null)'),
    'выбор отдела, которого нет в справочнике, больше не сбрасывается',
  )
})

test('сохранённое «Без отдела» у роли по отделам читается как «не выбрано»', () => {
  // И читается СИНХРОННО, а не эффектом: из эффекта первый запрос успевал
  // уйти и вернуть 403 с тостом «ошибка загрузки», хотя ошибки нет.
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(
    code.includes("const groupDenied = deptChoice === 'none' && isDeptScoped"),
    'ссылка ?department_id=none уведёт роль по отделам в 403 вместо экрана выбора',
  )
  assert.ok(
    code.indexOf('const groupDenied') < code.indexOf('const needsDeptChoice'),
    'проверка «группа не для этой роли» съехала ниже условия показа экрана выбора',
  )
})

test('ссылка ?department_id=none открывает группу «Без отдела»', () => {
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(code.includes("raw === 'none'"), 'значение none в адресе не читается')
})

// ── 5. Переходы из других разделов ───────────────────────────────────────────

test('«Задачи» и дашборд всегда передают отдел', () => {
  // Без параметра человек попадал бы на экран выбора и не добрался бы до
  // периода, ради которого перешёл; у группы «Без отдела» это `none`.
  const tasks = source('pages/TasksPage.tsx')
  assert.ok(
    tasks.includes("params.set('department_id', t.department_id !== null ? String(t.department_id) : 'none')"),
    'TasksPage: переход без отдела снова возможен',
  )
  const dash = source('pages/DashboardPage.tsx')
  assert.ok(
    dash.includes("deptId !== null ? deptId : 'none'"),
    'DashboardPage: клик по «Без отдела» снова уводит в никуда',
  )
})

// ── Соседние экраны не задеты ────────────────────────────────────────────────

test('ведомость свой режим «все отделы» сохранила', () => {
  // Задача про табель: в «Расчёт ЗП» отбор отделов не менялся.
  const code = source('pages/admin/PayrollPage.tsx')
  assert.ok(code.includes('Все отделы'), 'из ведомости пропал выбор «Все отделы»')
})

test('сброс устаревшего выбора по юрлицу продолжает работать', () => {
  const depts = [
    { id: 1, head_company_id: 10 },
    { id: 2, head_company_id: 20 },
  ]
  assert.equal(departmentChoiceIsStale(depts, 20, 1), true)
  assert.equal(departmentChoiceIsStale(depts, 20, 2), false)
  // Группа «Без отдела» юрлицу не принадлежит и сбросу по юрлицу не подлежит:
  // сбрасывать нечего, отдела у неё нет.
  assert.equal(departmentChoiceIsStale(depts, 20, 'none'), false)
  assert.equal(departmentChoiceIsStale(depts, 20, null), false)
})
