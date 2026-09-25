/**
 * Отбор отделов по выбранному юрлицу — ОДНО место на табель и ведомость.
 *
 * Правило: юрлицо отдела — это его ГОЛОВНАЯ компания (`head_company_id`), та же
 * привязка, по которой отдел стоит в дереве оргструктуры. Это навигационный
 * ярлык, а не расчёт: мультикомпанийность сотрудников (часы и проценты
 * распределения по разным юрлицам) на этот отбор не влияет вовсе — иначе выбор
 * «К-Сервис» вытаскивал бы отделы «Земли МО» из-за одного проставленного часа.
 *
 * Отдел без головной компании (`head_company_id === null`) не принадлежит
 * ни одному юрлицу и при выбранной компании в список не попадает — так же,
 * как в дереве он висит в ветке «Без головной компании».
 *
 * Держать в одном месте обязательно: две копии этого правила разъедутся, и
 * табель с ведомостью начнут предлагать разные отделы под тем же фильтром.
 */

/** Отделы, предлагаемые к выбору. `companyId` не задан — все отделы. */
export function departmentsForCompany<T extends { head_company_id?: number | null }>(
  departments: T[],
  companyId: number | null | undefined,
): T[] {
  if (companyId == null) return departments
  return departments.filter((d) => d.head_company_id === companyId)
}

/**
 * Выбранный отдел больше не принадлежит выбранному юрлицу → выбор надо сбросить:
 * сочетание «Секьюрити + отдел Земли МО» даёт заведомо пустой экран без
 * объяснения. В табеле сброс возвращает на ЭКРАН ВЫБОРА отдела (режима «все
 * отделы» там больше нет), в ведомости — к «Все отделы».
 *
 * ПУСТОЙ справочник отделов сбросом НЕ считается: список грузится отдельным
 * запросом и на первом рендере пуст, а выбор отдела восстанавливается из
 * localStorage синхронно. Без этой оговорки сохранённый отдел стирался бы при
 * каждой перезагрузке страницы, если включён фильтр компании.
 */
export function departmentChoiceIsStale<T extends { id: number; head_company_id?: number | null }>(
  departments: T[],
  companyId: number | null | undefined,
  /**
   * id отдела; всё остальное — выбора отдела нет, сбрасывать нечего:
   * `'none'` (группа «Без отдела»), `'all'` (ведомость), `null`.
   */
  choice: number | 'none' | 'all' | null | undefined,
): boolean {
  if (companyId == null || typeof choice !== 'number' || departments.length === 0) return false
  return !departmentsForCompany(departments, companyId).some((d) => d.id === choice)
}

/**
 * Отдел, который принёс адрес (`?department_id=`): id, группа «Без отдела» или
 * `null`, если ссылка отдела не несёт (в том числе при негодном значении —
 * несуществующий id отсеет уже справочник отделов).
 *
 * Разбор ОДИН на весь экран: по этому же значению решается, снимать ли
 * сохранённый фильтр юрлица (`companyFilterAfterLink`). Вторая копия разбора
 * разъехалась бы с первой, и фильтр снимался бы не тогда, когда применён отдел.
 */
export function departmentChoiceFromLink(raw: string | null): number | 'none' | null {
  if (raw === 'none') return 'none'
  const id = parseInt(raw ?? '', 10)
  return Number.isFinite(id) ? id : null
}

/**
 * Ссылка с отделом СИЛЬНЕЕ сохранённого фильтра юрлица — иначе переход из
 * «Задач» и с дашборда упирается в экран выбора отдела.
 *
 * Фильтр юрлица и выбранный отдел сохраняются между заходами НЕЗАВИСИМО. Ссылка
 * задаёт отдел, но фильтр остаётся прежним, и `departmentChoiceIsStale` честно
 * видит противоречие («отдел «Земли МО» при фильтре «Комфорт-Security»») и
 * сбрасывает выбор на гейт. Причём гейт показывает отделы ОТФИЛЬТРОВАННОГО
 * юрлица, то есть нужного отдела в списке нет вовсе — до периода, ради которого
 * переходили, не добраться (поймано на препроде 25.09.2026).
 *
 * Поэтому применённый из ссылки отдел снимает фильтр. Снимается он и из
 * хранилища: иначе тот же тупик повторялся бы при следующем переходе.
 */
