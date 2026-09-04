/** timekeeper — табельщик: ведёт время своих отделов, финансов не видит
 *  (task_timekeeper_role). По правам между employee и manager. */
export type UserRole = 'admin' | 'manager' | 'accountant' | 'timekeeper' | 'employee'
export type WeekendPayType = 'coefficient' | 'fixed_rate'

export interface CompanyBreakdown {
  company_id: number
  company_code: string
  company_name: string
  hours: string
  percent: string
  base_amount: string
  overtime_amount: string
  off_schedule_amount: string
  holiday_amount: string
  total: string
}

export interface EmployeePayroll {
  employee_id: number
  employee_name: string
  /** строка расчёта = ПОЗИЦИЯ: employee_id у совместителя повторяется */
  position_id: number | null
  position_title: string | null
  is_primary_position: boolean
  /** у посменного здесь условный оклад = ставка × норма смен */
  rate: string | null
  schedule_name: string | null
  pay_type: PayType
  shift_rate: string | null
  worked_shifts: number
  norm_shifts: number | null
  // Смены в базе посменного: плановые дни графика (выходные/праздники
  // оплачены по коэффициенту, в базу не входят)
  base_shifts: number
  total_hours: string
  norm_hours: string | null
  delta_hours: string | null
  overtime_hours: string
  /** выход в свой выходной по графику */
  off_schedule_hours: string
  /** работа в нерабочий праздничный день календаря */
  holiday_hours: string
  norm_days: number | null
  fact_days: number
  hourly_rate: string | null
  base_amount: string
  overtime_amount: string
  off_schedule_amount: string
  holiday_amount: string
  total_amount: string
  // Ночные смены: число отмеченных, ставка (фонд отдела / дни месяца) и
  // надбавка = смены × ставка. Надбавка входит в total_amount и «к выплате».
  // У табельщика ставка и сумма пустые, а число смен — на месте.
  night_shifts: number
  night_rate: string | null
  night_amount: string
  // Отсутствия: дни по видам и оплата ОТ/Б
  vacation_days: number
  unpaid_days: number
  sick_days: number
  absent_days: number
  vacation_paid_days: number
  sick_paid_days: number
  vacation_amount: string
  sick_amount: string
  // Годовой лимит больничного
  sick_limit_days: number
  sick_days_used_before: number
  sick_unpaid_days: number
  sick_limit_remaining: number
  weekend_pay_type: WeekendPayType | null
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  holiday_pay_type: WeekendPayType | null
  holiday_coefficient: string | null
  holiday_fixed_rate: string | null
  premium_amount: string
  kpi_amount: string
  advance_deduction: string
  loan_deduction: string
  loan_remaining: string
  loan_planned_deduction: string
  loan_is_manual: boolean
  total_deductions: string
  // net_payout округлён математически до 1000 ₽; exact/tail — справочно
  net_payout: string
  net_payout_exact: string
  rounding_tail: string
  breakdown_by_company: CompanyBreakdown[]
  is_calculable: boolean
  reason_if_not_calculable: string | null
}

export interface PayrollSummary {
  year: number
  month: number
  employees: EmployeePayroll[]
  total_employees: number
  total_hours: string
  total_base_amount: string
  total_overtime_amount: string
  total_off_schedule_amount: string
  total_holiday_amount: string
  total_vacation_amount: string
  total_sick_amount: string
  total_night_amount: string
  grand_total: string
  total_premium: string
  total_kpi: string
  total_deductions: string
  total_net_payout: string
  total_net_payout_exact: string
  total_rounding_tail: string
}

// ── Payroll statement (задача 3.11b) ──
export interface CompanyShare {
  company_id: number
  percent: string
}

export interface EmployeeShares {
  employee_id: number
  /** рабочее место, к которому относятся проценты (null — основное) */
  position_id: number | null
  shares: CompanyShare[]
  percent_sum: string
  // Дефолт отдела — наследуется, если своего распределения нет (каскад, ч.3)
  department_id: number | null
  department_name: string | null
  department_shares: CompanyShare[]
  inherits_department: boolean
}

export interface DepartmentShares {
  department_id: number
  shares: CompanyShare[]
  percent_sum: string
}

/**
 * Откуда взято распределение по юрлицам.
 * Каскад: month (правка на месяц) > employee (карточка) > department > hours.
 * quantity — отдел с флагом «распределение по количественному показателю»
 * (заявки у HR, АРМ у ИТ): каскад для него не применяется вовсе.
 */
