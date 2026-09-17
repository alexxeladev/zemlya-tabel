// Штат охраны ведётся из модуля «Вахта» (task_guard_ownership).
//
// Владение — на уровне РАБОЧЕГО МЕСТА: охранную позицию правит только вахта,
// обычную и самого человека — общий справочник. Решает это БЭК
// (`services/guard_staff.py`, отказ 403); здесь — только отражение для экрана:
// какие кнопки прятать и куда вести. Признак берётся из пришедшего с бэка
// `department.is_guard_department`, своего правила у фронта нет.

import type { Department, EmployeePosition } from '../types/api'

export type GuardKind = 'guard' | 'gbr' | 'dispatcher' | 'chief'

export const GUARD_KIND_OPTIONS: { value: GuardKind; label: string }[] = [
  { value: 'guard', label: 'Охранник' },
  { value: 'gbr', label: 'ГБР' },
  { value: 'dispatcher', label: 'Диспетчер' },
  { value: 'chief', label: 'Начальник охраны' },
]

/** Путь экрана «Сотрудники охраны». */
export const GUARD_STAFF_PATH = '/vahta/staff'

export function isGuardDepartment(dept: Pick<Department, 'is_guard_department'> | null | undefined): boolean {
  return Boolean(dept?.is_guard_department)
}

/** Рабочее место в охранном подразделении — правится в вахте, а не в карточке. */
export function isGuardPosition(p: Pick<EmployeePosition, 'department'>): boolean {
  return isGuardDepartment(p.department)
}

/**
 * Подпись поля суммы в форме. Тип оплаты следует из ДОЛЖНОСТИ: у начальника
 * охраны оклад за месяц, у остальных ставка за смену. Это только подпись до
 * сохранения — сам тип выставляет бэк (`pay_type_for_kind`), и после сохранения
 * экран показывает `pay_type` из ответа.
 */
export function guardAmountLabel(kind: GuardKind): string {
  return kind === 'chief' ? 'Оклад за месяц, ₽' : 'Ставка за смену, ₽'
}

// ── Подтверждения 409 ─────────────────────────────────────────────────────────
// Два случая, когда охранные рабочие места переходят в общий справочник:
// перевод позиции в обычный отдел и снятие флага охраны у отдела. Бэк их не
// запрещает, а откатывает и отвечает 409 с причинами — спрашиваем и повторяем.

const TRANSFER_ERROR = 'guard_transfer_out_confirmation_required'
const FLAG_ERROR = 'guard_flag_removal_confirmation_required'

type GuardConfirmDetail = {
  error?: string
  message?: string
  issues?: string[]
  position_count?: number
  not_calculable_count?: number
}

/** Текст подтверждения или `null`, если ошибка не про переход из охраны. */
export function guardConfirmMessage(detail: unknown): string | null {
  if (typeof detail !== 'object' || detail === null) return null
  const d = detail as GuardConfirmDetail
  if (d.error !== TRANSFER_ERROR && d.error !== FLAG_ERROR) return null

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