export function companyFilterAfterLink(
  saved: number | null,
  rawDepartmentId: string | null,
): number | null {
  return departmentChoiceFromLink(rawDepartmentId) === null ? saved : null
}

// ── Юрлицо РАБОЧЕГО МЕСТА: отбор строк табеля и ведомости ────────────────────

/**
 * Юрлицо рабочего места — ПО СПРАВОЧНЫМ ДАННЫМ, а не по деньгам.
 *
 * Решает ОТДЕЛ: юрлицо места — головная компания его отдела. Компания самой
 * позиции (`company_id`) — запасной вариант и только для мест БЕЗ отдела:
 * основная компания карточки сплошь и рядом не совпадает с компанией отдела,
 * и, взятая первой, она тянула бы в выдачу отделы чужих юрлиц.
 *
 * Ни ЧАСЫ, ни ДОЛИ РАСПРЕДЕЛЕНИЯ в этом не участвуют. Расчёт мультикомпанийный:
 * час на чужое юрлицо не делает человека его сотрудником, и разнесённые на
 * юрлицо затраты — тоже. Ведомость — документ на выплату ЛЮДЯМ, в ней должны
 * стоять те, кто в компании ЧИСЛИТСЯ: Иванов из «Земли МО» с 50% затрат на
 * «Комфорт» под фильтром «Комфорт» появляться не должен — «Комфорт» ему ничего
 * не платит. Разнесение затрат остаётся отдельной аналитикой ВНУТРИ строки
 * (колонки распределения), к отбору строк отношения не имеет.
 */
export interface Workplace {
  /** отдел рабочего места; null — место без отдела */
  department_id?: number | null
  /** головная компания этого отдела */
  department_head_company_id?: number | null
  /** основная компания самой позиции — запасной вариант для мест без отдела */
  company_id?: number | null
}

export function workplaceCompanyId(place: Workplace): number | null {
  if (place.department_id != null) return place.department_head_company_id ?? null
  return place.company_id ?? null
}

/** `companyId` не задан («Все компании») — подходит любое рабочее место. */
export function workplaceInCompany(place: Workplace, companyId: number | null | undefined): boolean {
  if (companyId == null) return true
  return workplaceCompanyId(place) === companyId
}

/**
 * Переходники от строк двух экранов к общему правилу. Живут здесь, а не в
 * страницах, именно чтобы правило осталось ОДНО: тест сверяет, что на одном и
 * том же рабочем месте оба дают один ответ.
 */
export function positionInCompany(
  position: {
    department_id: number | null
    department?: { head_company_id?: number | null } | null
    company_id: number | null
  },
  companyId: number | null | undefined,
): boolean {
  return workplaceInCompany(
    {
      department_id: position.department_id,
      department_head_company_id: position.department?.head_company_id,
      company_id: position.company_id,
    },
    companyId,
  )
}

/**
 * Строка ведомости. `main_company_id` — это и есть `company_id` позиции
 * (`main_company = position.company` в `services/payroll_statement.py`),
 * поэтому отдельного поля под него не заводится.
 */
export function statementRowInCompany(
  row: {
    department_id?: number | null
    department_head_company_id?: number | null
    main_company_id: number | null
  },
  companyId: number | null | undefined,
): boolean {
  return workplaceInCompany(
    {
      department_id: row.department_id,
      department_head_company_id: row.department_head_company_id,
      company_id: row.main_company_id,
    },
    companyId,
  )
}


// ── Сохранённый выбор отдела в табеле ────────────────────────────────────────

/**
 * Выбор отдела в табеле: id отдела, `'none'` — группа «Без отдела», `null` —
 * не выбран (показываем экран выбора).
 */
export type TimesheetDeptChoice = number | 'none' | null

/**
 * Годится ли сохранённое значение выбора отдела.
 *
 * Отдельная функция ради одного случая: в браузерах людей остался `'all'` от
 * режима «все отделы», снятого в task_timesheet_dept_only. Валидатор его НЕ
 * принимает, `loadValidated` отдаёт `null`, и человек попадает на экран выбора
 * отдела — вместо режима, которого больше нет.
 */
export function isTimesheetDeptChoice(value: unknown): value is TimesheetDeptChoice {
  return (
    value === 'none'
    || value === null
    || (typeof value === 'number' && Number.isFinite(value))
  )
}
