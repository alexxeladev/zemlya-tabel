/**
 * Отображаемое название юрлица (task_vedomost_format ч.2).
 *
 * В справочнике компании заведены как «ООО "Комфорт"» — в заголовок колонки
 * такое не влезает, а код («kft») человеку ничего не говорит. Приоритет:
 * `display_name` с бэка (там уже разрешено ручное короткое название) → своя
 * нормализация «ООО "Комфорт"» → «Комфорт» → код. Полное имя (`name`) остаётся
 * для подсказки — его сюда не подставляем.
 *
 * Своя нормализация нужна для типов, которые `display_name` не несут
 * (локальный `Company` в табеле). Она та же по смыслу, что
 * `company_display_name` в backend/app/services/company_order.py — правишь
 * одно, поглядывай на второе.
 */
const LEGAL_FORMS = /^(ООО|ОАО|ЗАО|ПАО|АО|ИП|НАО)\s+/i

export function companyLabel(company: {
  name?: string | null
  code: string
  display_name?: string | null
}): string {
  const resolved = (company.display_name ?? '').trim()
  if (resolved) return resolved
  const name = (company.name ?? '').trim()
  if (!name) return company.code
  const short = name.replace(LEGAL_FORMS, '').replace(/^[«"']+|[»"']+$/g, '').trim()
  return short || name
}
