// Сохранение состояния интерфейса между разделами и перезагрузками страницы
// (task_ux_improvements ч.3).
//
// Только UI-настройки: выбранный период, фильтры, вид, развёрнутость блоков.
// Ничего чувствительного — токен и профиль живут в своём месте, сюда не кладём.
// В БД это тоже не едет: состояние экрана — свойство браузера, а не данных.
//
// localStorage может быть недоступен (приватный режим, отключённые cookies,
// переполненная квота) — тогда работаем как раньше, просто без сохранения:
// каждое обращение обёрнуто и любая ошибка означает «значения нет».

const PREFIX = 'tabel.'

export const UI_KEYS = {
  /** период (год+месяц), общий для табеля и «Расчёт ЗП» */
  period: 'period',
  /** период дашборда — диапазон месяцев, у него своя семантика */
  dashboardPeriod: 'dashboard.period',
  /** табель: выбранный отдел */
  timesheetDept: 'timesheet.dept',
  /** табель: фильтр компании (фильтры колонок сессионные, не сохраняются) */
  timesheetFilters: 'timesheet.filters',
  /** табель: развёрнут ли блок «ПО КОМПАНИЯМ» в подвале */
  timesheetCompanySummary: 'timesheet.companySummary',
  /** табель: развёрнут ли блок «ЗАЯВКИ НА ПОДБОР» над таблицей */
  timesheetQuantities: 'timesheet.quantities',
  /** ведомость «Расчёт ЗП»: отдел, поиск, фильтр компании */
  payrollFilters: 'payroll.filters',
  /** «Задачи»: показывать ли закрытые периоды */
  tasksShowClosed: 'tasks.showClosed',
} as const

export function loadUiState<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(PREFIX + key)
    if (raw === null) return fallback
    const parsed = JSON.parse(raw) as unknown
    return parsed === null || parsed === undefined ? fallback : (parsed as T)
  } catch {
    // Недоступное хранилище или битый JSON — молча возвращаемся к дефолту:
    // сохранение вида не та вещь, ради которой стоит показывать ошибку.
    return fallback
  }
}

export function saveUiState(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch {
    /* хранилище недоступно — просто не сохраняем */
  }
}

/**
 * Прочитать сохранённое состояние с проверкой формы.
 *
 * Формат ключа может измениться между версиями приложения, а в localStorage
 * останется старое значение — без проверки оно приехало бы в компонент и
 * уронило бы экран. Не прошло проверку — берём дефолт.
 */
export function loadValidated<T>(
  key: string,
  fallback: T,
  isValid: (value: unknown) => boolean,
): T {
  const value = loadUiState<unknown>(key, fallback)
  return isValid(value) ? (value as T) : fallback
}

// ── Общие валидаторы ──────────────────────────────────────────────────────────

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

export const isYearMonth = (v: unknown): boolean =>
  isObject(v)
  && typeof v.year === 'number' && v.year >= 2000 && v.year <= 2100
  && typeof v.month === 'number' && v.month >= 1 && v.month <= 12

export const isMonthRange = (v: unknown): boolean =>
  isYearMonth(v)
  && isObject(v)
  && typeof v.toYear === 'number' && v.toYear >= 2000 && v.toYear <= 2100
  && typeof v.toMonth === 'number' && v.toMonth >= 1 && v.toMonth <= 12
