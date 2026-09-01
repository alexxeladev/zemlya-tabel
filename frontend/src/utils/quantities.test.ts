/**
 * Условие показа блока количественного показателя (АРМ у ИТ, Заявки у HR)
 * над табелем.
 *
 * Запуск:  cd frontend && npm test
 * (node:test + нативный TypeScript в Node 24, дополнительных зависимостей нет)
 *
 * Проверка построена ПО ТРЕБОВАНИЮ, а не по диффу. Требование было такое:
 * блок виден, только если в ТЕКУЩЕМ отфильтрованном представлении есть хотя бы
 * один отдел с флагом; показываются только таблицы присутствующих отделов
 * (виден ИТ — блока HR нет, и наоборот); учитываются ВСЕ активные фильтры —
 * компания, отдел и фильтры в заголовках колонок. Поэтому здесь не только
 * чистая функция, но и (а) прогон через тот же конвейер фильтров, что у
 * страницы, и (б) сверка, что страница отдаёт в блок именно отфильтрованный
 * список, а компонент на пустом списке ничего не рисует.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { quantitiesForVisibleDepartments } from './quantities.ts'
import { positionInCompany } from './departments.ts'

const ZMO = 1
const KSERVICE = 2

const IT_DEPT = 10
const HR_DEPT = 11
const SERVICE_DEPT = 20 // «Сервис Доп.Услуги Собственникам», флага нет

/** Что отдаёт бэк: показатель по ВСЕМ отделам с флагом области видимости. */
const QUANTITIES = [
  { department_id: IT_DEPT, department_name: 'ИТ', metric_name: 'АРМ' },
  { department_id: HR_DEPT, department_name: 'HR', metric_name: 'Заявки' },
]

const metrics = (list: { metric_name: string }[]) => list.map((q) => q.metric_name)

// ── Само правило ────────────────────────────────────────────────────────────

test('ни одного отдела с показателем в выборке — блока нет вовсе', () => {
  assert.deepEqual(quantitiesForVisibleDepartments(QUANTITIES, [SERVICE_DEPT]), [])
})

test('виден только ИТ — таблица HR не показывается', () => {
  assert.deepEqual(metrics(quantitiesForVisibleDepartments(QUANTITIES, [IT_DEPT])), ['АРМ'])
})

test('виден только HR — таблица ИТ не показывается', () => {
  assert.deepEqual(metrics(quantitiesForVisibleDepartments(QUANTITIES, [HR_DEPT])), ['Заявки'])
})

test('видны оба отдела — обе таблицы', () => {
  assert.deepEqual(
    metrics(quantitiesForVisibleDepartments(QUANTITIES, [IT_DEPT, SERVICE_DEPT, HR_DEPT])),
    ['АРМ', 'Заявки'],
  )
})

test('порядок таблиц — как пришёл с бэка, а не как встретились строки', () => {
  assert.deepEqual(
    metrics(quantitiesForVisibleDepartments(QUANTITIES, [HR_DEPT, IT_DEPT])),
    ['АРМ', 'Заявки'],
  )
})

test('повторы отделов (несколько строк одного отдела) ничего не ломают', () => {
  assert.deepEqual(
    metrics(quantitiesForVisibleDepartments(QUANTITIES, [IT_DEPT, IT_DEPT, IT_DEPT])),
    ['АРМ'],
  )
})

test('строки без отдела («Без отдела») показателя не вытаскивают', () => {
  assert.deepEqual(quantitiesForVisibleDepartments(QUANTITIES, [null, undefined]), [])
})

test('пустая выборка строк — пустой список', () => {
  assert.deepEqual(quantitiesForVisibleDepartments(QUANTITIES, []), [])
})

test('показателей нет вовсе — блока нет при любых строках', () => {
  assert.deepEqual(quantitiesForVisibleDepartments([], [IT_DEPT, HR_DEPT]), [])
})

// ── Через тот же конвейер фильтров, что у табеля ────────────────────────────
// Строка табеля = рабочее место; фильтр компании отбирает по КАРТОЧКЕ
// (головная компания отдела), фильтры колонок — по значениям колонок.

type Row = {
  position: {
    department_id: number | null
    department?: { name: string; head_company_id?: number | null } | null
    company_id: number | null
    title: string
  }
}

const row = (
  departmentId: number | null,
  departmentName: string,
  headCompanyId: number | null,
  title: string,
): Row => ({
  position: {
    department_id: departmentId,
    department: departmentId === null ? null : { name: departmentName, head_company_id: headCompanyId },
    company_id: ZMO,
    title,
  },
})

