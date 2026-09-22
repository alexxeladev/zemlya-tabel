// Штат охраны ведётся из модуля «Вахта» (task_guard_ownership).
//
// Владение — на уровне РАБОЧЕГО МЕСТА: охранную позицию правит только вахта,
// обычную и самого человека — общий справочник. Решает это БЭК
// (`services/guard_staff.py`, отказ 403); здесь — только отражение для экрана:
// какие кнопки прятать и куда вести. Признак берётся из пришедшего с бэка
// `department.is_guard_department`, своего правила у фронта нет.

// Расширение `.ts` — ради `npm test` (node:test без сборщика).
import { vahtaSettingsPath } from './vahtaSettings.ts'

/**
 * Где ведутся сотрудники охраны: вкладка настроек вахты
 * (task_vahta_settings_staff; отдельной страницы `/vahta/staff` больше нет).
 */
export const GUARD_STAFF_PATH = vahtaSettingsPath('staff')

/**
 * Кто может открыть вкладку «Сотрудники охраны» — те же роли, что у маршрута
 * настроек вахты (`AppRouter`, `_require_settings` бэка): admin и менеджер.
 * Остальным ссылка вела бы на «нет доступа» — им показывается текст.
 */
export function canOpenGuardStaff(role: string | null | undefined): boolean {
  return role === 'admin' || role === 'manager'
}

/** Ссылка сразу на рабочее место: вкладка откроется с его формой. */
export function guardStaffPositionPath(positionId: number): string {
  return vahtaSettingsPath('staff', { positionId })
}

export function isGuardDepartment(dept: { is_guard_department?: boolean } | null | undefined): boolean {
  return Boolean(dept?.is_guard_department)
}

/** Рабочее место в охранном подразделении — правится в вахте, а не в карточке. */
export function isGuardPosition(
  // Структурный тип, а не `EmployeePosition`: у табеля своя, урезанная форма позиции.
  p: { department?: { is_guard_department?: boolean } | null },
): boolean {
  return isGuardDepartment(p.department)
}

/**
 * Почему общий ввод на охранном рабочем месте закрыт (аудит 2-Г). Часы,
 * премии/KPI/аванс и заём для него бэк отклоняет (403): позицию считает модуль
 * вахты, и общий ввод расчётом игнорировался. Экран ввод не даёт, а уже
 * введённое до запрета оставляет снимаемым.
 */
export const GUARD_ACCRUAL_HINT =
  'Охранное рабочее место: смены, премии, штрафы и выплаты ведутся в модуле «Вахта»'

/**
 * Подпись поля суммы в форме — по способу оплаты должности из справочника
 * (`GuardJobTitle.pay_type`): оклад за месяц либо ставка за смену. Это только
 * подпись до сохранения — сам тип выставляет бэк по той же должности.
 */
export function guardAmountLabel(payType: 'per_shift' | 'salary' | undefined): string {
  return payType === 'salary' ? 'Оклад за месяц, ₽' : 'Ставка за смену, ₽'
}

// ── Подтверждения 409 ─────────────────────────────────────────────────────────
// Три случая, когда бэк не запрещает операцию, а откатывает её и отвечает 409 с
// причинами: переход рабочего места в общий справочник (перевод позиции в
// обычный отдел, снятие флага охраны у отдела) и снятие признака «официально
// устроен» при уже начисленной выплате. Спрашиваем и повторяем с `confirm`.

const TRANSFER_ERROR = 'guard_transfer_out_confirmation_required'
const FLAG_ERROR = 'guard_flag_removal_confirmation_required'
const OFFICIAL_ERROR = 'guard_official_removal_confirmation_required'

const CONFIRM_ERRORS = [TRANSFER_ERROR, FLAG_ERROR, OFFICIAL_ERROR]

const MONTHS_RU = [
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]

type GuardConfirmDetail = {
  error?: string
  message?: string
  issues?: string[]
  position_count?: number
  not_calculable_count?: number
  /** Снятие признака «официально устроен»: месяцы с начисленной выплатой. */
  months?: { year: number; month: number; amount: string }[]
  total?: string
  /** Перевод из охраны гасит признак: те же месяцы, что обнулятся. */
  official_months?: { year: number; month: number; amount: string }[]
}