export type DistributionSource = 'month' | 'employee' | 'department' | 'hours' | 'quantity'

/**
 * Количественный показатель отдела по юрлицу за месяц.
 * Хранятся две части (у HR «в работе»/«закрытые»); `count` (всего) — их сумма,
 * считается на бэке. У показателя без разбивки (АРМ) заполнена только part1.
 */
export interface QuantityShare {
  company_id: number
  part1: number
  part2: number
  /** всего = part1 + part2; это и есть база распределения */
  count: number
  /** количество компании / сумму × 100, до сотых; сумма ровно 100.00 */
  percent: string
}

/** Показатель отдела за месяц + проценты, по которым делится зарплата отдела. */
export interface DepartmentQuantities {
  department_id: number
  department_name: string | null
  /** подпись показателя из карточки отдела: «Заявки» у HR, «АРМ» у ИТ */
  metric_name: string | null
  /** подписи частей; при has_parts=false показатель вводится одним числом */
  part1_name: string | null
  part2_name: string | null
  has_parts: boolean
  year: number
  month: number
  items: QuantityShare[]
  total_part1: number
  total_part2: number
  total_count: number
  /** показатель за месяц не заведён → отдел временно идёт по обычному каскаду */
  is_empty: boolean
}

/**
 * Распределение рабочего места по юрлицам — для блока «Распределение» в табеле.
 * Считает бэк теми же числами, что ведомость: фронт не пересобирает базу из
 * кусков расчёта. База — «К выплате» (task_it_arm_distribution ч.2).
 */
export interface QuantityDistributionRow {
  employee_id: number
  position_id: number | null
  department_id: number | null
  /** База распределения — «Итого начислено» строки */
  base_amount: string
  /** company_id → сумма, каждая кратна 1000 ₽ (округление ВНИЗ) */
  amounts: Record<number, string>
  /** base_amount − Σ amounts, от 0 до 999 ₽: никому не приписано */
  unallocated_remainder: string
}

export interface StatementCompanyRef {
  id: number
  code: string
  name: string
  /** короткое имя для заголовка колонки; полное — в name (идёт в подсказку) */
  display_name: string
  sort_order: number
}

export interface StatementCompanyAmount {
  company_id: number
  /** процент, ЗАДАННЫЙ каскадом — его и правят в ведомости */
  percent: string
  /**
   * фактическая доля юрлица в «Итого начислено». С целевыми премиями
   * (task_funding_source) отличается от заданной: каскад 50/50 + целевая
   * премия даёт 40/60. Показываем её, а правим percent.
   */
  effective_percent: string
  amount: string
}

