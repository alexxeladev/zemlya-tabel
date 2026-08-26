/**
 * Короткое название юрлица для узких колонок (шапки распределения).
 *
 * В справочнике компании заведены как «ООО "Комфорт"» — в заголовок колонки
 * такое не влезает, а код («kft») человеку ничего не говорит. Снимаем правовую
 * форму и кавычки: «ООО "Комфорт"» → «Комфорт». Полное имя остаётся в подсказке.
 *
 * Нормализация та же по смыслу, что `company_keys` в
 * backend/app/services/employee_import.py, но здесь она только для показа —
 * ничего по этому имени не ищется.
 */
const LEGAL_FORMS = /^(ООО|ОАО|ЗАО|ПАО|АО|ИП|НАО)\s+/i

export function companyLabel(company: { name?: string | null; code: string }): string {
  const name = (company.name ?? '').trim()
  if (!name) return company.code
  const short = name.replace(LEGAL_FORMS, '').replace(/^[«"']+|[»"']+$/g, '').trim()
  return short || name
}
