/**
 * Фильтр отделов по выбранному юрлицу — табель и ведомость.
 *
 * Запуск:  cd frontend && npm test
 * (node:test + нативный TypeScript в Node 24, дополнительных зависимостей нет)
 *
 * Проверка построена ПО ТРЕБОВАНИЮ, а не по диффу: мало проверить чистую
 * функцию — надо ещё убедиться, что оба выпадающих списка отрисовывают именно
 * её результат. Ровно на этом уже обжигались: список СТРОК отфильтровали, а
 * выпадашка отделов осталась со всеми 20 отделами, и отчёт говорил «готово».
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  departmentsForCompany,
  departmentChoiceIsStale,
  workplaceCompanyId,
  positionInCompany,
  statementRowInCompany,
} from './departments.ts'

const ZMO = 1
const SEC = 2

/** Отделы как в жизни: у «Земли МО» два, у «Секьюрити» два, один без юрлица. */
const DEPARTMENTS = [
  { id: 10, name: 'Стройдепартамент', head_company_id: ZMO },
  { id: 11, name: 'Департамент маркетинга', head_company_id: ZMO },
  { id: 20, name: 'СБ Охрана', head_company_id: SEC },
  { id: 21, name: 'Секьюрити Отдел продаж', head_company_id: SEC },
  { id: 30, name: 'Без головной компании', head_company_id: null },
]

const names = (list: { name: string }[]) => list.map((d) => d.name)

// ── Сам отбор ───────────────────────────────────────────────────────────────

test('выбрана компания — в списке только её отделы', () => {
  assert.deepEqual(names(departmentsForCompany(DEPARTMENTS, ZMO)), [
    'Стройдепартамент',
    'Департамент маркетинга',
  ])
})

test('чужие отделы из списка уходят (баг: «СБ Охрана» при выбранной «Земле МО»)', () => {
  const shown = names(departmentsForCompany(DEPARTMENTS, ZMO))
  assert.ok(!shown.includes('СБ Охрана'))
  assert.ok(!shown.includes('Секьюрити Отдел продаж'))
})

test('«Все компании» — все отделы, включая отдел без головной компании', () => {
  assert.equal(departmentsForCompany(DEPARTMENTS, null).length, DEPARTMENTS.length)
  assert.equal(departmentsForCompany(DEPARTMENTS, undefined).length, DEPARTMENTS.length)
})

test('отдел без головной компании не приписывается ни одному юрлицу', () => {
  for (const company of [ZMO, SEC]) {
    assert.ok(!names(departmentsForCompany(DEPARTMENTS, company)).includes('Без головной компании'))
  }
})

test('юрлицо без единого отдела даёт пустой список, а не все отделы', () => {
  assert.deepEqual(departmentsForCompany(DEPARTMENTS, 999), [])
})

test('отбор идёт ТОЛЬКО по головной компании: часы и доли на него не влияют', () => {
  // Мультикомпанийность сотрудников — расчётная вещь; в справочных данных
  // отдела её нет вовсе, и попасть в фильтр ей неоткуда.
  const hoursOnEveryCompany = { id: 10, name: 'Стройдепартамент', head_company_id: ZMO }
  assert.deepEqual(departmentsForCompany([hoursOnEveryCompany], SEC), [])
  assert.deepEqual(departmentsForCompany([hoursOnEveryCompany], ZMO), [hoursOnEveryCompany])
})

// ── Сброс выбранного отдела ─────────────────────────────────────────────────

test('отдел чужого юрлица признаётся невалидным → сброс на «Все отделы»', () => {
  assert.equal(departmentChoiceIsStale(DEPARTMENTS, ZMO, 20), true)
})

test('отдел выбранного юрлица остаётся выбранным', () => {
  assert.equal(departmentChoiceIsStale(DEPARTMENTS, ZMO, 10), false)
})

test('без фильтра компании выбор отдела не сбрасывается никогда', () => {
  assert.equal(departmentChoiceIsStale(DEPARTMENTS, null, 20), false)
  assert.equal(departmentChoiceIsStale(DEPARTMENTS, undefined, 20), false)
})