/** Табель как на скриншоте: ИТ и HR — «Земля МО», сервисный отдел — «К-Сервис». */
const ROWS: Row[] = [
  row(IT_DEPT, 'ИТ', ZMO, 'Системный администратор'),
  row(HR_DEPT, 'HR', ZMO, 'Рекрутёр'),
  row(SERVICE_DEPT, 'Сервис Доп.Услуги Собственникам', KSERVICE, 'Мастер'),
  row(SERVICE_DEPT, 'Сервис Доп.Услуги Собственникам', KSERVICE, 'Диспетчер'),
]

/** Повторяет отбор строк страницы: фильтр компании + фильтры колонок. */
function shownRows(
  companyFilter: number | null,
  columnFilters: { department?: string[]; title?: string[] } = {},
): Row[] {
  return ROWS.filter((r) => {
    if (!positionInCompany(r.position, companyFilter)) return false
    const dept = r.position.department?.name ?? ''
    if (columnFilters.department && !columnFilters.department.includes(dept)) return false
    if (columnFilters.title && !columnFilters.title.includes(r.position.title)) return false
    return true
  })
}

const panelFor = (rows: Row[]) =>
  metrics(quantitiesForVisibleDepartments(QUANTITIES, rows.map((r) => r.position.department_id)))

test('баг со скриншота: компания «К-Сервис» + отдел «Сервис Доп.Услуги» — блока нет', () => {
  const rows = shownRows(KSERVICE, { department: ['Сервис Доп.Услуги Собственникам'] })
  assert.equal(rows.length, 2)
  assert.deepEqual(panelFor(rows), [])
})

test('фильтр только по компании: «К-Сервис» уносит и ИТ, и HR', () => {
  assert.deepEqual(panelFor(shownRows(KSERVICE)), [])
})

test('фильтр только по компании: «Земля МО» оставляет обе таблицы', () => {
  assert.deepEqual(panelFor(shownRows(ZMO)), ['АРМ', 'Заявки'])
})

test('фильтр колонки «Отдел» = ИТ (без фильтра компании) — только таблица ИТ', () => {
  assert.deepEqual(panelFor(shownRows(null, { department: ['ИТ'] })), ['АРМ'])
})

test('фильтр колонки «Должность» = Рекрутёр — только таблица HR', () => {
  assert.deepEqual(panelFor(shownRows(null, { title: ['Рекрутёр'] })), ['Заявки'])
})

test('фильтры сужают вместе: «Земля МО» + должность «Мастер» — пусто, блока нет', () => {
  const rows = shownRows(ZMO, { title: ['Мастер'] })
  assert.equal(rows.length, 0)
  assert.deepEqual(panelFor(rows), [])
})

test('без фильтров — обе таблицы, как и раньше', () => {
  assert.deepEqual(panelFor(shownRows(null)), ['АРМ', 'Заявки'])
})

// ── Основание: страница и компонент действительно это применяют ─────────────
// Мало проверить чистую функцию: если страница по-прежнему отдаёт в блок
// `data.quantities`, тест выше был бы зелёным, а баг остался бы на экране.

const here = dirname(fileURLToPath(import.meta.url))
const src = (rel: string) => readFileSync(resolve(here, rel), 'utf8')