export interface StatementRow {
  employee_id: number
  /**
   * Строка ведомости = РАБОЧЕЕ МЕСТО (task_positions). У совместителя строк
   * столько, сколько позиций; employee_id повторяется, «к выплате» между
   * ними не суммируется — платят разные компании.
   */
  position_id: number | null
  is_primary_position: boolean
  tab_number: string | null
  employee_name: string
  main_company_id: number | null
  main_company_name: string | null
  department_name: string | null
  /** отдел строки и его головная компания — по ним фильтр юрлица отбирает
   *  строки (то же правило, что в табеле); в деньгах не участвуют */
  department_id?: number | null
  department_head_company_id?: number | null
  position: string | null
  schedule_name: string | null
  /** у посменного здесь условный оклад = ставка × норма смен */
  rate: string | null
  pay_type: PayType
  shift_rate: string | null
  worked_shifts: number
  norm_shifts: number | null
  // Смены в базе посменного: плановые дни графика (выходные/праздники
  // оплачены по коэффициенту, в базу не входят)
  base_shifts: number
  norm_hours: string | null
  fact_hours: string
  overtime_coefficient: string
  /**
   * Переработка ведомости — сверхурочные ПЛЮС работа в выходные/праздники по
   * графику (task_overtime_columns): в часах и в рублях это одни и те же
   * категории, поэтому число и сумма в соседних колонках сходятся.
   * Дельты «факт − норма» в ведомости нет: она в расчёте не участвует.
   */
  overtime_hours: string
  overtime_amount: string
  /** Начислено, оклад — только оплата обычных часов (без выходных/праздничных) */
  base_salary: string
  premium_amount: string
  kpi_amount: string
  premium_extra_amount: string
  /** обоснования — по одной строке на запись; попадают и в Excel-выгрузку */
  premium_reasons: string[]
  kpi_reasons: string[]
  advance_reasons: string[]
  /** ручная правка удержания по займу (своего обоснования у займа нет) */
  loan_note: string | null
  vacation_days: number
  sick_days: number
  unpaid_days: number
  absent_days: number
  vacation_amount: string
  sick_amount: string
  sick_limit_days: number
  sick_unpaid_days: number
  sick_limit_remaining: number
  /** надбавка за ночные смены: число смен × ставка фонда отдела */
  night_shifts: number
  night_rate: string | null
  night_amount: string
  accrued_total: string
  deductions: string
  net_payout: string        // округлено до ближайшей 1000 ₽ (на распределение НЕ влияет)
  net_payout_exact: string
  rounding_tail: string
  is_overridden: boolean
  is_auto_distributed: boolean
  distribution_source: DistributionSource
  /**
   * Подпись количественного показателя отдела («Заявки», «АРМ»), если строка
   * относится к отделу, делящемуся по нему, и показатель за месяц задан.
   * Для ведомости это признак «правка процентов заблокирована»: он стоит и у
   * строк, ушедших на распределение из карточки (task_card_priority) —
   * исключения задаются только в карточке сотрудника.
   */
  quantity_metric_name: string | null
  /** пояснение: отдел «по заявкам», но заявок за месяц нет → каскад */
  distribution_note: string | null
  /**
   * Целевые премии/KPI (task_funding_source): {company_id: сумма}, их итог и
   * пометка «включает целевая премия 20000 ₽ (Секьюрити)». Каскад делит
   * «Итого начислено» МИНУС targeted_total, поэтому Σ распределения всегда
   * равна «Итого начислено».
   */
  targeted_amounts: Record<number, string>
  targeted_total: string
  targeted_note: string | null
  percent_sum: string
  distribution: StatementCompanyAmount[]
  distribution_total: string
  /**
   * «Итого начислено» − Σ распределения: суммы по юрлицам округляются ВНИЗ до
   * 1000 ₽, поэтому остаётся 0…999 ₽. Никому не приписывается — иначе затраты
   * юрлица оказались бы больше начисленного. Не путать с `rounding_tail`
   * (округление ВЫПЛАТЫ, знак любой).
   */
  unallocated_remainder: string
  is_calculable: boolean
  note: string | null
}

export interface PayrollStatement {
  year: number
  month: number
  companies: StatementCompanyRef[]
  rows: StatementRow[]
  total_overtime_amount: string
  total_base_salary: string
  total_vacation_amount: string
  total_sick_amount: string
  total_night_amount: string
  total_premium: string
  total_kpi: string
  total_accrued: string
  total_deductions: string
  total_net_payout: string
  total_net_payout_exact: string
  total_rounding_tail: string
  /** Σ нераспределённых остатков строк (округление РАСПРЕДЕЛЕНИЯ вниз до 1000) */
  total_unallocated_remainder: string
  distribution_totals: Record<number, string>
}

export type AdjustmentKind = 'premium' | 'kpi' | 'advance'

export interface Adjustment {
  id: number
  employee_id: number
  /** рабочее место, на котором заработано (task_positions); null — основное */
  position_id: number | null
  year: number
  month: number
  kind: AdjustmentKind
  amount: string
  reason: string
  /**
   * Источник финансирования (task_funding_source) — только у премии и KPI.
   * Задан: сумма целиком относится на затраты этого юрлица, база каскада
   * распределения на неё уменьшается. null — обычный каскад.
   */
  funding_company_id: number | null
  funding_company_name: string | null
  created_by_id: number | null
  created_at: string | null
}

export interface TimesheetEntry {
  employee_id: number
  /** рабочее место, на которое отработаны часы; null — заведено до позиций */
  position_id: number | null
  work_date: string  // YYYY-MM-DD
  company_id: number
  hours: number  // decimal as number
}

// ── Отсутствия: коды ОТ / ДО / Б / Н ──
export type AbsenceKind = 'vacation' | 'unpaid' | 'sick' | 'absent'

export interface Absence {
  employee_id: number
  work_date: string  // YYYY-MM-DD
  kind: AbsenceKind
  code: string       // ОТ / ДО / Б / Н
  // Больничный сверх годового лимита — отмечен, но не оплачивается
  over_limit: boolean
}