test('«Все отделы» сбрасывать нечего', () => {
  assert.equal(departmentChoiceIsStale(DEPARTMENTS, ZMO, undefined), false)
  assert.equal(departmentChoiceIsStale(DEPARTMENTS, ZMO, null), false)
})

test('незагруженный справочник сбросом НЕ считается', () => {
  // Первый рендер: фильтры уже восстановлены из localStorage, отделы ещё летят
  // отдельным запросом. Иначе сохранённый отдел стирался бы при каждой
  // перезагрузке страницы с включённым фильтром компании.
  assert.equal(departmentChoiceIsStale([], ZMO, 10), false)
})

// ── Отбор СТРОК: юрлицо рабочего места ───────────────────────────────────────

/**
 * Одно и то же рабочее место в двух представлениях: строка табеля (позиция) и
 * строка ведомости. Пара нужна, чтобы сверять экраны между собой, а не каждый
 * с самим собой.
 */
function workplace(opts: {
  departmentId: number | null
  headCompanyId?: number | null
  positionCompanyId: number | null
}) {
  const { departmentId, headCompanyId = null, positionCompanyId } = opts
  return {
    position: {
      department_id: departmentId,
      department: departmentId == null ? null : { head_company_id: headCompanyId },
      company_id: positionCompanyId,
    },
    row: {
      department_id: departmentId,
      department_head_company_id: departmentId == null ? null : headCompanyId,
      main_company_id: positionCompanyId,
    },
  }
}

test('юрлицо места — головная компания его отдела', () => {
  assert.equal(
    workplaceCompanyId({ department_id: 10, department_head_company_id: ZMO, company_id: SEC }),
    ZMO,
  )
})

test('у места БЕЗ отдела юрлицо берётся из компании позиции', () => {
  assert.equal(
    workplaceCompanyId({ department_id: null, department_head_company_id: null, company_id: SEC }),
    SEC,
  )
})

test('компания позиции НЕ перебивает отдел', () => {
  // Основная компания карточки сплошь и рядом не совпадает с компанией отдела;
  // взятая первой, она втащила бы в выдачу отделы чужих юрлиц.
  const { position, row } = workplace({ departmentId: 10, headCompanyId: ZMO, positionCompanyId: SEC })
  assert.equal(positionInCompany(position, SEC), false)
  assert.equal(statementRowInCompany(row, SEC), false)
})

test('ТРЕБОВАНИЕ: разнесённые на юрлицо ЗАТРАТЫ не делают человека его сотрудником', () => {
  // Иванов числится в «Земле МО», 50% его затрат разнесено на «Комфорт».
  // Под фильтром «Комфорт» его в ведомости быть не должно: «Комфорт» ему
  // ничего не платит. Доли распределения в отборе не участвуют вовсе —
  // у правила нет к ним доступа даже теоретически.
  const ivanov = {
    department_id: 10,
    department_head_company_id: ZMO,
    main_company_id: ZMO,
    distribution: [
      { company_id: ZMO, percent: '50' },
      { company_id: SEC, percent: '50' },
    ],
  }
  assert.equal(statementRowInCompany(ivanov, SEC), false)
  assert.equal(statementRowInCompany(ivanov, ZMO), true)
})

test('«Все компании» — строка проходит любая', () => {
  const { position, row } = workplace({ departmentId: 10, headCompanyId: ZMO, positionCompanyId: ZMO })
  for (const nothing of [null, undefined]) {
    assert.equal(positionInCompany(position, nothing), true)
    assert.equal(statementRowInCompany(row, nothing), true)
  }
})

test('место в отделе без головной компании не попадает ни под один фильтр', () => {
  const { position, row } = workplace({ departmentId: 30, headCompanyId: null, positionCompanyId: ZMO })
  for (const company of [ZMO, SEC]) {
    assert.equal(positionInCompany(position, company), false)
    assert.equal(statementRowInCompany(row, company), false)
  }
})

// ── ТАБЕЛЬ И ВЕДОМОСТЬ ФИЛЬТРУЮТ ОДИНАКОВО ──────────────────────────────────