/** «• август 2026 — 25 230 ₽» построчно. */
function monthLines(
  months: { year: number; month: number; amount: string }[],
): string {
  return months
    .map((m) => `• ${MONTHS_RU[m.month - 1] ?? m.month} ${m.year} — ${money(m.amount)}`)
    .join('\n')
}

/** Сумма из ответа бэка в «12 615 ₽»: Decimal приходит строкой. */
function money(value: string | undefined): string {
  const n = parseFloat(value ?? '0')
  if (Number.isNaN(n)) return `${value} ₽`
  return `${n.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽`
}

/** Текст подтверждения или `null`, если ошибка не из этих трёх. */
export function guardConfirmMessage(detail: unknown): string | null {
  if (typeof detail !== 'object' || detail === null) return null
  const d = detail as GuardConfirmDetail
  if (!d.error || !CONFIRM_ERRORS.includes(d.error)) return null

  if (d.error === OFFICIAL_ERROR) {
    const lines = [
      d.message ??
        'У рабочего места уже начислена официальная выплата — после снятия ' +
          'признака она и налог станут нулевыми',
    ]
    const months = d.months ?? []
    if (months.length) {
      lines.push(
        'Обнулится выплата:\n' +
          monthLines(months) +
          (d.total ? `\nВсего ${money(d.total)}` : ''),
      )
    }
    lines.push('Продолжить?')
    return lines.join('\n\n')
  }

  const lines = [d.message ?? 'Рабочие места перейдут в общий справочник.']
  const issues = d.issues ?? []
  if (d.error === FLAG_ERROR && (d.not_calculable_count ?? 0) > 0) {
    lines.push(`Из них не войдут в расчёт: ${d.not_calculable_count}.`)
  }
  if (issues.length) {
    lines.push(
      (d.error === TRANSFER_ERROR ? 'Рабочее место не войдёт в расчёт:' : 'Причины:') +
        '\n' + issues.map((i) => `• ${i}`).join('\n'),
    )
    lines.push('Недостающее заполняется в общем справочнике сотрудников.')
  }
  // Перевод из охраны снимает «официально устроен» — одно подтверждение должно
  // назвать ОБА последствия, иначе про обнулённые выплаты никто не узнает.
  const official = d.official_months ?? []
  if (official.length) {
    lines.push(
      'Признак «официально устроен» будет снят, обнулится выплата:\n' +
        monthLines(official),
    )
  }
  lines.push('Продолжить?')
  return lines.join('\n\n')
}

/** Пользователь не подтвердил — ничего не сохранено. */
export const GUARD_CONFIRM_CANCELLED = Symbol('guard-confirm-cancelled')

/**
 * Сохранение, которое может упереться в подтверждение перехода из охраны.
 * `run(false)` — обычная попытка; 409 про охрану → спрашиваем → `run(true)`.
 * Любая другая ошибка пробрасывается.
 */
export async function withGuardConfirm<T>(
  run: (confirm: boolean) => Promise<T>,
  ask: (message: string) => boolean,
): Promise<T | typeof GUARD_CONFIRM_CANCELLED> {
  try {
    return await run(false)
  } catch (e) {
    const detail =
      typeof e === 'object' && e !== null && 'detail' in e
        ? (e as { detail?: unknown }).detail
        : undefined
    const message = guardConfirmMessage(detail)
    if (!message) throw e
    if (!ask(message)) return GUARD_CONFIRM_CANCELLED
    return run(true)
  }
}

/** Табельщику: новое рабочее место в охране (второй пост в месяце, человек из
 *  другого подразделения) заводит только admin или менеджер охраны — сервер
 *  отклонит (task_stage2_access п.2.9). Подсказка, а не блокировка: точно
 *  «нужно ли новое место» знает только сервер (пост и месяц). */
export const TIMEKEEPER_NEW_POSITION_HINT =
  'Кто уже стоит на другом месте в этом месяце, на второе место ставит администратор или менеджер охраны.'