// ── Ночные смены (task_night_shifts_rework) ──
// Отметка выхода в ночь: часов нет, оплачивается сам факт смены по ставке
// «фонд отдела / календарные дни месяца». К графику не привязана и с дневными
// часами того же дня сосуществует.
export interface NightShift {
  employee_id: number
  position_id: number
  work_date: string  // YYYY-MM-DD
}

/** Состояние фонда ночных смен отдела за месяц (индикатор остатка). */
export interface NightFund {
  department_id: number
  department_name: string | null
  /** деньги — у табельщика null; смены видны всем, кто ведёт табель */
  fund: string | null
  rate: string | null
  limit_shifts: number
  used_shifts: number
  remaining_shifts: number
}

export interface TimesheetMonthResponse {
  year: number
  month: number
  employees: Employee[]
  companies: Company[]
  entries: TimesheetEntry[]
  periods: TimesheetPeriod[]
  extra_companies_by_employee: Record<string, number[]>
  /** видимые актору рабочие места по сотрудникам: табель строит строку на позицию */
  positions_by_employee: Record<string, EmployeePosition[]>
  absences: Absence[]
  night_shifts: NightShift[]
  night_funds: NightFund[]
  payroll: PayrollSummary | null
  adjustments: Adjustment[]
}

export interface AutofillSkippedEmployee {
  employee_id: number
  employee_name: string
  reason: string
}

export interface AutofillPreview {
  year: number
  month: number
  entries_to_create: TimesheetCellInput[]
  cells_skipped: number
  employees_processed: number
  employees_skipped: AutofillSkippedEmployee[]
}

export interface TimesheetCellInput {
  employee_id: number
  /** на какое рабочее место идут часы; не задано — на основное */
  position_id?: number | null
  work_date: string  // YYYY-MM-DD
  company_id: number
  hours: number
}

export type PeriodStatus = 'draft' | 'pending_review' | 'closed'

export interface TimesheetPeriod {
  id: number
  department_id: number | null
  department_name: string | null
  year: number
  month: number
  status: PeriodStatus
  submitted_at: string | null
  submitted_by_name: string | null
  reviewed_at: string | null
  reviewed_by_name: string | null
  closed_at: string | null
  closed_by_name: string | null
  can_edit: boolean
  can_submit: boolean
  can_close: boolean
  can_return: boolean
  can_reopen: boolean
}

export interface PeriodTask {
  period_id: number
  department_id: number | null
  department_name: string
  year: number
  month: number
  status: PeriodStatus
  submitted_by_name: string | null
  submitted_at: string | null
  closed_by_name: string | null
  closed_at: string | null
  total_hours: number
}

export interface TasksResponse {
  pending_review: PeriodTask[]
  recently_closed: PeriodTask[]
}

export interface AuditLogEntry {
  id: number
  actor_id: number
  actor_name: string | null
  entity_type: string
  entity_id: number | null
  action: string
  before: unknown
  after: unknown
  reason: string | null
  created_at: string
}

export interface MonthData {
  month: number
  days: string
}

export interface ProductionCalendar {
  id: number
  year: number
  months: MonthData[]
  source: 'remote' | 'manual'
  loaded_at: string
  workdays_total: number
  short_days_total: number
}

export type DayType = 'work' | 'short' | 'holiday'

export interface DayInfo {
  day: number
  type: DayType
  weekday: number
}