test('на одном рабочем месте табель и ведомость дают ОДИН ответ', () => {
  const cases = [
    { departmentId: 10, headCompanyId: ZMO, positionCompanyId: ZMO },   // обычный
    { departmentId: 10, headCompanyId: ZMO, positionCompanyId: SEC },   // карточка ≠ отдел
    { departmentId: 20, headCompanyId: SEC, positionCompanyId: ZMO },   // и наоборот
    { departmentId: 30, headCompanyId: null, positionCompanyId: ZMO },  // отдел без юрлица
    { departmentId: null, positionCompanyId: SEC },                     // место без отдела
    { departmentId: null, positionCompanyId: null },                    // ни того, ни другого
  ]
  let checked = 0
  for (const c of cases) {
    const { position, row } = workplace(c)
    for (const company of [ZMO, SEC, 999, null, undefined]) {
      assert.equal(
        positionInCompany(position, company),
        statementRowInCompany(row, company),
        `экраны разошлись: ${JSON.stringify(c)} под юрлицом ${company}`,
      )
      checked++
    }
  }
  assert.equal(checked, 30)
})

// ── Оба экрана действительно рисуют суженный список ──────────────────────────

const here = dirname(fileURLToPath(import.meta.url))
const source = (rel: string) => readFileSync(resolve(here, '..', rel), 'utf8')

/**
 * Места, где пользователю ПРЕДЛАГАЮТ отдел. Каждое обязано брать суженный
 * список: проверять только чистую функцию мало — именно здесь баг и жил.
 */
const PICKERS = [
  {
    screen: 'табель',
    file: 'pages/TimesheetPage.tsx',
    points: [
      ['выпадашка отделов в шапке', 'selectableDepartments.map('],
      ['экран первичного выбора отдела', 'departments={selectableDepartments}'],
    ],
  },
  {
    screen: 'ведомость',
    file: 'pages/admin/PayrollPage.tsx',
    points: [['выпадашка отделов в шапке', 'selectableDepartments.map(']],
  },
] as const

for (const { screen, file, points } of PICKERS) {
  for (const [where, needle] of points) {
    test(`${screen}: ${where} берёт суженный список`, () => {
      assert.ok(source(file).includes(needle), `${file}: ожидалось «${needle}»`)
    })
  }

  test(`${screen}: сужение и сброс идут из общего utils/departments`, () => {
    const code = source(file)
    assert.ok(code.includes('departmentsForCompany('), `${file}: нет departmentsForCompany`)
    assert.ok(code.includes('departmentChoiceIsStale('), `${file}: нет сброса невалидного отдела`)
  })
}

test('ведомость: неотфильтрованного списка отделов в разметке не осталось', () => {
  // В табеле такая же проверка невозможна текстом: единственный
  // `departments.map` там — внутри презентационного `DepartmentGate`, и это
  // ЕГО проп, которому выше передан `selectableDepartments` (проверено).
  assert.ok(!source('pages/admin/PayrollPage.tsx').includes('{departments.map('))
})

test('ведомость: отбор по долям распределения убран', () => {
  // Прежнее правило: строка подходила, если по компании есть доля > 0.
  // Именно оно тащило в «Комфорт» сотрудников «Земли МО».
  const code = source('pages/admin/PayrollPage.tsx')
  assert.ok(!code.includes('hasShare'), 'PayrollPage: остался отбор строк по долям распределения')
  assert.ok(code.includes('statementRowInCompany('), 'PayrollPage: не применяется общее правило')
})

test('табель: своей копии правила не осталось', () => {
  const code = source('pages/TimesheetPage.tsx')
  assert.ok(
    !code.includes('function positionInCompany('),
    'TimesheetPage: правило снова продублировано вместо utils/departments',
  )
  assert.ok(code.includes('positionInCompany(position, companyFilter)'))
})

test('распределение затрат в ведомости не тронуто', () => {
  // Меняется ТОЛЬКО отбор строк: колонки распределения, живой пересчёт и
  // правка процентов остаются на месте.
  const code = source('pages/admin/PayrollPage.tsx')
  for (const kept of ['distributeToThousands(', 'row.distribution', 'setPercent(']) {
    assert.ok(code.includes(kept), `PayrollPage: пропало «${kept}» — задета аналитика распределения`)
  }
})