test('TimesheetPage отдаёт в блок ОТФИЛЬТРОВАННЫЙ список, а не data.quantities', () => {
  const page = src('../pages/TimesheetPage.tsx')
  assert.match(page, /quantitiesForVisibleDepartments\(/)
  assert.match(page, /quantities=\{shownQuantities\}/)
  assert.doesNotMatch(page, /quantities=\{data\.quantities/)
})

test('отбор строится от shownRows — тех же строк, что рисует таблица', () => {
  const page = src('../pages/TimesheetPage.tsx')
  const call = page.match(/quantitiesForVisibleDepartments\([\s\S]{0,200}?\)\s*,/)
  assert.ok(call, 'вызов отбора не найден')
  assert.match(call[0], /shownRows\.map\(\(r\) => r\.position\.department_id\)/)
})

test('пустой список — компонент не рисует ничего', () => {
  const panel = src('../components/QuantityPanel.tsx')
  assert.match(panel, /if \(quantities\.length === 0\) return null/)
})

// ── Колонки «Распределение» в хвосте строк — ТО ЖЕ условие ──────────────────
// Блок ввода и колонки обязаны появляться и исчезать вместе: разное условие
// оставило бы на экране либо блок без колонок, либо столбцы прочерков без
// блока. Общий источник — `shownQuantities`; своё у колонок только «видит
// деньги» и «показатель за месяц заведён» (блок ввода, наоборот, нужен и на
// пустом показателе — иначе цифры некуда вводить).

/** Условие колонок ровно как на странице: canSeeMoney && shownQuantities. */
const columnsOn = (
  canSeeMoney: boolean,
  shownQuantities: { is_empty: boolean }[],
): boolean => canSeeMoney && shownQuantities.some((q) => !q.is_empty)

const filled = (...ids: number[]) =>
  ids.map((department_id) => ({ department_id, is_empty: false }))

test('нет отдела с показателем в выборке — колонок нет (а не прочерки)', () => {
  const shown = quantitiesForVisibleDepartments(filled(IT_DEPT, HR_DEPT), [SERVICE_DEPT])
  assert.deepEqual(shown, [])
  assert.equal(columnsOn(true, shown), false)
})

test('баг со скриншота: «К-Сервис» + сервисный отдел — ни блока, ни колонок', () => {
  const rows = shownRows(KSERVICE, { department: ['Сервис Доп.Услуги Собственникам'] })
  const shown = quantitiesForVisibleDepartments(
    filled(IT_DEPT, HR_DEPT),
    rows.map((r) => r.position.department_id),
  )
  assert.deepEqual(shown, [])
  assert.equal(columnsOn(true, shown), false)
})

test('отдел с показателем в выборке — колонки есть', () => {
  const shown = quantitiesForVisibleDepartments(filled(IT_DEPT, HR_DEPT), [IT_DEPT])
  assert.equal(shown.length, 1)
  assert.equal(columnsOn(true, shown), true)
})

test('блок и колонки не разъезжаются: на каждой выборке решает один список', () => {
  const cases: (number | null)[][] = [
    [],
    [SERVICE_DEPT],
    [IT_DEPT],
    [HR_DEPT],
    [IT_DEPT, HR_DEPT],
    [SERVICE_DEPT, HR_DEPT],
    [null],
  ]
  for (const visible of cases) {
    const shown = quantitiesForVisibleDepartments(filled(IT_DEPT, HR_DEPT), visible)
    // Блок рисуется при непустом списке, колонки — при нём же плюс деньги и
    // заведённый показатель. Значит «колонки без блока» невозможны никогда.
    const panelOn = shown.length > 0
    assert.equal(columnsOn(true, shown), panelOn, `выборка ${JSON.stringify(visible)}`)
  }
})

test('показатель за месяц не заведён — блок ввода есть, колонок нет', () => {
  const shown = quantitiesForVisibleDepartments(
    [{ department_id: IT_DEPT, is_empty: true }],
    [IT_DEPT],
  )
  assert.equal(shown.length, 1, 'блок нужен: без него негде ввести цифры')
  assert.equal(columnsOn(true, shown), false)
})

test('денег не видит (табельщик) — колонок нет даже при заведённом показателе', () => {
  const shown = quantitiesForVisibleDepartments(filled(IT_DEPT), [IT_DEPT])
  assert.equal(columnsOn(false, shown), false)
})

test('колонки берут ТОТ ЖЕ shownQuantities, а не свою копию правила', () => {
  const page = src('../pages/TimesheetPage.tsx')
  const line = page.match(/const distributionOn = .*/)
  assert.ok(line, 'условие колонок не найдено')
  assert.match(line[0], /shownQuantities/)
  // Второй копии отбора по отделам быть не должно: `data.quantities` в условии
  // видимости — это ровно тот баг, который чинили.
  assert.doesNotMatch(line[0], /data\.quantities/)
})

test('видимость колонок считается ровно в одном месте', () => {
  const page = src('../pages/TimesheetPage.tsx')
  assert.equal((page.match(/const distributionOn\b/g) ?? []).length, 1)
  assert.equal((page.match(/quantitiesForVisibleDepartments\(/g) ?? []).length, 1)
})

// ── Подпись блока: приоритет карточки (task_card_priority) ──────────────────

test('устаревшей подписи «обычный каскад не применяется» больше нет', () => {
  const panel = src('../components/QuantityPanel.tsx')
  assert.doesNotMatch(panel, /обычный каскад не применяется/)
})

test('подпись говорит про приоритет распределения из карточки', () => {
  const panel = src('../components/QuantityPanel.tsx')
  const caption = panel.match(/\{open \? \([\s\S]*?\) : \(/)
  assert.ok(caption, 'подпись развёрнутого блока не найдена')
  assert.match(caption[0], /карточке/)
  assert.match(caption[0], /приоритет/)
})