export interface MonthSummary {
  year: number
  month: number
  workdays: number
  short_days: number
  norm_hours_8h: number
  days: DayInfo[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
  must_change_password: boolean
}

export interface Department {
  id: number
  name: string
  code: string
  /**
   * Головная компания — где отдел числится в дереве оргструктуры.
   * ТОЛЬКО группировка: на расчёт ЗП не влияет, сотрудники по-прежнему
   * работают на несколько юрлиц (часы + проценты распределения).
   */
  head_company_id: number | null
  /** месячный фонд ночных смен: из него считаются ставка и лимит числа смен */
  night_shift_fund: string | null
  /** зарплата отдела делится по количественному показателю, а не по каскаду */
  uses_quantity_distribution: boolean
  /** «Заявки» у HR, «АРМ» у ИТ; пусто — нейтральное «Количество» */
  quantity_metric_name: string | null
  /** подписи двух частей показателя; обе пусты — вводится одним числом */
  quantity_part1_name: string | null
  quantity_part2_name: string | null
  is_active: boolean
}

/** Руководитель или табельщик отдела — краткая карточка (task_org_structure ч.2,
 *  task_timekeeper_role). Связь одна, различает их `role`. */
export interface DepartmentManager {
  id: number
  full_name: string
  position: string | null
  email: string | null
  role: UserRole | null
}

export interface DepartmentManagers {
  department_id: number
  managers: DepartmentManager[]
}

// ── Дерево оргструктуры: Компания → Отдел → Сотрудники (ч.3) ──

export interface OrgEmployee {
  id: number
  full_name: string
  tab_number: string | null
  position: string | null
  role: UserRole | null
  is_active: boolean
}

export interface OrgDepartment {
  id: number
  name: string
  code: string
  is_active: boolean
  head_company_id: number | null
  /** фонд ночных смен на месяц: задаёт ставку смены и лимит их числа */
  night_shift_fund: string | null
  /** зарплата отдела делится по количественному показателю (заявки/АРМ) */
  uses_quantity_distribution: boolean
  quantity_metric_name: string | null
  quantity_part1_name: string | null
  quantity_part2_name: string | null
  managers: OrgEmployee[]
  /** Количество сотрудников — чтобы свёрнутый узел не рендерил список. */
  employee_count: number
  employees: OrgEmployee[]
}

export interface OrgCompany {
  id: number
  code: string
  name: string
  inn: string | null
  /** короткое название для узких колонок; пусто — выводится из name */
  short_name: string | null
  /** порядок перечисления юрлиц — общий для всех экранов и выгрузок */
  sort_order: number
  is_active: boolean
  departments: OrgDepartment[]
}

export interface OrgTree {
  companies: OrgCompany[]
  departments_without_company: OrgDepartment[]
  employees_without_department: OrgEmployee[]
}

export interface Company {
  id: number
  code: string
  name: string
  inn: string | null
  short_name: string | null
  sort_order: number
  /** короткое имя, уже разрешённое бэком: short_name → name без правовой формы → код */
  display_name: string
  is_active: boolean
}

/** Перенос отдела в другую компанию (task_move_department): что будет затронуто.
 *  Закрытые месяцы перед сменой фиксируются как есть, поэтому прошлое не едет. */
export interface DepartmentMovePreview {
  department_id: number
  department_name: string
  source_company_id: number | null
  source_company_name: string | null
  target_company_id: number
  target_company_name: string
  employee_count: number
  position_count: number
  /** рабочие места тех же людей в ДРУГИХ отделах — они остаются на своих компаниях */
  untouched_position_count: number
  /** закрытые месяцы отдела: их расклад по юрлицам будет зафиксирован */
  closed_months: { year: number; month: number }[]
  /** у скольких позиций задан явный %, не включающий целевую компанию */
  stale_share_position_count: number
  /** дефолт распределения самого отдела не включает целевую компанию */
  department_shares_stale: boolean
  /** ячеек часов в незакрытых месяцах, которые сменят юрлицо на целевое */
  entries_to_reattribute: number
}

export interface DepartmentMoveResult {
  department_id: number
  target_company_id: number
  positions_moved: number
  employees_affected: number
  closed_months_frozen: number
  override_rows_written: number
  entries_reattributed: number
}

/** Тип оплаты позиции: оклад / смены × ставка / часы × ставка за час */
export type PayType = 'salary' | 'per_shift' | 'hourly'

/**
 * Рабочее место сотрудника (task_positions). У совместителя их несколько:
 * у каждого свои должность, тип оплаты и база, график, отдел, компания
 * и коэффициенты. Ровно одно помечено `is_primary`.
 */
export interface EmployeePosition {
  id: number
  employee_id: number
  title: string | null
  /** должность, а без неё — «Основная»/«Совместительство» */
  display_title: string
  is_primary: boolean
  is_active: boolean
  sort_order: number
  department_id: number | null
  schedule_id: number | null
  company_id: number | null
  pay_type: PayType
  rate: string | null
  shift_rate: string | null
  hour_rate: string | null
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  holiday_pay_type: WeekendPayType
  holiday_coefficient: string | null
  holiday_fixed_rate: string | null
  overtime_coefficient: string | null
  /** можно ли отмечать этому рабочему месту выходы в ночь; ставка не задаётся —
   *  она вычисляется из фонда отдела (task_night_shifts_rework) */
  has_night_shifts: boolean
  department: Department | null
  schedule: Schedule | null
  company: Company | null
}

/** Поля позиции, которые задаются из карточки (без служебных id). */
export type EmployeePositionInput = Partial<
  Pick<
    EmployeePosition,
    | 'title' | 'department_id' | 'schedule_id' | 'company_id'
    | 'pay_type' | 'rate' | 'shift_rate' | 'hour_rate'
    | 'weekend_pay_type' | 'weekend_coefficient' | 'weekend_fixed_rate'
    | 'holiday_pay_type' | 'holiday_coefficient' | 'holiday_fixed_rate'
    | 'overtime_coefficient' | 'has_night_shifts'
    | 'is_active' | 'sort_order'
  >
> & { is_primary?: boolean }

export type ScheduleType = 'weekday' | 'cyclic'

export interface Schedule {
  id: number
  name: string
  hours_per_shift: number
  /** weekday — по дням недели (5/2, 6/1, вс–чт); cyclic — скользящий цикл (2/2, 3/3) */
  schedule_type: ScheduleType
  /** weekday: рабочие дни недели, 0=Пн … 6=Вс. null → выводятся из имени «N/M» */
  work_weekdays: number[] | null
  /** cyclic: анкер фазы цикла */
  cycle_start_date: string | null
  cycle_work_days: number | null
  cycle_off_days: number | null
  description: string | null
  is_active: boolean
}

export interface SchedulePreviewDay {
  day: number
  work_date: string
  weekday: number
  is_work_day: boolean
  hours: number
  is_holiday: boolean
  is_short_day: boolean
}

export interface SchedulePreview {
  year: number
  month: number
  days: SchedulePreviewDay[]
  work_days: number
  norm_hours: number
  has_calendar: boolean
  issue: string | null
}

export interface Employee {
  id: number
  tab_number: string | null
  full_name: string
  position: string | null
  department_id: number | null
  schedule_id: number | null
  default_company_id: number | null
  /** salary — месячный оклад; per_shift — смены × ставка; hourly — часы × ставка */
  pay_type: PayType
  rate: string | null
  shift_rate: string | null
  hour_rate: string | null
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  holiday_pay_type: WeekendPayType
  holiday_coefficient: string | null
  holiday_fixed_rate: string | null
  overtime_coefficient: string | null
  loan_amount: string | null
  loan_term_months: number | null
  loan_start_date: string | null
  is_active: boolean
  status: 'active' | 'dismissed'
  hire_date: string | null
  dismissal_date: string | null
  // auth fields
  email: string | null
  role: UserRole | null
  has_access: boolean
  must_change_password: boolean
  last_login_at: string | null
  is_system_admin: boolean
  // nested
  department: Department | null
  schedule: Schedule | null
  default_company: Company | null
  /**
   * Отделы, которыми РУКОВОДИТ (task_org_structure ч.2) — не путать с
   * `department`, где сотрудник числится. Заполнено только у manager.
   */
  managed_department_ids: number[]
  /**
   * Рабочие места (task_positions ч.B). Плоские поля выше — это ОСНОВНАЯ
   * позиция; здесь весь список, включая совместительство.
   */
  positions: EmployeePosition[]
}

// ── Импорт сотрудников из Excel (task_employee_import) ──

/** Строка файла после разбора: что распозналось и что с ней не так. */
export interface EmployeeImportRow {
  row_number: number
  is_valid: boolean
  errors: string[]
  /** Значения как в файле — показываем «как ввели», рядом с распознанным */
  raw: Record<string, string>
  tab_number: string | null
  full_name: string | null
  position: string | null
  company_id: number | null
  company_name: string | null
  department_id: number | null
  department_name: string | null
  schedule_id: number | null
  schedule_name: string | null
  pay_type: PayType
  rate: string | null
  shift_rate: string | null
  weekend_pay_type: WeekendPayType
  weekend_coefficient: string | null
  weekend_fixed_rate: string | null
  hire_date: string | null
  /** Заполняется только после подтверждённого импорта */
  created: boolean
  employee_id: number | null
}

export interface EmployeeImportResult {
  /** false — превью (в БД ничего не создано), true — импорт выполнен */
  confirmed: boolean
  total: number
  valid_count: number
  error_count: number
  created_count: number
  skipped_count: number
  rows: EmployeeImportRow[]
}
