/**
 * Порядок колонок табеля вахты (task_vahta_columns_order).
 *
 * Проверка написана ПО ТРЕБОВАНИЮ: порядок «смен · зарплата · трудоустройство ·
 * премия · штраф · начислено · оф. выплата · налог · к выплате», одинаковый
 * набор колонок в режиме месяца и половины, и совпадение шапки с ячейками
 * строки (в разметке они помечены `data-col`).
 *
 * Запуск:  cd frontend && npm test
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  VAHTA_TAIL_COLUMNS,
  employmentLabel,
  vahtaColWidth,
  vahtaTailColumns,
  vahtaTaxLabel,
} from './vahtaColumns.ts'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p: string) => readFileSync(resolve(here, p), 'utf8')

test('порядок колонок — как в исходной таблице бухгалтерии', () => {
  assert.deepEqual(
    VAHTA_TAIL_COLUMNS.map((c) => c.key),
    ['shifts', 'salary', 'employment', 'premium', 'penalty', 'accrued', 'official', 'tax', 'payout'],
  )
  // Подписи — словами из исходного файла, а не техническими названиями полей.
  assert.deepEqual(
    VAHTA_TAIL_COLUMNS.map((c) => c.label),
    ['Смен', 'Зарплата', 'Трудоустройство', 'Премия', 'Штраф', 'Начислено',
     'Оф. выплата', 'Налог', 'К выплате'],
  )
})

test('строка читается слева направо как сборка суммы', () => {
  const keys = VAHTA_TAIL_COLUMNS.map((c) => c.key)
  const at = (k: string) => keys.indexOf(k)
  // зарплата + премия − штраф = начислено: слагаемые ДО итога.
  assert.ok(at('salary') < at('accrued'))
  assert.ok(at('premium') < at('accrued'))
  assert.ok(at('penalty') < at('accrued'))
  // начислено − оф. выплата = к выплате: вычет ПОСЛЕ итога, выплата последней.
  assert.ok(at('accrued') < at('official'))
  assert.ok(at('official') < at('payout'))
  // Премия и штраф — раздельные колонки, «Премии и штрафа» одной колонкой нет.
  assert.notEqual(at('premium'), at('penalty'))
  // Налог — после оф. выплаты, как было до правки.
  assert.ok(at('official') < at('tax') && at('tax') < at('payout'))
})

test('набор колонок не зависит от режима половины — только от доступа к деньгам', () => {
  const money = vahtaTailColumns(true).map((c) => c.key)
  assert.deepEqual(money, VAHTA_TAIL_COLUMNS.map((c) => c.key))
  // Табельщик: денег нет, но смены и трудоустройство видит (признак не денежный).
  assert.deepEqual(vahtaTailColumns(false).map((c) => c.key), ['shifts', 'employment'])
  // Порядок у обеих ролей один и тот же — фильтр, а не второй список.
  const timekeeper = vahtaTailColumns(false).map((c) => c.key)
  assert.deepEqual(timekeeper, money.filter((k) => timekeeper.includes(k)))
})

test('ячейки строки идут в том же порядке, что шапка', () => {
  const page = read('../pages/VahtaPage.tsx')
  const cells = [...page.matchAll(/data-col="([a-z]+)"/g)].map((m) => m[1])
  assert.deepEqual(cells, VAHTA_TAIL_COLUMNS.map((c) => c.key))
  // Шапка рисуется из того же списка, а не второй копией подписей.
  assert.match(page, /tailColumns\.map\(\(col\)/)
  assert.match(page, /const tailColumns = vahtaTailColumns\(showMoney\)/)
  // Прежней сводной ячейки «премия, штраф, оф. выплата» одной кнопкой нет:
  // премия и штраф — свои колонки, оф. выплата — своя, только для чтения.
  assert.doesNotMatch(page, /'премия, штраф'/)
})

test('число колонок строки-спины считается из того же списка', () => {
  const page = read('../pages/VahtaPage.tsx')
  assert.match(page, /const totalCols = 3 \+ days\.length \+ tailColumns\.length \+ 1/)
})

test('подпись налога несёт ставку', () => {
  assert.equal(vahtaTaxLabel('40.00'), 'Налог 40 %')
  assert.equal(vahtaTaxLabel(40.5), 'Налог 40.5 %')
  assert.equal(vahtaTaxLabel(null), 'Налог')
  assert.equal(vahtaTaxLabel(0), 'Налог')
})

test('трудоустройство — словом; пустое место — прочерк', () => {
  assert.equal(employmentLabel(true, true), 'Официальный')
  assert.equal(employmentLabel(false, true), 'Неофициальный')
  // Вакансия: человека нет, признак взять неоткуда — «данных нет».
  assert.equal(employmentLabel(false, false), '—')
  assert.equal(employmentLabel(true, false), '—')
})

test('ширина колонки берётся из одного места', () => {
  for (const col of VAHTA_TAIL_COLUMNS) {
    assert.equal(vahtaColWidth(col.key), col.width)
  }
  assert.equal(vahtaColWidth('нет такой'), 96)
})

test('зарплата и официальность сравниваются в memo строки', () => {
  // Без этого новые колонки показывали бы прежние значения после правки.
  const page = read('../pages/VahtaPage.tsx')
  assert.match(page, /a\.row\.salary === b\.row\.salary/)
  assert.match(page, /a\.row\.is_official === b\.row\.is_official/)
})

test('Excel вахты остаётся в порядке образца бухгалтерии (решение заказчика)', () => {
  // «Начислено» в файле нет, «Налоги» — хвостовой колонкой после разбивки.
  const backend = read('../../../backend/app/services/guard_export.py')
  assert.match(
    backend,
    /_TAIL_HEADERS = \[\s*"Кол-во смен", "Зарплата", "Трудоустройство", "Премия", "Штраф",\s*"Оф\. выплата", "К выплате", "Примечания",\s*\]/,
  )
})
