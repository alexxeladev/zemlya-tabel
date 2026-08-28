// frontend/src/pages/TimesheetPage.tsx
// Полная переделка страницы табеля по образцу tabel_portal_reference.html
//
// Архитектура:
//   - Одна строка на сотрудника
//   - Внутри ячейки дня — несколько "слотов" (компания + часы)
//   - Слот = <select компании> + <input часы> + ×
//   - Кнопка "+" добавляет новый слот для свободной компании
//   - Sticky первая колонка (Сотрудник/Отдел/График), sticky шапка, sticky правые колонки
//   - Скролл только внутри таблицы, не страницы
//
// API:
//   - GET /api/timesheet/{year}/{month}?include_payroll=true&department_id=X
//   - PUT /api/timesheet/cell  body: { employee_id, work_date, company_id, hours }
//
// При смене компании в слоте — два запроса (удалить старый, создать новый).
// При hours=0 — слот удаляется (бэк удаляет запись).

import { Fragment, memo, useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../store/auth';
import { toast } from '../store/toasts';
import { timesheetApi } from '../api/timesheet';
import { apiClient } from '../api/client';
import { listDepartments } from '../api/departments';
import { companyColorByIndex } from '../utils/colors';
import { companyLabel } from '../utils/companies';
import { ABSENCE_KINDS, absenceMeta } from '../utils/absences';
import { useTimesheetViewStore, type DeptChoice } from '../store/timesheetView';
import { usePeriodStore } from '../store/period';
import { usePersistentState } from '../hooks/usePersistentState';
import { UI_KEYS } from '../utils/persist';
import { TimesheetCompanyView } from './TimesheetCompanyView';
import { ApplicationsPanel } from '../components/ApplicationsPanel';
import type { AbsenceKind, ApplicationsDistributionRow, AutofillPreview, DepartmentApplications, NightFund, NightShift } from '../types/api';

// ──────────────────────────────────────────────────────────────
// Типы (минимальные, чтобы не зависеть от уточнений в api.ts)
// ──────────────────────────────────────────────────────────────
export type Employee = {
  id: number;
  tab_number?: string | null;
  full_name: string;
  department_id: number | null;
  department?: { id: number; name: string } | null;
  schedule_id: number | null;
  schedule?: { id: number; name: string; hours_per_shift: number } | null;
  default_company_id: number | null;
  is_active: boolean;
  is_system_admin?: boolean;
  dismissal_date?: string | null;
  loan_amount?: string | null;
  loan_term_months?: number | null;
  loan_start_date?: string | null;
};

/**
 * Рабочее место сотрудника (task_positions ч.B). Совместитель = несколько
 * позиций, у каждой свои должность, график, отдел, компания и расчёт.
 * `id === 0` — синтетическая позиция для сотрудника, которому бэк ничего не
 * отдал: строка всё равно нужна, часы уйдут на основную (position_id не шлём).
 */
export type Position = {
  id: number;
  employee_id: number;
  title: string | null;
  display_title: string;
  is_primary: boolean;
  department_id: number | null;
  department?: { id: number; name: string } | null;
  schedule_id: number | null;
  schedule?: { id: number; name: string; hours_per_shift: number } | null;
  company_id: number | null;
  /** можно ли отмечать этому месту выходы в ночь: только тогда под строкой
   *  появляется строка «Ночные» (task_night_shifts_rework) */
  has_night_shifts?: boolean;
};

/** Строка табеля = сотрудник × его позиция; ФИО объединяется через rowspan. */
export type PositionRow = {
  emp: Employee;
  position: Position;
  /** индекс позиции внутри сотрудника и общее их число — для rowspan */
  index: number;
  count: number;
};

export type Adjustment = {
  id: number;
  employee_id: number;
  /** к какому рабочему месту относится премия/KPI/аванс */
  position_id: number | null;
  year: number;
  month: number;
  kind: 'premium' | 'kpi' | 'advance';
  amount: string;
  reason: string;
};

export type Company = {
  id: number;
  code: string;
  name: string;
  /** короткое имя с бэка (short_name → name без правовой формы → код) */
  display_name?: string;
};

export type Absence = {
  employee_id: number;
  work_date: string; // 'YYYY-MM-DD'
  kind: AbsenceKind;
  code: string;
  over_limit?: boolean; // больничный сверх годового лимита — за свой счёт
};

export type TimesheetEntry = {
  employee_id: number;
  /** рабочее место, на которое отработаны часы; null — заведено до позиций */
  position_id?: number | null;
  work_date: string; // 'YYYY-MM-DD'
  company_id: number;
  hours: number | string; // decimal на бэке -> может прилететь строкой
};

export type DayType = 'work' | 'short' | 'holiday' | 'weekend';

export type CompanyBreakdown = {
  company_id: number;
  company_code: string;
  company_name?: string;
  hours: string;
  percent?: string;
  base_amount?: string;
  overtime_amount?: string;
  off_schedule_amount?: string;
  holiday_amount?: string;
  overtime_hours?: string;
  off_schedule_hours?: string;
  holiday_hours?: string;
  total: string;
};

export type EmployeePayroll = {
  employee_id: number;
  /** строка расчёта = ПОЗИЦИЯ: employee_id у совместителя повторяется */
  position_id?: number | null;
  position_title?: string | null;
  is_primary_position?: boolean;
  total_hours: string;
  norm_hours: string | null;
  /** плановых рабочих дней (смен) месяца по графику позиции */
  norm_days?: number | null;
  fact_days?: number;
  delta_hours: string | null;
  /** Часы по категориям — их видит и табельщик, деньги ему бэк не отдаёт */
  overtime_hours?: string;
  /** выход в свой выходной по графику */
  off_schedule_hours?: string;
  /** работа в нерабочий праздничный день календаря */
  holiday_hours?: string;
  /** salary — оклад; per_shift — смены × ставка */
  pay_type?: 'salary' | 'per_shift' | 'hourly';
  shift_rate?: string | null;
  worked_shifts?: number;
  norm_shifts?: number | null;
  /** смены в базе посменного: плановые дни графика (выходные/праздники — по коэффициенту) */
  base_shifts?: number;
  base_amount: string;
  overtime_amount: string;
  /** выход в свой выходной по графику */
  off_schedule_amount: string;
  /** работа в нерабочий праздничный день календаря */
  holiday_amount: string;
  total_amount: string;
  /** ночные смены: число отмеченных, ставка фонда отдела и надбавка = смены × ставка */
  night_shifts?: number;
  night_rate?: string | null;
  night_amount?: string;
  vacation_days?: number;
  unpaid_days?: number;
  sick_days?: number;
  absent_days?: number;
  vacation_paid_days?: number;
  sick_paid_days?: number;
  vacation_amount?: string;
  sick_amount?: string;
  sick_limit_days?: number;
  sick_days_used_before?: number;
  sick_unpaid_days?: number;
  sick_limit_remaining?: number;
  weekend_pay_type?: 'coefficient' | 'fixed_rate' | null;
  weekend_coefficient?: string | null;
  weekend_fixed_rate?: string | null;
  holiday_pay_type?: 'coefficient' | 'fixed_rate' | null;
  holiday_coefficient?: string | null;
  holiday_fixed_rate?: string | null;
  premium_amount?: string;
  kpi_amount?: string;
  advance_deduction?: string;
  loan_deduction?: string;
  loan_remaining?: string;
  loan_planned_deduction?: string;
  loan_is_manual?: boolean;
  total_deductions?: string;
  net_payout?: string;         // округлено вниз до 100 ₽
  net_payout_exact?: string;
  rounding_tail?: string;
  breakdown_by_company: CompanyBreakdown[];
  is_calculable: boolean;
  reason_if_not_calculable: string | null;
};

type PayrollSummary = {
  employees: EmployeePayroll[];
  total_hours: string;
  total_base_amount: string;
  total_overtime_amount: string;
  total_off_schedule_amount?: string;
  total_holiday_amount: string;
  total_vacation_amount?: string;
  total_sick_amount?: string;
  /** надбавка за ночные смены за месяц (входит в grand_total) */
  total_night_amount?: string;
  grand_total: string;
  total_premium?: string;
  total_kpi?: string;
  total_deductions?: string;
  total_net_payout?: string;
  total_net_payout_exact?: string;
  total_rounding_tail?: string;
};

export type Period = {
  id: number;
  department_id: number | null;
  department_name: string | null;
  status: 'draft' | 'pending_review' | 'closed';
  can_edit: boolean;
  can_submit: boolean;
  can_close: boolean;
  can_return: boolean;
  can_reopen: boolean;
};

export type MonthResponse = {
  year: number;
  month: number;
  employees: Employee[];
  companies: Company[];
  entries: TimesheetEntry[];
  /** видимые актору рабочие места по сотрудникам (табель отдела — только его) */
  positions_by_employee?: Record<number, Position[]>;
  absences?: Absence[];
  /** отметки выходов в ночь (task_night_shifts_rework) */
  night_shifts?: NightShift[];
  /** фонд ночных смен по отделам: ставка, лимит смен и остаток */
  night_funds?: NightFund[];
  /** заявки на подбор отделов с флагом «распределение по заявкам» */
  applications?: DepartmentApplications[];
  /** суммы распределения по юрлицам для строк таких отделов (считает бэк) */
  applications_distribution?: ApplicationsDistributionRow[];
  /** доп. юрлица сотрудника (кроме основного), где у него есть часы */
  extra_companies_by_employee?: Record<number, number[]>;
  payroll: PayrollSummary | null;
  periods: Period[];
  adjustments?: Adjustment[];
};

type CalendarSummary = {
  days: Array<{ day: number; type: DayType; weekday: number }>;
};

// ──────────────────────────────────────────────────────────────
// Позиции: ключи индексов и запасной вариант
//
// Строка табеля = сотрудник × позиция. Ключи часов/расчёта/премий содержат
// position_id, поэтому у совместителя данные одного рабочего места не текут
// в другое. Всё, что связано с позициями, собрано здесь — иначе формулу ключа
// пришлось бы держать в голове в трёх файлах.
// ──────────────────────────────────────────────────────────────

/**
 * Пересчитать index/count для rowspan ФИО внутри уже отобранного списка строк.
 * Считать их заранее нельзя: позиции одного человека в разных отделах попадают
 * в разные группы, и объединять их одной ячейкой не получится.
 */
export function withSpans(rows: PositionRow[]): PositionRow[] {
  const counts = new Map<number, number>();
  for (const r of rows) counts.set(r.emp.id, (counts.get(r.emp.id) ?? 0) + 1);
  const seen = new Map<number, number>();
  return rows.map((r) => {
    const index = seen.get(r.emp.id) ?? 0;
    seen.set(r.emp.id, index + 1);
    return { ...r, index, count: counts.get(r.emp.id) ?? 1 };
  });
}

/** Позиция, которую можно отправить на бэк; 0 — синтетическая (основная). */
export function positionIdParam(position: Position | null | undefined): number | undefined {
  return position && position.id > 0 ? position.id : undefined;
}

export function posKey(employeeId: number, positionId: number | null | undefined): string {
  return `${employeeId}:${positionId ?? 0}`;
}

/**
 * Рабочее место, к которому относится ячейка. `position_id IS NULL` — строка,
 * заведённая до появления позиций: она принадлежит ОСНОВНОЙ (так же её
 * разрешает `_resolve_position_id` на бэке). Нужна при локальном патче
 * состояния, где `primaryPositionIdByEmp` недоступен: значение берётся из
 * того же снимка `data`, который правим.
 */
/** Один поповер на страницу вместо <select> в каждой ячейке (см. openCompanyPicker). */
type PickerItem = {
  key: string; label: string; hint?: string; color?: string; active?: boolean;
};
type PickerState = {
  x: number; y: number; title: string;
  items: PickerItem[];
  onPick: (key: string) => void;
};

/** Общая ссылка на пустой список слотов: новый [] на каждый рендер ломал бы memo. */
const EMPTY_SLOTS: TimesheetEntry[] = [];

function effectivePositionId(
  positionId: number | null | undefined,
  employeeId: number,
  positionsByEmployee: Record<number, Position[]> | undefined,
): number | null {
  if (positionId != null) return positionId;
  const list = positionsByEmployee?.[employeeId] ?? [];
  const primary = list.find((p) => p.is_primary) ?? list[0];
  return primary?.id ?? null;
}

/**
 * Дописать юрлицо в `extra_companies_by_employee` после появления часов по
 * нему. Обычно это делал ответ месяца; при локальном патче список надо
 * поддержать самим, иначе новая компания не получит строку в виде
 * «по компаниям». Порядок — возрастающий, как отдаёт бэк.
 */
function withExtraCompany(
  prev: MonthResponse, employeeId: number, companyId: number,
): Record<number, number[]> {
  const map = prev.extra_companies_by_employee ?? {};
  const emp = prev.employees.find((e) => e.id === employeeId);
  if (emp?.default_company_id === companyId) return map;
  const current = map[employeeId] ?? [];
  if (current.includes(companyId)) return map;
  return { ...map, [employeeId]: [...current, companyId].sort((a, b) => a - b) };
}

/**
 * Позиция для сотрудника, которому бэк её не отдал (старый ответ, employee-роль):
 * строка всё равно нужна, а часы уходят на основную — ровно как до части B.
 */
function syntheticPosition(emp: Employee): Position {
  return {
    id: 0,
    employee_id: emp.id,
    title: null,
    display_title: '—',
    is_primary: true,
    department_id: emp.department_id,
    department: emp.department ?? null,
    schedule_id: emp.schedule_id,
    schedule: emp.schedule ?? null,
    company_id: emp.default_company_id,
  };
}

// ──────────────────────────────────────────────────────────────
// Палитра цветов компаний — общая с дашбордом (utils/colors.ts)
// ──────────────────────────────────────────────────────────────
function getCompanyColor(companyId: number, companies: Company[]) {
  return companyColorByIndex(companies.findIndex((c) => c.id === companyId));
}

// ──────────────────────────────────────────────────────────────
// Утилиты дат и форматов
// ──────────────────────────────────────────────────────────────
const MONTH_NAMES_RU = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

const WEEKDAY_RU = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

// Через сколько после последней правки часов пересчитывать суммы. Серия правок
// подряд (обычный ввод табеля) должна гонять расчёт один раз, а не на каждую цифру.
const PAYROLL_REFRESH_DELAY_MS = 1200;

// ──────────────────────────────────────────────────────────────
// DepartmentGate — выбор отдела перед загрузкой табеля
// ──────────────────────────────────────────────────────────────
// Табель отдела — это десятки строк и расчёт по ним; табель всех отделов при 200
// сотрудниках — сотни строк и полный расчёт ЗП. Поэтому отдел выбирается явно,
// а «все отделы» остаются отдельным пунктом для сводных итогов.
function DepartmentGate({
  departments,
  allLabel,
  onPick,
}: {
  departments: { id: number; name: string }[];
  allLabel: string;
  onPick: (choice: DeptChoice) => void;
}) {
  return (
    <div className="p-8">
      <h2 className="text-lg font-semibold text-gray-800">Выберите отдел</h2>
      <p className="mt-1 text-sm text-gray-500">
        Табель открывается по одному отделу — так он грузится быстро и в нём
        удобнее вводить часы.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        {departments.map((d) => (
          <button
            key={d.id}
            type="button"
            onClick={() => onPick(d.id)}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-800 hover:border-blue-500 hover:bg-blue-50"
          >
            {d.name}
          </button>
        ))}
      </div>

      {departments.length === 0 && (
        <p className="mt-4 text-sm text-gray-500">
          Отделы не заведены — откройте табель целиком.
        </p>
      )}

      <button
        type="button"
        onClick={() => onPick('all')}
        className="mt-6 block text-sm text-gray-500 underline decoration-dotted hover:text-blue-600"
        title="Все сотрудники сразу: строк больше, загрузка дольше. Нужно для сводных итогов."
      >
        {allLabel} — со сводными итогами
      </button>
    </div>
  );
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

function pad2(n: number): string {
  return n < 10 ? '0' + n : String(n);
}

function dateStr(year: number, month: number, day: number): string {
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

function jsWeekdayMonFirst(year: number, month: number, day: number): number {
  // 0=Пн, 6=Вс
  const js = new Date(year, month - 1, day).getDay();
  return js === 0 ? 6 : js - 1;
}

function num(value: string | number | null | undefined, fallback = 0): number {
  if (value === null || value === undefined || value === '') return fallback;
  const n = typeof value === 'string' ? parseFloat(value) : value;
  return Number.isFinite(n) ? n : fallback;
}

function fmtHours(value: number): string {
  if (value === 0) return '';
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(1).replace(/\.0$/, '');
}

function fmtMoney(value: string | null): string {
  if (value === null) return '—';
  const n = num(value);
  if (n === 0) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(n) + ' ₽';
}

// Подсказка для колонок отпускных/больничных: сколько дней отмечено и сколько
// из них оплачено (код на выходном отмечается, но не оплачивается — нормы нет).
function absenceDaysTitle(label: string, days?: number, paidDays?: number): string {
  if (!days) return `${label}: нет`;
  const paid = paidDays ?? days;
  const suffix = paid === days ? '' : ` (оплачено рабочих: ${paid})`;
  return `${label}: ${days} дн.${suffix}`;
}

// Подсказка по годовому лимиту больничного: сколько израсходовано за год и
// сколько дней месяца ушло за свой счёт (лимит считается с 1 января).
export function sickLimitTitle(pay?: EmployeePayroll | null): string {
  if (!pay) return 'Больничный: нет';
  const limit = pay.sick_limit_days ?? 0;
  if (!limit) return absenceDaysTitle('Больничный', pay.sick_days, pay.sick_paid_days);
  const used = (pay.sick_days_used_before ?? 0) + (pay.sick_paid_days ?? 0);
  const lines = [
    `Больничный: ${pay.sick_days ?? 0} дн. в месяце, оплачено ${pay.sick_paid_days ?? 0}`,
    `Годовой лимит: использовано ${used}/${limit}, остаток ${pay.sick_limit_remaining ?? 0}`,
  ];
  if (pay.sick_unpaid_days) {
    lines.push(`Сверх лимита (за свой счёт): ${pay.sick_unpaid_days} дн.`);
  }
  return lines.join('\n');
}

// Коэффициент/режим оплаты выходных сотрудника — для колонки «Коэф.» (п.3)
export function fmtCoeff(pay?: EmployeePayroll | null): string {
  if (!pay) return '—';
  // У посменного все смены равнозначны — коэффициент выходных не применяется.
  if (pay.pay_type === 'per_shift') return '—';
  if (pay.weekend_pay_type === 'fixed_rate') {
    const r = num(pay.weekend_fixed_rate ?? null);
    return r > 0 ? `${new Intl.NumberFormat('ru-RU').format(r)}₽/ч` : '—';
  }
  const c = pay.weekend_coefficient != null ? num(pay.weekend_coefficient) : 1.5;
  return `×${c}`;
}

// ──────────────────────────────────────────────────────────────
// Основной компонент
// ──────────────────────────────────────────────────────────────
export function TimesheetPage() {
  const user = useAuthStore((s: any) => s.user);
  const role: string | null = user?.role ?? null;
  // Табельщик (task_timekeeper_role) ведёт часы, но денег не видит — бэк ему
  // payroll и не отдаёт, так что колонки было бы нечем заполнить.
  const canSeeMoney = role === 'admin' || role === 'accountant' || role === 'manager';
  // Табельщик ведёт время, поэтому ЧАСЫ он видит все — норму, Δ, переработку,
  // часы вне графика и праздничные, дни отпуска и больничного. Деньги считает и
  // вычищает бэк (mask_payroll_summary), фронт лишь не рисует денежные колонки.
  const canSeeHourStats = canSeeMoney || role === 'timekeeper';
  // Т-13 — только часы, поэтому выгрузка доступна и табельщику.
  const canExport =
    role === 'admin' || role === 'accountant' || role === 'manager' || role === 'timekeeper';
  // Селектор отделов: admin/accountant всегда, manager и timekeeper — если у них
  // несколько отделов (task_org_structure ч.2). С одним отделом выбирать нечего.
  const managedDeptCount: number = user?.managed_department_ids?.length ?? 0;
  const isDeptScoped = role === 'manager' || role === 'timekeeper';
  const canSelectDept =
    role === 'admin' || role === 'accountant' || (isDeptScoped && managedDeptCount > 1);

  const viewMode = useTimesheetViewStore((s) => s.mode);
  const setViewMode = useTimesheetViewStore((s) => s.setMode);

  // ── Ссылка из «Задач»/дашборда (?year=&month=&department_id=) ──
  // Она сильнее сохранённого выбора: человек перешёл в конкретный месяц отдела,
  // открыть надо именно его. Применяется СИНХРОННО на первом рендере (useState
  // вместо useEffect и ДО чтения сторов ниже): из эффекта первый запрос ушёл бы
  // за прежний месяц/отдел, а его ответ мог прийти позже правильного и затереть
  // его — гонка на ровном месте.
  const [searchParams] = useSearchParams();
  useState(() => {
    const y = parseInt(searchParams.get('year') ?? '', 10);
    const m = parseInt(searchParams.get('month') ?? '', 10);
    const validY = y >= 2000 && y <= 2100 ? y : null;
    const validM = m >= 1 && m <= 12 ? m : null;
    if (validY !== null || validM !== null) {
      const period = usePeriodStore.getState();
      period.setPeriod(validY ?? period.year, validM ?? period.month);
    }
    const dept = parseInt(searchParams.get('department_id') ?? '', 10);
    if (Number.isFinite(dept)) useTimesheetViewStore.getState().setDeptChoice(dept);
    return null;
  });

  // ── Период: общий с «Расчёт ЗП» и сохранённый (task_ux_improvements ч.3) ──
  const year = usePeriodStore((s) => s.year);
  const month = usePeriodStore((s) => s.month);
  const setYear = usePeriodStore((s) => s.setYear);
  const setMonth = usePeriodStore((s) => s.setMonth);
  // ── Выбор отдела: при 200 сотрудниках «все отделы» по умолчанию не грузим ──
  // deptChoice: id | 'all' | null(не выбрано). Тем, у кого выбора нет (employee,
  // руководитель/табельщик одного отдела), сразу ставим 'all' — бэк и так отдаёт
  // только их людей, спрашивать нечего.
  const deptChoice = useTimesheetViewStore((s) => s.deptChoice);
  const setDeptChoice = useTimesheetViewStore((s) => s.setDeptChoice);

  // Параметр для бэка: 'all' и «не выбрано» — это отсутствие фильтра.
  const departmentFilter = typeof deptChoice === 'number' ? deptChoice : null;
  // Спрашиваем отдел только у того, у кого есть из чего выбирать: employee видит
  // себя, руководитель/табельщик одного отдела — свой отдел, спрашивать нечего.
  const needsDeptChoice = canSelectDept && deptChoice === null;
  const deptChosen = !needsDeptChoice;
  // Поиск по ФИО/таб.№ и фильтр компании — как на «Расчёт ЗП»: фильтруют строки
  // поверх фильтра отдела, на бэк не ходят.
  // Сохраняются вместе (один ключ — один объект): фильтры осмыслены только
  // в паре, и восстанавливать их порознь незачем.
  const [filters, setFilters] = usePersistentState(
    UI_KEYS.timesheetFilters,
    { search: '', companyId: null as number | null },
    (v) => typeof v === 'object' && v !== null && 'search' in v,
  );
  const { search } = filters;
  const companyFilter = filters.companyId;
  const setSearch = (value: string) => setFilters((f) => ({ ...f, search: value }));
  const setCompanyFilter = (value: number | null) =>
    setFilters((f) => ({ ...f, companyId: value }));

  const [data, setData] = useState<MonthResponse | null>(null);
  const [calendar, setCalendar] = useState<CalendarSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  // Сводка «По компаниям» в подвале: при 8 юрлицах занимала пол-экрана
  // (в основном прочерками), поэтому по умолчанию свёрнута. Развернул —
  // остаётся развёрнутой и после перехода в другой раздел.
  const [companySummaryOpen, setCompanySummaryOpen] = usePersistentState(
    UI_KEYS.timesheetCompanySummary, false, (v) => typeof v === 'boolean',
  );
  const [autofillPreview, setAutofillPreview] = useState<AutofillPreview | null>(null);
  const [autofillLoading, setAutofillLoading] = useState(false);
  // Суммы на экране относятся к состоянию ДО последней правки часов и ждут
  // пересчёта (см. afterEdit). Показываем это явно, чтобы бухгалтер не сверял
  // несходящиеся цифры.
  const [payrollStale, setPayrollStale] = useState(false);
  const payrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Актуальные данные для колбэков, которые обязаны быть СТАБИЛЬНЫМИ: любая
  // зависимость от `data` пересоздаёт колбэк на каждую правку, а вместе с ним
  // рушится React.memo на ячейках дней (task_timesheet_perf2).
  const dataRef = useRef<MonthResponse | null>(null);
  dataRef.current = data;

  // ── Загрузка данных ──
  // Часы и деньги грузятся раздельно: расчёт ЗП — самая дорогая часть ответа, а
  // при вводе часов он не нужен немедленно. Поэтому правка ячейки перечитывает
  // только часы (быстро), а суммы пересчитываются с задержкой — см. afterEdit.
  // Возвращает признак успеха: по нему решается, снимать ли пометку «суммы
  // пересчитываются». Если запрос упал, суммы так и остались старыми — гасить
  // индикатор нельзя, иначе устаревшие цифры выглядят как актуальные.
  const fetchMonth = useCallback(
    async (withPayroll: boolean): Promise<boolean> => {
      if (!deptChosen) {
        setData(null);
        return false;
      }
      setLoading(true);
      try {
        const [monthData, cal] = await Promise.all([
          timesheetApi.getMonth(year, month, {
            department_id: departmentFilter ?? undefined,
            include_payroll: withPayroll && canSeeHourStats,
          }) as Promise<MonthResponse>,
          apiClient.get<CalendarSummary>(`/api/calendar/${year}/${month}/summary`)
            .then(r => r.data)
            .catch(() => ({ days: [] } as CalendarSummary)),
        ]);
        // Без расчёта бэк присылает payroll=null — оставляем прежние суммы, иначе
        // денежные колонки мигали бы пустотой на каждую введённую цифру. Что они
        // пока не пересчитаны, показывает индикатор payrollStale.
        setData((prev) =>
          withPayroll || !prev
            ? monthData
            : {
                ...monthData,
                payroll: prev.payroll,
                // Суммы распределения считаются вместе с расчётом: без него бэк
                // присылает пустой список, и колонки «Распределение» мигали бы
                // прочерками на каждую введённую цифру.
                applications_distribution: prev.applications_distribution,
              }
        );
        setCalendar(cal);
        return true;
      } catch (err: any) {
        toast.error('Ошибка загрузки табеля: ' + (err?.message ?? err));
        return false;
      } finally {
        setLoading(false);
      }
    },
    [year, month, departmentFilter, canSeeHourStats, deptChosen]
  );

  // Полная перезагрузка (смена месяца/отдела, workflow периода).
  const reload = useCallback(async () => {
    if (payrollTimer.current) clearTimeout(payrollTimer.current);
    setPayrollStale(false);
    await fetchMonth(true);
  }, [fetchMonth]);

  // Пересчёт ТОЛЬКО сумм, без перечитывания месяца. `/payroll` отдаёт один
  // расчёт (на отдел в 70 человек — 120 КБ против 440 КБ у месяца), и, что
  // важнее, entries/employees/companies сохраняют идентичность — мемоизированные
  // ячейки дней не перерисовываются вовсе.
  // Два исключения, где нужен весь месяц:
  //   • табельщик — на `/payroll` у него 403 (это финансовый эндпойнт), часы
  //     он получает из ответа месяца с вычищенными деньгами;
  //   • отделы «по заявкам» — суммы распределения считаются только вместе с
  //     месяцем, отдельного эндпойнта у них нет.
  const refreshPayroll = useCallback(async (): Promise<boolean> => {
    if (!deptChosen || !canSeeHourStats) return false;
    if (!canSeeMoney) return fetchMonth(true);
    if ((dataRef.current?.applications ?? []).length > 0) return fetchMonth(true);
    try {
      const payroll = await timesheetApi.getPayroll(
        year, month, departmentFilter ?? undefined,
      );
      setData((prev) => (prev ? { ...prev, payroll } : prev));
      return true;
    } catch (err: any) {
      toast.error('Не удалось пересчитать суммы: ' + (err?.message ?? err));
      return false;
    }
  }, [year, month, departmentFilter, canSeeMoney, canSeeHourStats, deptChosen, fetchMonth]);

  // Премия / KPI / аванс / правка займа: перечитываем ТОЛЬКО список начислений
  // и суммы. Раньше здесь стоял reload() — полный месяц с расчётом (на «всех
  // отделах» это 1,9 МБ и секунда сервера) плюс полная перерисовка таблицы.
  const refreshAdjustments = useCallback(async () => {
    if (!canSeeMoney) return;
    try {
      const adjustments = await timesheetApi.getAdjustments(
        year, month, departmentFilter ?? undefined,
      );
      setData((prev) => (prev ? { ...prev, adjustments } : prev));
    } catch (err: any) {
      toast.error('Не удалось обновить начисления: ' + (err?.message ?? err));
    }
  }, [year, month, departmentFilter, canSeeMoney]);

  const afterAdjustment = useCallback(async () => {
    await refreshAdjustments();
    await refreshPayroll();
  }, [refreshAdjustments, refreshPayroll]);

  // После правки часов месяц НЕ перечитывается: состояние правится локально
  // ответом бэка (patchEntry / patchAbsence ниже). Раньше здесь стоял
  // fetchMonth(false) — на отдел в 70 человек это 400 КБ несжатого JSON и
  // полная перерисовка таблицы на КАЖДУЮ введённую цифру.
  // Остаётся только отложенный пересчёт сумм, чтобы серия правок не гоняла
  // расчёт по всему отделу на каждую цифру.
  const afterEdit = useCallback(() => {
    if (!canSeeHourStats) return;
    setPayrollStale(true);
    if (payrollTimer.current) clearTimeout(payrollTimer.current);
    payrollTimer.current = setTimeout(() => {
      // Пометку снимаем только при успехе: после ошибки суммы остались старыми
      refreshPayroll().then((ok) => { if (ok) setPayrollStale(false); });
    }, PAYROLL_REFRESH_DELAY_MS);
  }, [refreshPayroll, canSeeHourStats]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Таймер пересчёта не должен пережить уход с экрана
  useEffect(() => () => {
    if (payrollTimer.current) clearTimeout(payrollTimer.current);
  }, []);

  // ── Тип дня (рабочий/праздник/сокращённый/выходной) ──
  const dayTypes = useMemo(() => {
    const map: Record<number, DayType> = {};
    const numDays = daysInMonth(year, month);
    for (let d = 1; d <= numDays; d++) {
      const fromCal = calendar?.days?.find((x) => x.day === d);
      if (fromCal) {
        map[d] = fromCal.type;
      } else {
        const wd = jsWeekdayMonFirst(year, month, d);
        map[d] = wd >= 5 ? 'weekend' : 'work';
      }
    }
    return map;
  }, [calendar, year, month]);

  // ── Позиции сотрудников (task_positions ч.B) ──
  // Бэк отдаёт только ВИДИМЫЕ актору рабочие места: в табеле отдела — позиции
  // этого отдела, менеджеру — только его отделов. Сотрудник с одной позицией
  // даёт одну строку, как и раньше.
  const positionsByEmp = useMemo(() => {
    const map = new Map<number, Position[]>();
    if (!data) return map;
    const raw = data.positions_by_employee ?? {};
    for (const emp of data.employees) {
      const list = raw[emp.id] ?? [];
      map.set(emp.id, list.length > 0 ? list : [syntheticPosition(emp)]);
    }
    return map;
  }, [data]);

  // Позиция, к которой относятся часы: строки без position_id заведены до
  // появления позиций и принадлежат основной (иначе они бы просто исчезли).
  const primaryPositionIdByEmp = useMemo(() => {
    const map = new Map<number, number>();
    for (const [empId, list] of positionsByEmp) {
      const primary = list.find((p) => p.is_primary) ?? list[0];
      if (primary) map.set(empId, primary.id);
    }
    return map;
  }, [positionsByEmp]);

  const entryPositionId = useCallback(
    (e: TimesheetEntry): number | undefined =>
      e.position_id ?? primaryPositionIdByEmp.get(e.employee_id),
    [primaryPositionIdByEmp]
  );

  // ── Индекс entries: `empId:posId:day` → слоты компаний этого дня ──
  // Слоты внутри дня упорядочены по настроенному порядку юрлиц: раньше порядок
  // повторял выдачу бэка, и после локальной правки (patchEntry дописывает
  // запись в конец) чипы в ячейке могли поменяться местами. Заодно порядок
  // делает сравнение `sameSlots` в DayCell устойчивым.
  const companyRank = useMemo(() => {
    const map = new Map<number, number>();
    (data?.companies ?? []).forEach((c, i) => map.set(c.id, i));
    return map;
  }, [data]);

  const entriesByPosDay = useMemo(() => {
    const map = new Map<string, TimesheetEntry[]>();
    if (!data) return map;
    for (const e of data.entries) {
      const day = parseInt(e.work_date.slice(-2), 10);
      const key = `${posKey(e.employee_id, entryPositionId(e))}:${day}`;
      const arr = map.get(key);
      if (arr) arr.push(e);
      else map.set(key, [e]);
    }
    for (const arr of map.values()) {
      if (arr.length > 1) {
        arr.sort((a, b) =>
          (companyRank.get(a.company_id) ?? 0) - (companyRank.get(b.company_id) ?? 0));
      }
    }
    return map;
  }, [data, entryPositionId, companyRank]);

  // ── Индекс отсутствий: `empId:day` → код дня (в дне либо часы, либо код) ──
  const absenceByEmpDay = useMemo(() => {
    const map = new Map<string, Absence>();
    for (const a of data?.absences ?? []) {
      const day = parseInt(a.work_date.slice(-2), 10);
      map.set(`${a.employee_id}:${day}`, a);
    }
    return map;
  }, [data]);

  // ── Ночные смены: `empId:posId:day` → отмечена ли ночь ──
  // Ключ с позицией, потому что флаг «ночные смены» и отдел (а с ним фонд и
  // ставка) — свойства рабочего места, а не человека.
  const nightByPosDay = useMemo(() => {
    const set = new Set<string>();
    for (const n of data?.night_shifts ?? []) {
      const day = parseInt(n.work_date.slice(-2), 10);
      set.add(`${posKey(n.employee_id, n.position_id)}:${day}`);
    }
    return set;
  }, [data]);

  // Фонд ночных смен отдела: ставка, лимит и остаток (индикатор + подсказки).
  const nightFundByDept = useMemo(() => {
    const map = new Map<number, NightFund>();
    for (const f of data?.night_funds ?? []) map.set(f.department_id, f);
    return map;
  }, [data]);

  // Расчёт — строка на ПОЗИЦИЮ: ключ `empId:posId`, иначе у совместителя
  // вторая позиция затёрла бы первую.
  const payrollByPos = useMemo(() => {
    const map = new Map<string, EmployeePayroll>();
    if (!data?.payroll) return map;
    for (const p of data.payroll.employees) {
      map.set(posKey(p.employee_id, p.position_id), p);
    }
    return map;
  }, [data]);

  const payrollFor = useCallback(
    (emp: Employee, position: Position): EmployeePayroll | undefined =>
      payrollByPos.get(posKey(emp.id, positionIdParam(position)))
      // Синтетическая позиция: расчёт пришёл на основную, id которой мы не знаем
      ?? (position.id === 0
        ? data?.payroll?.employees.find((p) => p.employee_id === emp.id)
        : undefined),
    [payrollByPos, data]
  );

  // Премии/KPI/авансы адресованы конкретному рабочему месту (задача 3.11a + позиции)
  const adjByPos = useMemo(() => {
    const map = new Map<string, Adjustment[]>();
    for (const a of data?.adjustments ?? []) {
      const pid = a.position_id ?? primaryPositionIdByEmp.get(a.employee_id);
      const key = posKey(a.employee_id, pid);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(a);
    }
    return map;
  }, [data, primaryPositionIdByEmp]);

  // Открытый модал: рабочее место + категория (своя кнопка в каждом столбце)
  const [adjModal, setAdjModal] = useState<
    { emp: Employee; position: Position; category: 'premium' | 'kpi' | 'deduction' } | null
  >(null);

  // ── Видимые сотрудники (бэк уже исключил системных админов и применил видимость) ──
  const visibleEmployees = useMemo(() => {
    if (!data) return [] as Employee[];
    return data.employees.filter((e) => !e.is_system_admin);
  }, [data]);

  const visibleEmpIds = useMemo(
    () => new Set(visibleEmployees.map((e) => e.id)),
    [visibleEmployees]
  );

  // Компании, где у сотрудника есть часы в месяце — для фильтра по компании.
  const companiesByEmp = useMemo(() => {
    const map = new Map<number, Set<number>>();
    for (const e of data?.entries ?? []) {
      const set = map.get(e.employee_id);
      if (set) set.add(e.company_id);
      else map.set(e.employee_id, new Set([e.company_id]));
    }
    return map;
  }, [data]);

  // ── Поиск (ФИО / таб.№) + фильтр компании ──
  // Логика та же, что на «Расчёт ЗП»: поиск по ФИО ИЛИ табельному номеру,
  // компания — где у сотрудника есть доля (в табеле: есть часы) либо основная.
  // Чистая навигация поверх уже загруженных данных: фильтруются только СТРОКИ.
  // Итоги (ИТОГО, по компаниям, dayTotals) намеренно продолжают считаться по
  // всем видимым сотрудникам — иначе фильтр молча менял бы суммы месяца.
  // Что итоги шире выборки, видно по счётчику «найдено N из M» в шапке.
  const searchNeedle = search.trim().toLocaleLowerCase('ru');
  const filtersActive = searchNeedle !== '' || companyFilter !== null;
  const shownEmployees = useMemo(() => {
    if (!filtersActive) return visibleEmployees;
    return visibleEmployees.filter((e) => {
      if (searchNeedle) {
        const hay = `${e.full_name} ${e.tab_number ?? ''}`.toLocaleLowerCase('ru');
        if (!hay.includes(searchNeedle)) return false;
      }
      if (companyFilter !== null) {
        const hasHours = companiesByEmp.get(e.id)?.has(companyFilter) ?? false;
        if (!hasHours && e.default_company_id !== companyFilter) return false;
      }
      return true;
    });
  }, [visibleEmployees, searchNeedle, companyFilter, companiesByEmp, filtersActive]);

  // ── Строки табеля: сотрудник × позиция (task_positions ч.B) ──
  // Одна позиция = одна строка, как было до совместительства.
  const shownRows = useMemo(() => {
    const rows: PositionRow[] = [];
    for (const emp of shownEmployees) {
      const list = positionsByEmp.get(emp.id) ?? [];
      list.forEach((position, index) => {
        rows.push({ emp, position, index, count: list.length });
      });
    }
    return rows;
  }, [shownEmployees, positionsByEmp]);

  // ── Группировка по отделам (Bug 5): только при «Все отделы» для admin/accountant ──
  // Отдел — свойство ПОЗИЦИИ, поэтому группируем строки, а не сотрудников:
  // подработка в другом отделе попадает в свою группу (и под свой период).
  const grouped = canSelectDept && departmentFilter === null;
  const groups = useMemo(() => {
    const byDept = new Map<number | null, PositionRow[]>();
    for (const row of shownRows) {
      const k = row.position.department_id ?? null;
      if (!byDept.has(k)) byDept.set(k, []);
      byDept.get(k)!.push(row);
    }
    const entries = Array.from(byDept.entries());
    entries.sort((a, b) => {
      if (a[0] === null) return 1; // «Без отдела» — в самый низ
      if (b[0] === null) return -1;
      const na = a[1][0]?.position.department?.name ?? '';
      const nb = b[1][0]?.position.department?.name ?? '';
      return na.localeCompare(nb, 'ru');
    });
    return entries.map(([deptId, rows]) => ({
      deptId,
      name: deptId === null
        ? 'Без отдела'
        : rows[0]?.position.department?.name ?? `Отдел ${deptId}`,
      // rowspan считаем внутри группы: позиции одного человека в разных
      // отделах попадают в разные группы, объединить их одной ячейкой нельзя.
      rows: withSpans(rows),
      period: data?.periods.find((p) => p.department_id === deptId) ?? null,
    }));
  }, [shownRows, data]);

  const flatRows = useMemo(() => withSpans(shownRows), [shownRows]);

  // ── Видны ли все периоды в draft? Для autofill / submit ──
  const allEditable = useMemo(() => {
    if (!data?.periods?.length) return true;
    return data.periods.every((p) => p.can_edit);
  }, [data]);

  // ── Список отделов для селектора (стабильный, грузим отдельно от выдачи табеля) ──
  const [departments, setDepartments] = useState<{ id: number; name: string }[]>([]);
  useEffect(() => {
    if (!canSelectDept) return;
    listDepartments()
      .then((list) =>
        setDepartments(list.filter((d) => d.is_active).map((d) => ({ id: d.id, name: d.name })))
      )
      .catch(() => setDepartments([]));
  }, [canSelectDept]);

  // Выбор отдела живёт в сторе и localStorage: переживает смену месяца, смену
  // пользователя в той же вкладке и перезагрузку страницы. Чужой (или
  // удалённый) отдел дал бы 403 и пустой экран — возвращаем к выбору. Пустой
  // список отделов не трогаем: это может быть неудавшаяся загрузка справочника,
  // а не отсутствие доступа.
  useEffect(() => {
    if (!canSelectDept || departments.length === 0) return;
    if (typeof deptChoice === 'number' && !departments.some((d) => d.id === deptChoice)) {
      setDeptChoice(null);
    }
  }, [departments, canSelectDept, deptChoice, setDeptChoice]);

  // ── Локальные патчи состояния после мутации ──
  // Ответ бэка вписывается прямо в `data`; месяц целиком не перечитывается.
  // Правила взаимоисключения ПОВТОРЯЮТ серверные (единственный источник правды —
  // `services/timesheet._upsert_cell_no_commit` и `services/absences.set_absence`):
  // часы дня снимают код отсутствия, но НЕ трогают ночные смены.
  const patchEntry = useCallback(
    (
      employeeId: number, positionId: number | null | undefined,
      workDate: string, companyId: number, saved: TimesheetEntry | null,
    ) => {
      setData((prev) => {
        if (!prev) return prev;
        const posOf = (pid: number | null | undefined) =>
          effectivePositionId(pid, employeeId, prev.positions_by_employee);
        const target = saved?.position_id ?? posOf(positionId);
        const entries = prev.entries.filter((e) => !(
          e.employee_id === employeeId &&
          e.work_date === workDate &&
          e.company_id === companyId &&
          posOf(e.position_id) === target
        ));
        if (saved) entries.push(saved);
        return {
          ...prev,
          entries,
          absences: saved
            ? (prev.absences ?? []).filter(
                (a) => !(a.employee_id === employeeId && a.work_date === workDate))
            : prev.absences,
          extra_companies_by_employee: saved
            ? withExtraCompany(prev, employeeId, companyId)
            : prev.extra_companies_by_employee,
        };
      });
    },
    []
  );

  // ── Действия со слотами ──
  // Часы всегда пишутся на КОНКРЕТНОЕ рабочее место: у совместителя это
  // разные графики, нормы и юрлица (task_positions ч.B). positionId не задан
  // — бэк отнесёт часы к основной позиции, как было до части B.
  const saveSlot = useCallback(
    async (
      employeeId: number, day: number, companyId: number, hours: number,
      positionId?: number,
    ) => {
      const workDate = dateStr(year, month, day);
      try {
        const saved = await timesheetApi.saveCell({
          employee_id: employeeId,
          position_id: positionId ?? null,
          work_date: workDate,
          company_id: companyId,
          hours,
        });
        patchEntry(employeeId, positionId ?? null, workDate, companyId, saved);
        afterEdit();
      } catch (err: any) {
        toast.error('Не удалось сохранить: ' + (err?.message ?? err));
      }
    },
    [year, month, afterEdit, patchEntry]
  );

  // Поставить/снять код отсутствия. Бэк сам удалит часы этого дня —
  // взаимоисключение «часы или код» держится на сервере.
  const setAbsence = useCallback(
    async (employeeId: number, day: number, kind: AbsenceKind | null) => {
      try {
        await timesheetApi.setAbsence({
          employee_id: employeeId,
          work_date: dateStr(year, month, day),
          kind,
        });
        // Здесь месяц перечитывается ЦЕЛИКОМ, в отличие от часов. Причина —
        // флаг `over_limit` (больничный сверх годового лимита): он считается
        // хронологически по ВСЕМУ году, PUT /absence его не возвращает, и
        // правка одного дня может переставить пометку в других месяцах.
        // Коды ставят на порядок реже, чем часы, — цена приемлемая.
        await fetchMonth(false);
        afterEdit();
      } catch (err: any) {
        toast.error('Не удалось сохранить отметку: ' + (err?.message ?? err));
      }
    },
    [year, month, afterEdit, fetchMonth]
  );

  const changeSlotCompany = useCallback(
    async (
      employeeId: number, day: number, oldCompanyId: number, newCompanyId: number,
      hours: number, positionId?: number,
    ) => {
      try {
        const workDate = dateStr(year, month, day);
        const removed = await timesheetApi.saveCell({
          employee_id: employeeId,
          position_id: positionId ?? null,
          work_date: workDate,
          company_id: oldCompanyId,
          hours: 0,
        });
        patchEntry(employeeId, positionId ?? null, workDate, oldCompanyId, removed);
        const saved = await timesheetApi.saveCell({
          employee_id: employeeId,
          position_id: positionId ?? null,
          work_date: workDate,
          company_id: newCompanyId,
          hours,
        });
        patchEntry(employeeId, positionId ?? null, workDate, newCompanyId, saved);
        afterEdit();
      } catch (err: any) {
        toast.error('Не удалось сменить компанию: ' + (err?.message ?? err));
      }
    },
    [year, month, afterEdit, patchEntry]
  );

  // Добавить слот. Принимает ИДЕНТИФИКАТОРЫ, а не объекты, и читает данные из
  // `dataRef` — иначе колбэк зависел бы от `data`, пересоздавался на каждую
  // правку и обнулял React.memo на ячейках дней.
  const addSlotByIds = useCallback(
    (employeeId: number, positionId: number | undefined, day: number) => {
      const snap = dataRef.current;
      if (!snap) return;
      const emp = snap.employees.find((e) => e.id === employeeId);
      if (!emp) return;
      const list = snap.positions_by_employee?.[employeeId] ?? [];
      const position = list.find((p) => p.id === (positionId ?? 0)) ?? list.find((p) => p.is_primary) ?? list[0];
      const workDate = dateStr(year, month, day);
      const target = effectivePositionId(positionId ?? null, employeeId, snap.positions_by_employee);
      const used = new Set(
        snap.entries
          .filter((e) =>
            e.employee_id === employeeId &&
            e.work_date === workDate &&
            effectivePositionId(e.position_id, employeeId, snap.positions_by_employee) === target)
          .map((e) => e.company_id)
      );
      // Компания по умолчанию — основная компания ЭТОГО рабочего места:
      // у совместителя подработку обычно оплачивает другое юрлицо.
      let chosen: Company | undefined;
      const defaultCompanyId = position?.company_id ?? emp.default_company_id;
      if (defaultCompanyId && !used.has(defaultCompanyId)) {
        chosen = snap.companies.find((c) => c.id === defaultCompanyId);
      }
      if (!chosen) {
        chosen = snap.companies.find((c) => !used.has(c.id));
      }
      if (!chosen) {
        toast.info('Нет свободных компаний');
        return;
      }
      // Часы по умолчанию — длительность смены графика ЭТОЙ позиции
      const def = position?.schedule?.hours_per_shift ?? emp.schedule?.hours_per_shift ?? 8;
      saveSlot(employeeId, day, chosen.id, def, positionId);
    },
    [year, month, saveSlot]
  );

  // ── Выпадашки ячеек вынесены в ОДИН поповер на страницу ──
  // Раньше каждая ячейка с часами несла <select> со списком юрлиц, а каждый
  // день — <select> с кодами отсутствия. На отделе в 73 человека это 3994
  // <select> и 27027 <option> (замер препрода) — самый дорогой для браузера
  // элемент. Теперь в ячейке кнопка, а список рисуется только у открытого
  // поповера.
  const [picker, setPicker] = useState<PickerState | null>(null);
  const closePicker = useCallback(() => setPicker(null), []);

  const openCompanyPicker = useCallback(
    (anchor: HTMLElement, currentCompanyId: number, onPick: (companyId: number) => void) => {
      const snap = dataRef.current;
      if (!snap) return;
      const r = anchor.getBoundingClientRect();
      setPicker({
        x: r.left, y: r.bottom + 2,
        title: 'Юрлицо',
        items: snap.companies.map((c) => ({
          key: String(c.id),
          label: c.code,
          hint: companyLabel(c),
          active: c.id === currentCompanyId,
          color: getCompanyColor(c.id, snap.companies).color,
        })),
        onPick: (key) => onPick(Number(key)),
      });
    },
    []
  );

  const openAbsencePicker = useCallback(
    (anchor: HTMLElement, onPick: (kind: AbsenceKind) => void) => {
      const r = anchor.getBoundingClientRect();
      setPicker({
        x: r.left, y: r.bottom + 2,
        title: 'Код отсутствия',
        items: ABSENCE_KINDS.map((a) => ({
          key: a.kind, label: a.code, hint: a.label, color: a.color,
        })),
        onPick: (key) => onPick(key as AbsenceKind),
      });
    },
    []
  );

  // ── Ночная смена: отметить/снять выход в ночь ──
  // Часы дня не трогаем: ночная смена — отдельная подработка и сосуществует с
  // дневной работой. Лимит фонда отдела проверяет БЭК (409) — здесь только
  // показываем его сообщение: два человека в разных вкладках иначе
  // перерасходовали бы фонд.
  const toggleNight = useCallback(
    async (emp: Employee, position: Position, day: number, value: boolean) => {
      try {
        await timesheetApi.setNightShift({
          employee_id: emp.id,
          position_id: positionIdParam(position) ?? null,
          work_date: dateStr(year, month, day),
          value,
        });
        // Месяц перечитывается целиком, как и для кодов отсутствия: остаток
        // лимита и фонд отдела (`night_funds`) считаются по ВСЕМУ отделу и
        // приходят только с ответом месяца — из PUT /night-shift их не взять.
        // Ночные отмечают заметно реже, чем вводят часы.
        await fetchMonth(false);
        afterEdit();
      } catch (err: any) {
        toast.error(err?.message ?? String(err));
      }
    },
    [year, month, afterEdit, fetchMonth]
  );

  // ── Excel export ──
  const handleExportExcel = async () => {
    setExporting(true);
    try {
      const blob = await timesheetApi.exportExcel(year, month, departmentFilter ?? undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `timesheet_T13_${year}_${String(month).padStart(2, '0')}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Файл сохранён');
    } catch (err: any) {
      toast.error('Ошибка экспорта: ' + (err?.message ?? err));
    } finally {
      setExporting(false);
    }
  };

  // ── Автозаполнение по графику ──
  const handleAutofill = async () => {
    setAutofillLoading(true);
    try {
      const preview = await timesheetApi.autofillPreview(year, month, departmentFilter ?? undefined);
      setAutofillPreview(preview);
    } catch (err: any) {
      toast.error('Ошибка автозаполнения: ' + (err?.message ?? err));
    } finally {
      setAutofillLoading(false);
    }
  };

  const handleAutofillApply = async () => {
    const res = await timesheetApi.autofillApply(year, month, departmentFilter ?? undefined);
    toast.success(`Заполнено: ${res.entries_created} записей для ${res.employees_count} сотрудников`);
    await reload();
  };

  // ── Period actions ──
  const submitPeriod = async (periodId: number) => {
    try {
      await timesheetApi.submitPeriod(periodId);
      toast.success('Период отправлен на проверку');
      reload();
    } catch (err: any) {
      toast.error('Не удалось отправить: ' + (err?.message ?? err));
    }
  };

  const closePeriod = async (periodId: number) => {
    try {
      await timesheetApi.closePeriod(periodId);
      toast.success('Период закрыт');
      reload();
    } catch (err: any) {
      toast.error('Не удалось закрыть: ' + (err?.message ?? err));
    }
  };

  const returnPeriod = async (periodId: number, reason: string) => {
    try {
      await timesheetApi.returnPeriod(periodId, reason);
      toast.success('Период возвращён на доработку');
      reload();
    } catch (err: any) {
      toast.error('Не удалось вернуть: ' + (err?.message ?? err));
    }
  };

  const reopenPeriod = async (periodId: number, reason: string) => {
    try {
      await timesheetApi.reopenPeriod(periodId, reason);
      toast.success('Период переоткрыт');
      reload();
    } catch (err: any) {
      toast.error('Не удалось переоткрыть: ' + (err?.message ?? err));
    }
  };

  // ── Расчёт итогов по дням и компаниям ──
  // Итоги считаем ТОЛЬКО по entries видимых сотрудников (Bug 6)
  const dayTotals = useMemo(() => {
    const numDays = daysInMonth(year, month);
    const totals: number[] = new Array(numDays + 1).fill(0);
    if (!data) return totals;
    for (const e of data.entries) {
      if (!visibleEmpIds.has(e.employee_id)) continue;
      const d = parseInt(e.work_date.slice(-2), 10);
      totals[d] += num(e.hours);
    }
    return totals;
  }, [data, year, month, visibleEmpIds]);

  const companyTotals = useMemo(() => {
    const totals = new Map<number, number>();
    if (!data) return totals;
    for (const e of data.entries) {
      if (!visibleEmpIds.has(e.employee_id)) continue;
      totals.set(e.company_id, (totals.get(e.company_id) ?? 0) + num(e.hours));
    }
    return totals;
  }, [data, visibleEmpIds]);

  // Итоги по колонкам часов — для строки «ИТОГО» у табельщика. Как и денежные
  // итоги, считаются по ВСЕМ видимым сотрудникам месяца, а не по отфильтрованным
  // строкам (см. счётчик «найдено N из M · итоги по всем»).
  const hourTotals = useMemo(() => {
    const acc = {
      norm: 0, normDays: 0, overtime: 0, offSchedule: 0, holiday: 0,
      nightShifts: 0, vacationDays: 0, sickDays: 0,
    };
    for (const pe of data?.payroll?.employees ?? []) {
      acc.norm += num(pe.norm_hours);
      acc.normDays += pe.norm_days ?? 0;
      acc.overtime += num(pe.overtime_hours);
      acc.offSchedule += num(pe.off_schedule_hours);
      acc.holiday += num(pe.holiday_hours);
      acc.nightShifts += pe.night_shifts ?? 0;
      acc.vacationDays += pe.vacation_days ?? 0;
      acc.sickDays += pe.sick_days ?? 0;
    }
    return acc;
  }, [data]);

  // ── Переключение месяца ──
  const prevMonth = () => {
    if (month === 1) {
      setMonth(12);
      setYear(year - 1);
    } else setMonth(month - 1);
  };
  const nextMonth = () => {
    if (month === 12) {
      setMonth(1);
      setYear(year + 1);
    } else setMonth(month + 1);
  };

  // ── Render ──
  const numDays = daysInMonth(year, month);

  // Отдел ещё не выбран — не грузим табель и говорим об этом прямо, иначе пустой
  // экран читается как «сломалось». «Все отделы» рядом, отдельной кнопкой.
  if (needsDeptChoice) {
    return (
      <DepartmentGate
        departments={departments}
        allLabel={isDeptScoped ? 'Все мои отделы' : 'Все отделы'}
        onPick={setDeptChoice}
      />
    );
  }
  if (loading && !data) {
    return <div className="p-8 text-gray-500">Загрузка…</div>;
  }
  if (!data) {
    return <div className="p-8 text-gray-500">Нет данных</div>;
  }

  const periodForDept = (deptId: number | null) =>
    data.periods.find((p) => p.department_id === deptId);

  // ФИО,Должность,Отдел,График(4) + дни + Итого ч + блок справа:
  //   деньги (15): Коэф,Норма,Δ,Оклад,Сверхур,Вне граф.,Праздн.,Ночные,
  //                Отпускные,Больничные,Премия,KPI,Итого₽,Удержано,К выплате
  //   часы  (8):   Норма,Δ,Сверхур,Вне граф.,Празд.,Ночные см.,Отпуск,Больничный
  // Числа держим константами: по ним считается colSpan строки «Ночные» и
  // заглушка ИТОГО, и разъехавшись, они ломают всю таблицу.
  const MONEY_COLS = 15;
  const HOUR_COLS = 8;

  // ── Блок «Распределение» (task_hr_applications) ──
  // Показывается только в табеле отдела «по заявкам» и только тем, кто видит
  // деньги. Видимость завязана на сами ЗАЯВКИ, а не на присланные суммы: суммы
  // приходят вместе с расчётом, и колонки прыгали бы после каждой правки часа.
  const distributionOn =
    canSeeMoney && (data.applications ?? []).some((a) => !a.is_empty);
  const distCompanies = distributionOn ? data.companies : [];
  const distCols = distributionOn ? distCompanies.length + 1 : 0;
  // Ниже — БЕЗ useMemo: этот участок идёт после ранних return-ов (гейт отдела,
  // загрузка, пустой ответ), а хук после условного выхода ломает порядок хуков
  // и роняет страницу. Данных тут десятки строк, считать их каждый рендер дёшево.
  /** posKey → {company_id: сумма} — суммы считает бэк, фронт их только рисует. */
  const distByPos = (() => {
    const map = new Map<string, Record<number, string>>();
    for (const r of data.applications_distribution ?? []) {
      map.set(posKey(r.employee_id, r.position_id), r.amounts);
    }
    return map;
  })();
  /**
   * Итоги распределения по юрлицам — общие (строка подвала) и ОТДЕЛЬНО по
   * каждому отделу: в режиме «все отделы» флаг может стоять у нескольких, и
   * одна общая сумма в блоке заявок отдела была бы враньём.
   */
  const distTotals = (() => {
    const totals: Record<number, number> = {};
    const byDept = new Map<number, { totals: Record<number, number>; grand: number }>();
    let grand = 0;
    for (const r of data.applications_distribution ?? []) {
      const dept = r.department_id;
      if (dept != null && !byDept.has(dept)) byDept.set(dept, { totals: {}, grand: 0 });
      const bucket = dept != null ? byDept.get(dept)! : null;
      for (const [cid, amount] of Object.entries(r.amounts)) {
        const value = num(amount);
        totals[Number(cid)] = (totals[Number(cid)] ?? 0) + value;
        grand += value;
        if (bucket) {
          bucket.totals[Number(cid)] = (bucket.totals[Number(cid)] ?? 0) + value;
          bucket.grand += value;
        }
      }
    }
    return { totals, grand, byDept };
  })();

  const trailingCols =
    (canSeeMoney ? MONEY_COLS : canSeeHourStats ? HOUR_COLS : 0) + distCols;
  const totalCols = 4 + numDays + 1 + trailingCols;

  /** Есть ли у рабочего места ночные смены — только тогда рисуем строку «Ночные». */
  const hasNight = (position: Position) => Boolean(position.has_night_shifts);

  /**
   * Сколько СТРОК таблицы занимает сотрудник в этом списке: по строке на
   * рабочее место плюс строка «Ночные» у тех мест, где ночные включены.
   * `count` из withSpans считает только позиции, и ФИО-ячейка с ним разъехалась
   * бы с телом на высоту ночных строк.
   */
  const employeeRowSpans = (rows: PositionRow[]) => {
    const map = new Map<number, number>();
    for (const r of rows) {
      map.set(r.emp.id, (map.get(r.emp.id) ?? 0) + 1 + (hasNight(r.position) ? 1 : 0));
    }
    return map;
  };

  // Одна строка = одно рабочее место. У сотрудника с единственной позицией
  // строка ровно одна, как было до совместительства; у совместителя ФИО
  // объединяется по его строкам через rowspan.
  const renderPositionRow = (
    { emp, position, index, count }: PositionRow,
    spans?: Map<number, number>,
  ) => {
    const positionId = positionIdParam(position);
    // Высота ФИО-ячейки: строки позиций + их строки «Ночные».
    const nameSpan = spans?.get(emp.id) ?? count;
    // «Итого ч» и весь блок справа (деньги или часы) растягиваются на строку
    // «Ночные»: отдельной строки в сводном блоке быть не должно — там нечего
    // показывать, а пустые ячейки читаются как полоса через таблицу.
    const trailingSpan = hasNight(position) ? { rowSpan: 2 } : {};
    const pay = payrollFor(emp, position);
    // Пока суммы ждут пересчёта, часы берём из ячеек: они уже перечитаны, а
    // payroll.total_hours относится к состоянию до правки — иначе введённая
    // цифра появлялась бы в дне, но «Итого Ч» секунду стояло на месте.
    const hoursFromEntries = () =>
      sumPositionHours(emp.id, positionId, data.entries, primaryPositionIdByEmp);
    const rowTotal = payrollStale
      ? hoursFromEntries()
      : num(pay?.total_hours, 0) || hoursFromEntries();
    const periodEditable = periodForDept(position.department_id)?.can_edit ?? false;
    const schedule = position.schedule ?? null;
    const noSchedule = !schedule;
    const isFirst = index === 0;

    return (
      <Fragment key={`${emp.id}-${position.id}`}>
      <tr
        className="hover:bg-blue-50/30"
        title={noSchedule ? 'График не задан, автозаполнение по графику недоступно' : undefined}
      >
        {/* ── Sticky-колонка сотрудника: merge на все его позиции ── */}
        {isFirst && (
          <td
            rowSpan={nameSpan}
            className="sticky left-0 bg-white border border-gray-200 px-3 py-2 font-medium text-gray-900 align-top"
            style={{ minWidth: 200, zIndex: 10 }}
            title={emp.full_name}
          >
            <div className="truncate max-w-[200px]">{emp.full_name}</div>
            {count > 1 && (
              <div className="text-[10px] font-normal text-gray-400">
                совместительство: {count} места
              </div>
            )}
          </td>
        )}
        {/* Должность / отдел / график — у КАЖДОГО рабочего места свои */}
        <td className="border border-gray-200 px-2 py-2 text-xs text-gray-700">
          <span className="truncate">{position.display_title}</span>
          {position.is_primary && count > 1 && (
            <span className="ml-1 text-[9px] text-gray-400">осн.</span>
          )}
        </td>
        <td className="border border-gray-200 px-2 py-2 text-xs text-gray-600">
          {position.department?.name ?? '—'}
        </td>
        <td className="border border-gray-200 px-2 py-2 text-xs text-center font-mono text-gray-600">
          {noSchedule ? (
            <span className="italic text-gray-400 font-sans">не задан</span>
          ) : (
            schedule?.name
          )}
        </td>

        {/* ── Дни ── */}
        {/* Ячейка дня вынесена в мемоизированный DayCell: правка одной ячейки
            больше не перерисовывает все 31×N ячеек отдела. Все колбэки ниже
            стабильны (useCallback без зависимости от `data`), иначе memo не
            работал бы вовсе. */}
        {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => (
          <DayCell
            key={d}
            day={d}
            dayType={dayTypes[d]}
            slots={entriesByPosDay.get(`${posKey(emp.id, position.id)}:${d}`) ?? EMPTY_SLOTS}
            absence={absenceByEmpDay.get(`${emp.id}:${d}`)}
            isFirst={isFirst}
            editable={periodEditable}
            companies={data.companies}
            employeeId={emp.id}
            positionId={positionId}
            onSaveSlot={saveSlot}
            onChangeCompany={changeSlotCompany}
            onAddSlot={addSlotByIds}
            onSetAbsence={setAbsence}
            onOpenCompanyPicker={openCompanyPicker}
            onOpenAbsencePicker={openAbsencePicker}
          />
        ))}

        {/* ── Итого часов по этому рабочему месту ── */}
        <td {...trailingSpan} className="border border-gray-200 px-3 py-2 text-center font-mono font-semibold bg-gray-50">
          {fmtHours(rowTotal)}
        </td>

        {/* ── Финансы ── */}
        {/* Табельщик: детализация ЧАСОВ вместо денежного блока (деньги бэк ему
            и не отдаёт). Порядок колонок совпадает с шапкой ниже. */}
        {!canSeeMoney && canSeeHourStats && (
          <>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-600">
              <NormCell pay={pay} />
            </td>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-center font-mono text-xs">
              {pay?.delta_hours ? <DeltaCell delta={num(pay.delta_hours)} /> : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-700"
              title="Переработка: часы сверх дневной нормы смены"
            >
              {num(pay?.overtime_hours) > 0 ? fmtHours(num(pay?.overtime_hours)) : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-700"
              title="Вне графика: выход в свой выходной по графику"
            >
              {num(pay?.off_schedule_hours) > 0 ? fmtHours(num(pay?.off_schedule_hours)) : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-700"
              title="Праздничные: работа в нерабочий праздничный день календаря"
            >
              {num(pay?.holiday_hours) > 0 ? fmtHours(num(pay?.holiday_hours)) : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-indigo-700"
              title="Ночные смены: отмеченные выходы в ночь (надбавку считает бухгалтерия)"
            >
              {pay?.night_shifts ? `${pay.night_shifts} см.` : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-700"
              title={absenceDaysTitle('Отпуск', pay?.vacation_days, pay?.vacation_paid_days)}
            >
              {pay?.vacation_days ? `${pay.vacation_days} д` : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-700"
              title={sickLimitTitle(pay)}
            >
              {pay?.sick_days ? (
                <>
                  {pay.sick_days} д
                  {!!pay.sick_unpaid_days && (
                    <span className="text-amber-600"> ({pay.sick_unpaid_days} б/о)</span>
                  )}
                </>
              ) : '—'}
            </td>
          </>
        )}

        {canSeeMoney && (() => {
          const premium = num(pay?.premium_amount);
          const kpi = num(pay?.kpi_amount);
          const deductions = num(pay?.total_deductions);
          // Итого ₽ = всё начисленное: оклад+сверхур+праздн + премия + KPI
          const grossTotal = num(pay?.total_amount) + premium + kpi;
          return (
          <>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-600" title="Оплата выходных">
              {fmtCoeff(pay)}
            </td>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-600">
              <NormCell pay={pay} />
            </td>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-center font-mono text-xs">
              {pay?.delta_hours ? <DeltaCell delta={num(pay.delta_hours)} /> : '—'}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono text-xs"
              title={
                pay?.pay_type === 'per_shift'
                  ? `Посменно: ${pay.base_shifts ?? 0} смен × ${fmtMoney(pay.shift_rate ?? null)}` +
                    `; смены в выходные/праздники — в отдельных колонках, по коэффициенту`
                  : undefined
              }
            >
              {fmtMoney(pay?.base_amount ?? null)}
              {pay?.pay_type === 'per_shift' && (
                <div className="text-[10px] font-sans text-gray-400 leading-tight">
                  {pay.base_shifts ?? 0} см. × {fmtMoney(pay.shift_rate ?? null)}
                </div>
              )}
            </td>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-right font-mono text-xs">
              {fmtMoney(pay?.overtime_amount ?? null)}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono text-xs"
              title="Вне графика: выход в свой выходной по графику"
            >
              {fmtMoney(pay?.off_schedule_amount ?? null)}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono text-xs"
              title="Праздничные: работа в нерабочий праздничный день"
            >
              {fmtMoney(pay?.holiday_amount ?? null)}
            </td>
            {/* Надбавка за ночные смены — смены × ставка фонда отдела */}
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono text-xs"
              title={
                pay?.night_shifts
                  ? `${pay.night_shifts} ночных смен × ${fmtMoney(pay.night_rate ?? null)}`
                  : 'Надбавка за ночные смены: смены × (фонд отдела ÷ дни месяца)'
              }
            >
              {fmtMoney(pay?.night_amount ?? null)}
              {!!pay?.night_shifts && (
                <div className="text-[10px] font-sans text-gray-400 leading-tight">
                  {pay.night_shifts} см. × {fmtMoney(pay.night_rate ?? null)}
                </div>
              )}
            </td>
            {/* Отпускные / больничные — оплата дней отсутствия */}
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono text-xs"
              title={absenceDaysTitle('Отпуск', pay?.vacation_days, pay?.vacation_paid_days)}
            >
              {fmtMoney(pay?.vacation_amount ?? null)}
              {!!pay?.vacation_days && (
                <span className="ml-1 text-[10px] text-gray-400">{pay.vacation_days}д</span>
              )}
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono text-xs"
              title={sickLimitTitle(pay)}
            >
              {fmtMoney(pay?.sick_amount ?? null)}
              {!!pay?.sick_days && (
                <span className="ml-1 text-[10px] text-gray-400">
                  {pay.sick_days}д
                  {!!pay.sick_unpaid_days && (
                    <span className="text-amber-600"> ({pay.sick_unpaid_days} б/о)</span>
                  )}
                </span>
              )}
            </td>
            {/* Премия — своя кнопка */}
            <td {...trailingSpan} className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
              <button
                type="button"
                onClick={() => setAdjModal({ emp, position, category: 'premium' })}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-blue-50 text-gray-700"
                title="Премии"
              >
                <span>{premium > 0 ? fmtMoney(String(premium)) : '—'}</span>
                <span className="text-blue-500 font-sans">✎</span>
              </button>
            </td>
            {/* KPI — своя кнопка */}
            <td {...trailingSpan} className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
              <button
                type="button"
                onClick={() => setAdjModal({ emp, position, category: 'kpi' })}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-blue-50 text-gray-700"
                title="KPI"
              >
                <span>{kpi > 0 ? fmtMoney(String(kpi)) : '—'}</span>
                <span className="text-blue-500 font-sans">✎</span>
              </button>
            </td>
            <td {...trailingSpan} className="border border-gray-200 px-2 py-2 text-right font-mono font-semibold text-blue-700 bg-blue-50/30">
              {grossTotal > 0 ? fmtMoney(String(grossTotal)) : '—'}
            </td>
            {/* Удержано — аванс + займ, своя кнопка */}
            <td {...trailingSpan} className="border border-gray-200 px-2 py-1 text-right font-mono text-xs text-red-600">
              <button
                type="button"
                onClick={() => setAdjModal({ emp, position, category: 'deduction' })}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-red-50 text-red-600"
                title="Аванс и займ"
              >
                <span>{deductions > 0 ? '−' + fmtMoney(String(deductions)) : '—'}</span>
                <span className="text-blue-500 font-sans">✎</span>
              </button>
            </td>
            <td {...trailingSpan}
              className="border border-gray-200 px-2 py-2 text-right font-mono font-bold text-emerald-700 bg-emerald-50/40"
              title={
                num(pay?.rounding_tail) > 0
                  ? `Округлено вниз до 100 ₽: точно ${fmtMoney(pay?.net_payout_exact ?? null)}, округление −${fmtMoney(pay?.rounding_tail ?? null)}`
                  : undefined
              }
            >
              {pay?.is_calculable ? fmtMoney(pay?.net_payout ?? null) : '—'}
            </td>
            {/* ── Распределение по заявкам (task_hr_applications) ──
                 Суммы приходят с бэка теми же числами, что в ведомости; фронт
                 «Итого начислено» из кусков расчёта не пересобирает. */}
            {distributionOn && (() => {
              const amounts = distByPos.get(posKey(emp.id, position.id)) ?? null;
              const rowTotal = amounts
                ? Object.values(amounts).reduce((acc, v) => acc + num(v), 0)
                : 0;
              return (
                <>
                  {distCompanies.map((c) => (
                    <td
                      key={`dist-${c.id}`}
                      {...trailingSpan}
                      className="border border-gray-200 px-2 py-2 text-right font-mono text-xs bg-emerald-50/30"
                    >
                      {amounts && num(amounts[c.id]) > 0
                        ? fmtMoney(String(amounts[c.id]))
                        : '—'}
                    </td>
                  ))}
                  <td
                    {...trailingSpan}
                    className="border border-gray-200 px-2 py-2 text-right font-mono font-semibold text-emerald-800 bg-emerald-100/60"
                  >
                    {rowTotal > 0 ? fmtMoney(String(rowTotal)) : '—'}
                  </td>
                </>
              );
            })()}
          </>
          );
        })()}
      </tr>

      {/* ── Строка «Ночные»: отдельная подработка, галочка на день ──
          Ночная смена не привязана к графику и сосуществует с дневными часами
          того же дня, поэтому это своя тонкая строка, а не слот в ячейке. */}
      {hasNight(position) && (
        <NightRow
          emp={emp}
          position={position}
          numDays={numDays}
          dayTypes={dayTypes}
          marked={(d) => nightByPosDay.has(`${posKey(emp.id, position.id)}:${d}`)}
          absenceCode={(d) => absenceByEmpDay.get(`${emp.id}:${d}`)?.code}
          fund={
            position.department_id != null
              ? nightFundByDept.get(position.department_id) ?? null
              : null
          }
          editable={periodEditable}
          onToggle={(d, value) => toggleNight(emp, position, d, value)}
        />
      )}
      </Fragment>
    );
  };

  const renderGroupDivider = (
    deptId: number | null,
    name: string,
    period: Period | null
  ) => (
    <tr key={`group-${deptId ?? 'null'}`}>
      <td colSpan={totalCols} className="bg-slate-100 border border-gray-300 p-0">
        <div className="sticky left-0 flex items-center gap-3 px-3 py-2 w-fit">
          <span className="text-sm font-bold uppercase tracking-wide text-gray-700">
            {name}
          </span>
          {period && (
            <PeriodBadge
              period={period}
              onSubmit={() => submitPeriod(period.id)}
              onClose={() => closePeriod(period.id)}
              onReturn={(reason) => returnPeriod(period.id, reason)}
              onReopen={(reason) => reopenPeriod(period.id, reason)}
            />
          )}
        </div>
      </td>
    </tr>
  );

  return (
    <div className="h-full flex flex-col overflow-hidden min-w-0">
      {/* ───── Header: переключатель месяца, фильтры, действия ───── */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-gray-200 bg-white flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <button
            onClick={prevMonth}
            className="p-2 rounded hover:bg-gray-100"
            aria-label="Предыдущий месяц"
          >
            ←
          </button>
          <div className="text-base font-semibold min-w-[160px] text-center">
            {MONTH_NAMES_RU[month - 1]} {year}
          </div>
          <button
            onClick={nextMonth}
            className="p-2 rounded hover:bg-gray-100"
            aria-label="Следующий месяц"
          >
            →
          </button>

          {/* ── Переключатель вида: Классический / По компаниям ── */}
          <div className="ml-4 inline-flex rounded-lg border border-gray-300 overflow-hidden text-sm">
            <button
              onClick={() => setViewMode('classic')}
              className={
                'px-3 py-1.5 transition-colors ' +
                (viewMode === 'classic'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50')
              }
            >
              Классический
            </button>
            <button
              onClick={() => setViewMode('company')}
              className={
                'px-3 py-1.5 transition-colors border-l border-gray-300 ' +
                (viewMode === 'company'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50')
              }
            >
              По компаниям
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <div className="relative">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск: ФИО или таб.№"
              className="border border-gray-300 rounded pl-2 pr-7 py-1 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                title="Сбросить поиск"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 leading-none"
              >
                ×
              </button>
            )}
          </div>

          {data.companies.length > 0 && (
            <select
              className="border border-gray-300 rounded px-2 py-1 text-sm"
              value={companyFilter ?? ''}
              onChange={(e) =>
                setCompanyFilter(e.target.value === '' ? null : parseInt(e.target.value, 10))
              }
              title="Сотрудники с часами по этой компании или с ней как основной"
            >
              <option value="">Все компании</option>
              {data.companies.map((c) => (
                <option key={c.id} value={c.id} title={c.name}>
                  {companyLabel(c)}
                </option>
              ))}
            </select>
          )}

          {canSelectDept && departments.length > 0 && (
            <select
              className="border border-gray-300 rounded px-2 py-1 text-sm"
              value={deptChoice === 'all' ? 'all' : String(deptChoice ?? '')}
              onChange={(e) =>
                setDeptChoice(
                  e.target.value === 'all' ? 'all' : parseInt(e.target.value, 10)
                )
              }
            >
              {/* «Все отделы» — осознанный выбор, а не дефолт: на всех отделах это
                  сотни строк и полный расчёт ЗП по всем. */}
              <option value="all">{isDeptScoped ? 'Все мои отделы' : 'Все отделы'}</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          )}

          {payrollStale && (
            <span
              className="text-xs text-amber-600"
              title={
                canSeeMoney
                  ? 'Часы сохранены. Суммы (оклад, переработка, к выплате) пересчитываются.'
                  : 'Часы сохранены. Норма, переработка и итоги пересчитываются.'
              }
            >
              {canSeeMoney ? 'суммы пересчитываются…' : 'итоги пересчитываются…'}
            </span>
          )}

          {filtersActive && (
            <>
              <span
                className="text-xs text-gray-500"
                title="Фильтры сужают только строки. Итоги остаются по всем сотрудникам месяца."
              >
                найдено {shownEmployees.length} из {visibleEmployees.length}
                <span className="text-gray-400"> · итоги по всем</span>
              </span>
              <button
                type="button"
                onClick={() => { setSearch(''); setCompanyFilter(null); }}
                className="px-2 py-1 text-sm rounded border border-gray-300 text-gray-500 hover:bg-gray-100"
              >
                Сброс
              </button>
            </>
          )}

          {/* Статусы периодов в шапке — только когда НЕ группируем (один отдел в выдаче) */}
          {!grouped &&
            data.periods.map((p) => {
              if (departmentFilter !== null && p.department_id !== departmentFilter) return null;
              return (
                <PeriodBadge
                  key={p.id}
                  period={p}
                  onSubmit={() => submitPeriod(p.id)}
                  onClose={() => closePeriod(p.id)}
                  onReturn={(reason) => returnPeriod(p.id, reason)}
                  onReopen={(reason) => reopenPeriod(p.id, reason)}
                />
              );
            })}

          {allEditable && (
            <button
              className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={autofillLoading}
              onClick={handleAutofill}
            >
              {autofillLoading ? 'Загрузка…' : 'Заполнить по графику'}
            </button>
          )}

          {canExport && (
            <button
              onClick={handleExportExcel}
              disabled={exporting}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-green-600 text-green-700 hover:bg-green-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              {exporting ? 'Экспорт…' : 'Excel'}
            </button>
          )}
        </div>
      </div>

      {/* ───── Легенда компаний ───── */}
      <div className="flex-shrink-0 px-6 py-2 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-2 flex-wrap text-xs">
          {data.companies.map((c) => {
            const col = getCompanyColor(c.id, data.companies);
            return (
              <span
                key={c.id}
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-mono"
                style={{
                  background: col.bg,
                  color: col.color,
                  border: `1px solid ${col.color}40`,
                }}
              >
                <span
                  className="inline-block w-2 h-2 rounded-full"
                  style={{ background: col.color }}
                />
                {companyLabel(c)}
              </span>
            );
          })}
          <span className="text-gray-300">|</span>
          {/* Легенда кодов отсутствий — расшифровка ОТ/ДО/Б/Н */}
          {ABSENCE_KINDS.map((a) => (
            <span
              key={a.kind}
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-mono font-bold"
              style={{ background: a.bg, color: a.color, border: `1px solid ${a.color}40` }}
              title={a.paid ? 'Оплачивается' : 'Не оплачивается'}
            >
              {a.code}
              <span className="font-sans font-normal">{a.label}</span>
            </span>
          ))}
          <span
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-mono font-bold text-gray-500"
            style={{ background: '#f3f4f6', border: '1px dashed #6b728080' }}
            title="Больничный сверх годового лимита — за свой счёт"
          >
            Б*
            <span className="font-sans font-normal">сверх лимита, не оплачивается</span>
          </span>
          <span className="text-gray-500">
            «+» = слот компании · «·» = код отсутствия · серый = выходной
          </span>
        </div>
      </div>

      {/* ───── Заявки на подбор (task_hr_applications) ─────
           Показывается только для отделов с флагом «распределение по заявкам»
           (у остальных блока нет вовсе) и только тем, кто видит распределение. */}
      <ApplicationsPanel
        applications={data.applications ?? []}
        companies={data.companies}
        year={year}
        month={month}
        canEdit={canSeeMoney}
        totalsByDepartment={distributionOn ? distTotals.byDept : undefined}
        onSaved={reload}
      />

      {/* ───── Скролл-контейнер с таблицей ───── */}
      <div className="flex-1 relative min-h-0 min-w-0">
      <div className="absolute inset-0 overflow-auto bg-white">
        {viewMode === 'company' ? (
          <TimesheetCompanyView
            data={data}
            year={year}
            month={month}
            numDays={numDays}
            dayTypes={dayTypes}
            rows={flatRows}
            grouped={grouped}
            groups={groups}
            payrollFor={payrollFor}
            entryPositionId={entryPositionId}
            absenceByEmpDay={absenceByEmpDay}
            canSeeMoney={canSeeMoney}
            canSeeHourStats={canSeeHourStats}
            saveSlot={saveSlot}
            setAbsence={setAbsence}
            periodForDept={periodForDept}
            dayTotals={dayTotals}
            onSubmit={submitPeriod}
            onClose={closePeriod}
            onReturn={returnPeriod}
            onReopen={reopenPeriod}
          />
        ) : (
        <table
          className="border-collapse text-xs"
          style={{ minWidth: 'max-content' }}
        >
          {/* ===== ШАПКА ===== */}
          <thead>
            <tr>
              <th
                className="sticky left-0 top-0 bg-gray-50 border border-gray-200 px-3 py-2 text-left font-medium text-gray-600"
                style={{ minWidth: 200, zIndex: 30 }}
              >
                Сотрудник
              </th>
              <th
                className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-left font-medium text-gray-600"
                style={{ minWidth: 110, zIndex: 20 }}
                title="Рабочее место: у совместителя строка на каждое, со своим графиком и расчётом"
              >
                Должность
              </th>
              <th
                className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-left font-medium text-gray-600"
                style={{ minWidth: 100, zIndex: 20 }}
              >
                Отдел
              </th>
              <th
                className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600"
                style={{ minWidth: 60, zIndex: 20 }}
              >
                График
              </th>
              {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => {
                const t = dayTypes[d];
                const wd = jsWeekdayMonFirst(year, month, d);
                const cls =
                  t === 'holiday'
                    ? 'bg-red-50 text-red-600'
                    : t === 'short'
                    ? 'bg-yellow-50 text-yellow-700'
                    : t === 'weekend'
                    ? 'bg-gray-100 text-gray-500'
                    : 'bg-gray-50 text-gray-600';
                return (
                  <th
                    key={d}
                    className={`sticky top-0 ${cls} border border-gray-200 px-1 py-1 text-center font-medium`}
                    style={{ minWidth: 60, zIndex: 20 }}
                    title={dayTypeLabel(t)}
                  >
                    <div className="text-sm font-semibold">{d}</div>
                    <div className="text-[10px] font-normal opacity-75">
                      {WEEKDAY_RU[wd]}
                    </div>
                  </th>
                );
              })}
              <th
                className="sticky top-0 bg-gray-50 border border-gray-200 px-3 py-2 text-center font-medium text-gray-600"
                style={{ minWidth: 70, zIndex: 20 }}
              >
                Итого ч
              </th>
              {/* Табельщику — часы вместо рублей, порядок как в строке выше */}
              {!canSeeMoney && canSeeHourStats && (
                <>
                  {[
                    ['Норма ч / дн', 72, 'Норма по графику за месяц: часов и рабочих дней (смен)'],
                    ['Δ', 60, 'Отклонение факта от нормы'],
                    ['Сверхур. ч', 76, 'Переработка: часы сверх дневной нормы смены'],
                    ['Вне граф. ч', 82, 'Выход в свой выходной по графику'],
                    ['Празд. ч', 76, 'Работа в нерабочий праздничный день календаря'],
                    ['Ночные см.', 82, 'Отмечено выходов в ночь (надбавку считает бухгалтерия)'],
                    ['Отпуск', 70, 'Дни, отмеченные кодом ОТ'],
                    ['Больничный', 92, 'Дни, отмеченные кодом Б (в скобках — сверх годового лимита)'],
                  ].map(([label, width, hint]) => (
                    <th
                      key={label as string}
                      className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600"
                      style={{ minWidth: width as number, zIndex: 20 }}
                      title={hint as string}
                    >
                      {label as string}
                    </th>
                  ))}
                </>
              )}
              {canSeeMoney && (
                <>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600"
                    style={{ minWidth: 56, zIndex: 20 }}
                    title="Коэффициент/ставка оплаты выходных (из карточки сотрудника)"
                  >
                    Коэф.
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600"
                    style={{ minWidth: 72, zIndex: 20 }}
                    title="Норма по графику за месяц: часов и рабочих дней (смен)"
                  >
                    Норма ч / дн
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600"
                    style={{ minWidth: 60, zIndex: 20 }}
                  >
                    Δ
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 90, zIndex: 20 }}
                  >
                    Оклад
                    <div className="text-[10px] font-normal text-gray-400 leading-tight">
                      посменно — смены × ставка
                    </div>
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 80, zIndex: 20 }}
                  >
                    Сверхур.
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 80, zIndex: 20 }}
                  >
                    Вне граф.
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 80, zIndex: 20 }}
                    title="Праздничные: работа в нерабочий праздничный день календаря"
                  >
                    Праздн.
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 110, zIndex: 20 }}
                    title="Надбавка за ночные смены: число смен × (фонд отдела ÷ календарные дни месяца)"
                  >
                    Ночные ₽
                    <div className="text-[10px] font-normal text-gray-400 leading-tight">
                      смены × ставка фонда
                    </div>
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 90, zIndex: 20 }}
                    title="Отпускные: оклад / норма × (дни × 8)"
                  >
                    Отпускные
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 90, zIndex: 20 }}
                    title="Больничные: оклад / норма × (дни × 8)"
                  >
                    Больничные
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 90, zIndex: 20 }}
                    title="Премия"
                  >
                    Премия
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 90, zIndex: 20 }}
                    title="KPI"
                  >
                    KPI
                  </th>
                  <th
                    className="sticky top-0 bg-blue-50 border border-gray-200 px-2 py-2 text-right font-semibold text-blue-700"
                    style={{ minWidth: 100, zIndex: 20 }}
                  >
                    Итого ₽
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 90, zIndex: 20 }}
                    title="Удержано: займ + аванс"
                  >
                    Удержано ₽
                  </th>
                  <th
                    className="sticky top-0 bg-emerald-50 border border-gray-200 px-2 py-2 text-right font-semibold text-emerald-700"
                    style={{ minWidth: 110, zIndex: 20 }}
                  >
                    К выплате ₽
                  </th>
                </>
              )}
              {/* Распределение начисленного по юрлицам — по заявкам на подбор */}
              {distributionOn && (
                <>
                  {distCompanies.map((c) => (
                    <th
                      key={`dist-h-${c.id}`}
                      className="sticky top-0 bg-emerald-50 border border-gray-200 px-2 py-2 text-right font-medium text-emerald-800 leading-tight"
                      style={{ minWidth: 96, maxWidth: 130, zIndex: 20 }}
                      title={`Распределение по заявкам: ${c.name}`}
                    >
                      {companyLabel(c)}
                    </th>
                  ))}
                  <th
                    className="sticky top-0 bg-emerald-100 border border-gray-200 px-2 py-2 text-right font-semibold text-emerald-900"
                    style={{ minWidth: 100, zIndex: 20 }}
                    title="Итого распределено — равно «Итого ₽» строки"
                  >
                    ИТОГО
                  </th>
                </>
              )}
            </tr>
          </thead>

          {/* ===== ТЕЛО ===== */}
          <tbody>
            {shownEmployees.length === 0 && (
              <tr>
                <td
                  colSpan={totalCols}
                  className="text-center text-gray-500 py-10"
                >
                  {filtersActive ? 'Никто не найден по этим фильтрам' : 'Нет сотрудников'}
                </td>
              </tr>
            )}

            {/* rowspan ФИО считаем внутри отрисовываемого списка: у строки
                «Ночные» своя высота, и в разных группах у человека разный набор
                рабочих мест. */}
            {grouped
              ? groups.map((g) => {
                  const spans = employeeRowSpans(g.rows);
                  return (
                    <Fragment key={`grp-${g.deptId ?? 'null'}`}>
                      {renderGroupDivider(g.deptId, g.name, g.period)}
                      {g.rows.map((row) => renderPositionRow(row, spans))}
                    </Fragment>
                  );
                })
              : (() => {
                  const spans = employeeRowSpans(flatRows);
                  return flatRows.map((row) => renderPositionRow(row, spans));
                })()}

            {/* ===== ИТОГО строка ===== */}
            {visibleEmployees.length > 0 && (
              <tr className="bg-gray-100 font-semibold">
                <td
                  className="sticky left-0 bg-gray-200 border border-gray-300 px-3 py-2"
                  style={{ minWidth: 200, zIndex: 10 }}
                >
                  ИТОГО
                </td>
                <td className="border border-gray-300 px-2 py-2" colSpan={3}></td>
                {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => (
                  <td
                    key={d}
                    className="border border-gray-300 px-1 py-2 text-center font-mono text-xs text-gray-700"
                  >
                    {dayTotals[d] > 0 ? fmtHours(dayTotals[d]) : ''}
                  </td>
                ))}
                <td className="border border-gray-300 px-3 py-2 text-center font-mono font-bold">
                  {fmtHours(dayTotals.reduce((a, b) => a + b, 0))}
                </td>
                {!canSeeMoney && canSeeHourStats && (
                  <>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono">
                      {hourTotals.norm > 0 ? fmtHours(hourTotals.norm) : ''}
                      {hourTotals.normDays > 0 && (
                        <span className="block text-[10px] font-sans font-normal text-gray-500 leading-tight">
                          {hourTotals.normDays} дн
                        </span>
                      )}
                    </td>
                    <td className="border border-gray-300 px-2 py-2"></td>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono">
                      {hourTotals.overtime > 0 ? fmtHours(hourTotals.overtime) : ''}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono">
                      {hourTotals.offSchedule > 0 ? fmtHours(hourTotals.offSchedule) : ''}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono">
                      {hourTotals.holiday > 0 ? fmtHours(hourTotals.holiday) : ''}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono text-indigo-700">
                      {hourTotals.nightShifts > 0 ? `${hourTotals.nightShifts} см.` : ''}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono">
                      {hourTotals.vacationDays > 0 ? `${hourTotals.vacationDays} д` : ''}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-center font-mono">
                      {hourTotals.sickDays > 0 ? `${hourTotals.sickDays} д` : ''}
                    </td>
                  </>
                )}
                {canSeeMoney && (data.payroll ? (
                  <>
                    <td className="border border-gray-300 px-2 py-2"></td>
                    <td className="border border-gray-300 px-2 py-2"></td>
                    <td className="border border-gray-300 px-2 py-2"></td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_base_amount)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_overtime_amount)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_off_schedule_amount ?? null)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_holiday_amount)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_night_amount ?? null)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_vacation_amount ?? null)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_sick_amount ?? null)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_premium ?? null)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(data.payroll.total_kpi ?? null)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-blue-700 bg-blue-100">
                      {fmtMoney(String(num(data.payroll.grand_total) + num(data.payroll.total_premium) + num(data.payroll.total_kpi)))}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono text-red-600">
                      {num(data.payroll.total_deductions) > 0 ? '−' + fmtMoney(data.payroll.total_deductions ?? null) : '—'}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-emerald-700 bg-emerald-100">
                      {fmtMoney(data.payroll.total_net_payout ?? null)}
                    </td>
                  </>
                ) : (
                  Array.from({ length: MONEY_COLS }, (_, i) => (
                    <td key={i} className="border border-gray-300 px-2 py-2" />
                  ))
                ))}
                {/* Итоги распределения по юрлицам — как строка сумм в файле HR.
                    Считаются по ВСЕМ строкам месяца, как и остальные итоги. */}
                {distributionOn && (
                  <>
                    {distCompanies.map((c) => (
                      <td
                        key={`dist-t-${c.id}`}
                        className="border border-gray-300 px-2 py-2 text-right font-mono font-semibold text-emerald-800 bg-emerald-100"
                      >
                        {distTotals.totals[c.id] > 0
                          ? fmtMoney(String(distTotals.totals[c.id]))
                          : '—'}
                      </td>
                    ))}
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-emerald-900 bg-emerald-200/70">
                      {distTotals.grand > 0 ? fmtMoney(String(distTotals.grand)) : '—'}
                    </td>
                  </>
                )}
              </tr>
            )}

          </tbody>
        </table>
        )}
      </div>
      </div>

      {/* ───── Сводка по компаниям (вне скролла, не ездит горизонтально) ───── */}
      {visibleEmployees.length > 0 && data.companies.length > 0 && (
        <div className="flex-shrink-0 border-t-2 border-gray-300 bg-white">
          <button
            type="button"
            onClick={() => setCompanySummaryOpen((v) => !v)}
            className="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200 hover:bg-gray-100"
            title={companySummaryOpen ? 'Свернуть сводку' : 'Развернуть сводку'}
          >
            <span className="text-[10px] leading-none">{companySummaryOpen ? '▾' : '▸'}</span>
            По компаниям
            {!companySummaryOpen && (
              <span className="font-normal normal-case tracking-normal text-gray-400">
                — скрыто, компаний: {data.companies.length}
              </span>
            )}
          </button>
          {companySummaryOpen && data.companies.map((c) => {
            const col = getCompanyColor(c.id, data.companies);
            const hours = companyTotals.get(c.id) ?? 0;
            let money = 0;
            if (canSeeMoney && data.payroll) {
              for (const pe of data.payroll.employees) {
                const b = pe.breakdown_by_company?.find((x) => x.company_id === c.id);
                if (b) money += num(b.total);
              }
            }
            return (
              <div
                key={c.id}
                className="flex items-center gap-4 px-3 py-1.5 text-xs border-b border-gray-100"
              >
                <span
                  className="flex items-center gap-1.5 w-52 truncate"
                  style={{ color: col.color }}
                  title={c.name}
                >
                  <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: col.color }} />
                  {companyLabel(c)}
                </span>
                <span className="w-16 text-center font-mono font-semibold" style={{ color: col.color }}>
                  {fmtHours(hours)} ч
                </span>
                {canSeeMoney && (
                  <span className="text-right font-mono font-semibold" style={{ color: col.color }}>
                    {fmtMoney(money > 0 ? String(money) : null)}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Единственный на страницу список выбора юрлица / кода отсутствия */}
      <CellPicker state={picker} onClose={closePicker} />

      {/* ── Модал управления премиями/KPI/авансом/займом (задача 3.11a) ── */}
      {adjModal && (
        <AdjustmentsModal
          employee={adjModal.emp}
          position={adjModal.position}
          category={adjModal.category}
          year={year}
          month={month}
          payroll={payrollFor(adjModal.emp, adjModal.position) ?? null}
          adjustments={adjByPos.get(posKey(adjModal.emp.id, adjModal.position.id)) ?? []}
          onClose={() => setAdjModal(null)}
          onChanged={afterAdjustment}
        />
      )}

      {/* ── Модал автозаполнения по графику ── */}
      {autofillPreview && (
        <AutofillModal
          preview={autofillPreview}
          onApply={handleAutofillApply}
          onClose={() => setAutofillPreview(null)}
        />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Подкомпоненты
// ──────────────────────────────────────────────────────────────

// ── AutofillModal: превью автозаполнения по графику + применение ──
interface AutofillModalProps {
  preview: AutofillPreview;
  onApply: () => Promise<void>;
  onClose: () => void;
}

function AutofillModal({ preview, onApply, onClose }: AutofillModalProps) {
  const [loading, setLoading] = useState(false);
  const [showSkipped, setShowSkipped] = useState(false);

  const byEmployee = new Map<number, { count: number; hours: number }>();
  for (const e of preview.entries_to_create) {
    const hours = parseFloat(e.hours as unknown as string);
    const existing = byEmployee.get(e.employee_id);
    if (existing) { existing.count += 1; existing.hours += hours; }
    else byEmployee.set(e.employee_id, { count: 1, hours });
  }

  const handleApply = async () => {
    setLoading(true);
    try { await onApply(); onClose(); }
    catch (err: any) { toast.error('Не удалось применить: ' + (err?.message ?? err)); }
    finally { setLoading(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white shadow-xl flex flex-col max-h-[85vh]">
        <div className="p-6 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-900">Заполнить по графику</h2>
          <p className="mt-1 text-sm text-gray-500">
            Будет создано <strong>{preview.entries_to_create.length}</strong> записей для{' '}
            <strong>{preview.employees_processed}</strong> сотрудников
            {preview.cells_skipped > 0 && ` (${preview.cells_skipped} ячеек оставлено как есть)`}
          </p>
        </div>
        <div className="overflow-y-auto flex-1 p-6">
          {byEmployee.size > 0 && (
            <table className="w-full text-sm mb-4">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b border-gray-100">
                  <th className="pb-2 font-medium">ID</th>
                  <th className="pb-2 font-medium">Дней</th>
                  <th className="pb-2 font-medium">Часов</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(byEmployee.entries()).map(([empId, info]) => (
                  <tr key={empId} className="border-b border-gray-50">
                    <td className="py-1 text-gray-700">#{empId}</td>
                    <td className="py-1 text-gray-700">{info.count}</td>
                    <td className="py-1 text-gray-700">{info.hours}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {preview.employees_skipped.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <button onClick={() => setShowSkipped((v) => !v)}
                className="text-sm font-medium text-amber-800 hover:underline">
                {preview.employees_skipped.length} сотрудников пропущено {showSkipped ? '▲' : '▼'}
              </button>
              {showSkipped && (
                <ul className="mt-2 space-y-1">
                  {preview.employees_skipped.map((s) => (
                    <li key={s.employee_id} className="text-xs text-amber-700">
                      #{s.employee_id} {s.employee_name} — {s.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {preview.entries_to_create.length === 0 && preview.employees_skipped.length === 0 && (
            <p className="text-sm text-gray-500">Нечего заполнять</p>
          )}
        </div>
        <div className="p-6 border-t border-gray-100 flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">
            Отмена
          </button>
          <button type="button" onClick={handleApply}
            disabled={loading || preview.entries_to_create.length === 0}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {loading ? 'Применяется...' : 'Применить'}
          </button>
        </div>
      </div>
    </div>
  );
}

function dayTypeLabel(t: DayType): string {
  return {
    work: 'Рабочий день',
    short: 'Сокращённый день (−1 ч)',
    weekend: 'Выходной',
    holiday: 'Праздник',
  }[t];
}

/**
 * Часы ОДНОГО рабочего места — запасной итог, если расчёт не пришёл.
 * Строки без position_id заведены до появления позиций и относятся к основной.
 */
function sumPositionHours(
  empId: number,
  positionId: number | undefined,
  entries: TimesheetEntry[],
  primaryByEmp: Map<number, number>,
): number {
  let s = 0;
  for (const e of entries) {
    if (e.employee_id !== empId) continue;
    const pid = e.position_id ?? primaryByEmp.get(e.employee_id);
    // Синтетическая позиция (id не знаем) собирает все часы сотрудника.
    if (positionId === undefined || pid === positionId) s += num(e.hours);
  }
  return s;
}

/**
 * Норма месяца по графику: часы и ДНИ (task_ux_improvements ч.1).
 *
 * Норма дней приходит с бэка (`norm_days`) и уже считается по графику позиции:
 * для weekday — плановые рабочие дни производственного календаря, для cyclic —
 * рабочие СМЕНЫ цикла (не календарные дни). Здесь только отображение — рядом с
 * часами, чтобы не добавлять колонку и не пересчитывать colspan-ы таблицы.
 */
export function NormCell({ pay }: { pay?: EmployeePayroll | null }) {
  const hours = pay?.norm_hours ? num(pay.norm_hours) : null;
  const days = pay?.norm_days ?? null;
  if (!hours && !days) return <span className="text-gray-400">—</span>;
  return (
    <span title="Норма по графику: часов и рабочих дней (смен) за месяц">
      {hours ? fmtHours(hours) : '—'}
      {days != null && (
        <span className="block text-[10px] font-sans text-gray-400 leading-tight">
          {days} дн
        </span>
      )}
    </span>
  );
}

export function DeltaCell({ delta }: { delta: number }) {
  if (delta === 0) return <span className="text-gray-400">0</span>;
  const cls = delta > 0 ? 'text-amber-600' : 'text-red-600';
  return (
    <span className={cls + ' font-semibold'}>
      {delta > 0 ? '+' : ''}
      {fmtHours(delta)}
    </span>
  );
}

export function PeriodBadge({
  period,
  onSubmit,
  onClose,
  onReturn,
  onReopen,
}: {
  period: Period;
  onSubmit: () => void;
  onClose: () => void;
  onReturn: (reason: string) => void;
  onReopen: (reason: string) => void;
}) {
  const [returnReason, setReturnReason] = useState('');
  const [reopenReason, setReopenReason] = useState('');
  const [showReturn, setShowReturn] = useState(false);
  const [showReopen, setShowReopen] = useState(false);

  const status = period.status;
  const label =
    status === 'draft'
      ? 'Черновик'
      : status === 'pending_review'
      ? 'На проверке'
      : 'Закрыт';
  const cls =
    status === 'draft'
      ? 'bg-gray-100 text-gray-700'
      : status === 'pending_review'
      ? 'bg-yellow-100 text-yellow-800'
      : 'bg-green-100 text-green-800';

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className={`px-2 py-1 rounded text-xs font-medium ${cls}`}>
        {period.department_name ?? 'Без отдела'}: {label}
      </span>

      {period.can_submit && (
        <button
          onClick={onSubmit}
          className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
        >
          Отправить на проверку
        </button>
      )}

      {period.can_close && (
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-sm rounded bg-green-600 text-white hover:bg-green-700"
        >
          Закрыть
        </button>
      )}

      {period.can_return && !showReturn && (
        <button
          onClick={() => setShowReturn(true)}
          className="px-3 py-1.5 text-sm rounded border border-orange-400 text-orange-700 hover:bg-orange-50"
        >
          Вернуть
        </button>
      )}
      {period.can_return && showReturn && (
        <div className="flex items-center gap-1">
          <input
            autoFocus
            value={returnReason}
            onChange={(e) => setReturnReason(e.target.value)}
            placeholder="Причина возврата…"
            className="border border-gray-300 rounded px-2 py-1 text-xs w-44 focus:outline-none focus:ring-1 focus:ring-orange-400"
          />
          <button
            onClick={() => { onReturn(returnReason); setShowReturn(false); setReturnReason(''); }}
            disabled={returnReason.trim().length < 3}
            className="px-2 py-1 text-xs rounded bg-orange-500 text-white hover:bg-orange-600 disabled:opacity-40"
          >
            ОК
          </button>
          <button
            onClick={() => { setShowReturn(false); setReturnReason(''); }}
            className="px-2 py-1 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
          >
            ✕
          </button>
        </div>
      )}

      {period.can_reopen && !showReopen && (
        <button
          onClick={() => setShowReopen(true)}
          className="px-3 py-1.5 text-sm rounded border border-red-400 text-red-700 hover:bg-red-50"
        >
          Переоткрыть
        </button>
      )}
      {period.can_reopen && showReopen && (
        <div className="flex items-center gap-1">
          <input
            autoFocus
            value={reopenReason}
            onChange={(e) => setReopenReason(e.target.value)}
            placeholder="Причина переоткрытия…"
            className="border border-gray-300 rounded px-2 py-1 text-xs w-44 focus:outline-none focus:ring-1 focus:ring-red-400"
          />
          <button
            onClick={() => { onReopen(reopenReason); setShowReopen(false); setReopenReason(''); }}
            disabled={reopenReason.trim().length < 3}
            className="px-2 py-1 text-xs rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-40"
          >
            ОК
          </button>
          <button
            onClick={() => { setShowReopen(false); setReopenReason(''); }}
            className="px-2 py-1 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Общий каркас чипа в ячейке дня
//
// Слот компании и код отсутствия обязаны выглядеть одинаковой «плиткой»:
// одна высота, одни паддинги, крестик всегда прижат к правому краю в колонке
// фиксированной ширины. Поэтому размеры живут здесь, а не в каждом чипе.
// ──────────────────────────────────────────────────────────────
const CHIP_FRAME =
  'flex items-center gap-1 h-[22px] px-1.5 rounded text-[11px] font-mono w-full';
// Ширина колонки крестика. Место резервируется всегда — иначе чип без права
// на удаление (закрытый период) окажется уже соседнего.
const CHIP_CLOSE_W = 'w-3 shrink-0';

function ChipClose({
  onClick,
  title,
  disabled,
}: {
  onClick: () => void;
  title: string;
  disabled: boolean;
}) {
  if (disabled) return <span className={CHIP_CLOSE_W} aria-hidden />;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`${CHIP_CLOSE_W} text-center leading-none font-sans opacity-40 hover:opacity-100`}
      title={title}
    >
      ×
    </button>
  );
}

// ──────────────────────────────────────────────────────────────
// CellPicker — ЕДИНСТВЕННЫЙ на страницу список выбора (юрлицо / код отсутствия)
// ──────────────────────────────────────────────────────────────
// До этого список рисовался внутри КАЖДОЙ ячейки: <select> с <option> на
// юрлицо и ещё один — с кодами отсутствия. На отделе в 73 человека это
// 3994 <select> и 27027 <option> из 48012 узлов страницы. Теперь в ячейке
// стоит кнопка, а список существует только пока поповер открыт.
function CellPicker({ state, onClose }: { state: PickerState | null; onClose: () => void }) {
  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    // Прокрутка увела бы список от своей ячейки — проще закрыть.
    window.addEventListener('scroll', onClose, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onClose, true);
    };
  }, [state, onClose]);

  if (!state) return null;
  // Не даём списку вылезти за нижний/правый край окна.
  const top = Math.min(state.y, window.innerHeight - 40 - state.items.length * 26);
  const left = Math.min(state.x, window.innerWidth - 220);
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        className="fixed z-50 min-w-[180px] rounded border border-gray-200 bg-white py-1 shadow-lg"
        style={{ top: Math.max(4, top), left: Math.max(4, left) }}
      >
        <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-gray-400">
          {state.title}
        </div>
        {state.items.map((it) => (
          <button
            key={it.key}
            type="button"
            onClick={() => { state.onPick(it.key); onClose(); }}
            className={
              'flex w-full items-center gap-2 px-3 py-1 text-left text-xs hover:bg-blue-50 ' +
              (it.active ? 'bg-blue-50/60 font-semibold' : '')
            }
          >
            <span className="w-9 shrink-0 font-mono font-semibold" style={{ color: it.color }}>
              {it.label}
            </span>
            {it.hint && <span className="truncate text-gray-600">{it.hint}</span>}
          </button>
        ))}
      </div>
    </>
  );
}

// ──────────────────────────────────────────────────────────────
// DayCell — ячейка одного дня одного рабочего места (мемоизирована)
// ──────────────────────────────────────────────────────────────
// Ключевая оптимизация: правка одной ячейки перерисовывает ОДНУ ячейку, а не
// все 31×N. Чтобы memo работал, все колбэки-пропсы обязаны быть стабильными
// (useCallback без зависимости от `data`), а `slots` сравниваются ПО СОДЕРЖИМОМУ
// — массив пересобирается индексом `entriesByPosDay` на каждый рендер и по
// ссылке никогда не совпал бы.
type DayCellProps = {
  day: number;
  dayType: DayType;
  slots: TimesheetEntry[];
  absence?: Absence;
  isFirst: boolean;
  editable: boolean;
  companies: Company[];
  employeeId: number;
  positionId: number | undefined;
  onSaveSlot: (empId: number, day: number, companyId: number, hours: number, positionId?: number) => void;
  onChangeCompany: (empId: number, day: number, oldCompanyId: number, newCompanyId: number, hours: number, positionId?: number) => void;
  onAddSlot: (empId: number, positionId: number | undefined, day: number) => void;
  onSetAbsence: (empId: number, day: number, kind: AbsenceKind | null) => void;
  onOpenCompanyPicker: (anchor: HTMLElement, current: number, onPick: (companyId: number) => void) => void;
  onOpenAbsencePicker: (anchor: HTMLElement, onPick: (kind: AbsenceKind) => void) => void;
};

function sameSlots(a: TimesheetEntry[], b: TimesheetEntry[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].company_id !== b[i].company_id) return false;
    if (a[i].hours !== b[i].hours) return false;
    if ((a[i].position_id ?? null) !== (b[i].position_id ?? null)) return false;
  }
  return true;
}

const DayCell = memo(function DayCell(props: DayCellProps) {
  const {
    day, dayType, slots, absence, isFirst, editable, companies,
    employeeId, positionId,
    onSaveSlot, onChangeCompany, onAddSlot, onSetAbsence,
    onOpenCompanyPicker, onOpenAbsencePicker,
  } = props;
  const isOff = dayType === 'weekend' || dayType === 'holiday';
  const bgClass =
    dayType === 'holiday'
      ? 'bg-red-50/40'
      : dayType === 'short'
      ? 'bg-yellow-50/40'
      : dayType === 'weekend'
      ? 'bg-gray-50/60'
      : '';

  return (
    <td
      className={`border border-gray-200 align-top p-1 ${bgClass}`}
      // Ширина под чип целиком (код + часы + крестик) — иначе колонки
      // дней разъезжаются по содержимому и «квадратики» выходят разными.
      style={{ minWidth: 84 }}
    >
      <div className="flex flex-col gap-1">
        {/* День с кодом отсутствия: часов в нём нет по определению.
            Код ставится на ВЕСЬ день человека (он отсутствует на всех
            работах), поэтому рисуем и снимаем его на первой строке. */}
        {absence ? (
          isFirst ? (
            <AbsenceChip
              absence={absence}
              disabled={!editable}
              onClear={() => onSetAbsence(employeeId, day, null)}
            />
          ) : null
        ) : (
          <>
            {slots.map((slot) => (
              <SlotChip
                key={`${slot.employee_id}-${slot.work_date}-${slot.company_id}`}
                slot={slot}
                companies={companies}
                disabled={!editable}
                onHoursChange={(h) => onSaveSlot(employeeId, day, slot.company_id, h, positionId)}
                onCompanyChange={(newCompId) =>
                  onChangeCompany(
                    employeeId, day, slot.company_id, newCompId, num(slot.hours), positionId,
                  )
                }
                onDelete={() => onSaveSlot(employeeId, day, slot.company_id, 0, positionId)}
                onOpenPicker={onOpenCompanyPicker}
              />
            ))}
            {editable && (
              // Та же высота, что у чипов, и та же колонка справа —
              // кнопка кода встаёт ровно под крестиками.
              <div className="flex items-center gap-1 h-[22px]">
                <button
                  type="button"
                  onClick={() => onAddSlot(employeeId, positionId, day)}
                  className={
                    'flex-1 min-w-0 h-full text-[10px] leading-none border border-dashed rounded ' +
                    (isOff
                      ? 'text-gray-300 border-gray-200 hover:text-amber-600 hover:border-amber-300'
                      : 'text-gray-400 border-gray-300 hover:text-blue-600 hover:border-blue-300')
                  }
                  title={isOff ? 'Добавить работу в выходной/праздник' : 'Добавить слот'}
                >
                  +
                </button>
                {/* Код отсутствия — на человека целиком, ставится с первой строки */}
                {isFirst && (
                  <button
                    type="button"
                    onClick={(e) =>
                      onOpenAbsencePicker(e.currentTarget, (kind) =>
                        onSetAbsence(employeeId, day, kind))
                    }
                    className="shrink-0 w-5 h-full text-center text-[10px] leading-none text-gray-400 border border-dashed border-gray-300 rounded bg-transparent hover:text-blue-600 hover:border-blue-300 cursor-pointer"
                    title="Поставить код отсутствия (часы этого дня будут убраны)"
                  >
                    ·
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </td>
  );
}, (prev, next) =>
  prev.day === next.day &&
  prev.dayType === next.dayType &&
  prev.isFirst === next.isFirst &&
  prev.editable === next.editable &&
  prev.companies === next.companies &&
  prev.employeeId === next.employeeId &&
  prev.positionId === next.positionId &&
  prev.absence?.kind === next.absence?.kind &&
  prev.absence?.over_limit === next.absence?.over_limit &&
  prev.onSaveSlot === next.onSaveSlot &&
  prev.onChangeCompany === next.onChangeCompany &&
  prev.onAddSlot === next.onAddSlot &&
  prev.onSetAbsence === next.onSetAbsence &&
  prev.onOpenCompanyPicker === next.onOpenCompanyPicker &&
  prev.onOpenAbsencePicker === next.onOpenAbsencePicker &&
  sameSlots(prev.slots, next.slots)
);

// ──────────────────────────────────────────────────────────────
// SlotChip — один слот компании в ячейке дня
// ──────────────────────────────────────────────────────────────
function SlotChip({
  slot,
  companies,
  disabled,
  onHoursChange,
  onCompanyChange,
  onDelete,
  onOpenPicker,
}: {
  slot: TimesheetEntry;
  companies: Company[];
  disabled: boolean;
  onHoursChange: (hours: number) => void;
  onCompanyChange: (newCompanyId: number) => void;
  onDelete: () => void;
  onOpenPicker: (anchor: HTMLElement, current: number, onPick: (companyId: number) => void) => void;
}) {
  const col = getCompanyColor(slot.company_id, companies);
  const company = companies.find((c) => c.id === slot.company_id);
  const [hours, setHours] = useState<string>(String(slot.hours ?? ''));

  useEffect(() => {
    setHours(String(slot.hours ?? ''));
  }, [slot.hours]);

  const handleBlur = () => {
    const parsed = parseFloat(hours);
    if (Number.isNaN(parsed) || parsed < 0) {
      setHours(String(slot.hours));
      return;
    }
    // Часы только целые — округляем введённое значение
    const n = Math.min(24, Math.round(parsed));
    if (String(n) !== hours) setHours(String(n));
    if (n === num(slot.hours)) return;
    onHoursChange(n);
  };

  return (
    <div
      className={CHIP_FRAME}
      style={{
        background: col.bg,
        color: col.color,
        border: `1px solid ${col.color}40`,
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {/* Кнопка вместо <select>: список юрлиц рисуется одним поповером на всю
          страницу (CellPicker). Раньше каждый чип нёс свой список — на отделе
          в 73 человека это 27 тыс. <option>, больше половины DOM страницы.
          min-w — чтобы код не схлопнулся, даже если колонка дня узкая. */}
      <button
        type="button"
        onClick={(e) => onOpenPicker(e.currentTarget, slot.company_id, onCompanyChange)}
        disabled={disabled}
        title={`Компания: ${company ? companyLabel(company) : slot.company_id}`}
        className="bg-transparent border-0 outline-none cursor-pointer p-0 h-full flex-1 min-w-[26px] text-left text-[11px] font-semibold disabled:cursor-default"
        style={{ color: col.color }}
      >
        {company?.code ?? slot.company_id}
      </button>
      <input
        type="number"
        value={hours}
        onChange={(e) => setHours(e.target.value)}
        onBlur={handleBlur}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        disabled={disabled}
        min={0}
        max={24}
        step={1}
        title="Часы"
        // Спиннеры number-инпута тут не нужны — они распирают чип.
        className="bg-transparent border-0 outline-none p-0 h-full text-right w-5 shrink-0 text-[11px] font-mono tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
        style={{ color: col.color }}
      />
      <ChipClose onClick={onDelete} title="Удалить" disabled={disabled} />
    </div>
  );
}


// ──────────────────────────────────────────────────────────────
// AbsenceChip — день с кодом отсутствия (ОТ / ДО / Б / Н)
// ──────────────────────────────────────────────────────────────
function AbsenceChip({
  absence,
  disabled,
  onClear,
}: {
  absence: Absence;
  disabled: boolean;
  onClear: () => void;
}) {
  const meta = absenceMeta(absence.kind);
  // Больничный сверх годового лимита — за свой счёт: гасим цвет и метим «*»
  const over = !!absence.over_limit;
  const bg = over ? '#f3f4f6' : meta?.bg ?? '#e5e7eb';
  const color = over ? '#6b7280' : meta?.color ?? '#4b5563';

  return (
    <div
      className={`${CHIP_FRAME} font-bold`}
      style={{
        background: bg,
        color,
        border: over ? `1px dashed ${color}80` : `1px solid ${color}40`,
        opacity: disabled ? 0.7 : 1,
      }}
      title={
        over
          ? 'Больничный сверх годового лимита — за свой счёт, не оплачивается'
          : meta?.label ?? absence.code
      }
    >
      {/* Код центрируется в оставшемся месте, крестик — в той же колонке
          справа, что и у слота компании. */}
      <span className="flex-1 min-w-0 text-center">
        {absence.code}
        {over && <span className="font-sans">*</span>}
      </span>
      <ChipClose onClick={onClear} title="Убрать отметку" disabled={disabled} />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// NightRow — строка «Ночные» под рабочим местом (task_night_shifts_rework)
// ──────────────────────────────────────────────────────────────
//
// Ночная смена — отдельная подработка: к графику не привязана, часов не даёт и
// сосуществует с дневной работой того же дня. Поэтому здесь не слоты с часами,
// а галочка на день: отметил выход в ночь — снял.
//
// Ставка вручную не задаётся, она вычисляется из фонда отдела (фонд ÷
// календарные дни месяца), а лимит числа смен по отделу общий на всех — его
// остаток и показываем в хвосте строки. Проверяет лимит БЭК: две вкладки
// иначе перерасходовали бы фонд.
function NightRow({
  emp,
  position,
  numDays,
  dayTypes,
  marked,
  absenceCode,
  fund,
  editable,
  onToggle,
}: {
  emp: Employee;
  position: Position;
  numDays: number;
  dayTypes: Record<number, DayType>;
  marked: (day: number) => boolean;
  /** код отсутствия этого дня (ОТ/ДО/Б/Н), если он стоит — ночную не отметить */
  absenceCode: (day: number) => string | undefined;
  fund: NightFund | null;
  editable: boolean;
  onToggle: (day: number, value: boolean) => void;
}) {
  const noFund = fund != null && fund.limit_shifts === 0;

  return (
    <tr className="bg-indigo-50/40">
      <td
        className="border border-gray-200 px-2 py-1 text-[11px] text-indigo-700"
        colSpan={3}
        title={
          'Ночные смены: отдельная подработка, к графику не привязана и с ' +
          'дневными часами сосуществует. Ставка — из фонда отдела.'
        }
      >
        🌙 Ночные
        {fund && (
          <span className="ml-2 text-[10px] text-gray-500">
            {noFund
              ? 'фонд отдела не задан'
              : `осталось ${fund.remaining_shifts} из ${fund.limit_shifts} смен`}
          </span>
        )}
      </td>

      {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => {
        const on = marked(d);
        // Отсутствие — на весь день человека: в такой день он не выходит ни
        // днём, ни в ночь. Бэк это тоже не пропустит (422), здесь просто не
        // даём кликнуть и объясняем почему.
        const absent = absenceCode(d);
        const t = dayTypes[d];
        const bgClass =
          t === 'holiday'
            ? 'bg-red-50/30'
            : t === 'weekend'
            ? 'bg-gray-50/40'
            : '';
        return (
          <td key={d} className={`border border-gray-200 text-center p-0.5 ${bgClass}`}>
            <input
              type="checkbox"
              checked={on}
              disabled={!editable || !!absent}
              onChange={(e) => onToggle(d, e.target.checked)}
              className="cursor-pointer accent-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
              title={
                absent
                  ? `В этот день отмечено отсутствие (${absent}) — ночную смену не отметить`
                  : editable
                  ? `${emp.full_name} — ${position.display_title}: выход в ночь ${d}-го`
                  : 'Период закрыт для редактирования'
              }
            />
          </td>
        );
      })}

      {/* Ячеек «Итого ч» и блока справа здесь НЕТ: они растянуты на две строки
          со строки рабочего места (rowSpan). Пустые ячейки на их месте читались
          как полоса через всю таблицу — в сводном блоке строки «Ночные» быть не
          должно, там просто выше колонки. */}
    </tr>
  );
}

// ──────────────────────────────────────────────────────────────
// AbsencePicker — выбор кода отсутствия вместо часов

// ──────────────────────────────────────────────────────────────
// AdjustmentsModal — у каждого столбца своя категория:
//   'premium'   → только премии
//   'kpi'       → только KPI
//   'deduction' → аванс + правка займа
// ──────────────────────────────────────────────────────────────
const KIND_LABELS: Record<string, string> = {
  premium: 'Премия',
  kpi: 'KPI',
  advance: 'Аванс',
};

const CATEGORY_TITLE: Record<string, string> = {
  premium: 'Премии',
  kpi: 'KPI',
  deduction: 'Удержания (аванс и займ)',
};

function AdjustmentsModal({
  employee,
  position,
  category,
  year,
  month,
  payroll,
  adjustments,
  onClose,
  onChanged,
}: {
  employee: Employee;
  /** премия/KPI/аванс адресуются рабочему месту, на котором заработаны */
  position: Position;
  category: 'premium' | 'kpi' | 'deduction';
  year: number;
  month: number;
  payroll: EmployeePayroll | null;
  adjustments: Adjustment[];
  onClose: () => void;
  onChanged: () => void;
}) {
  // Тип записи для этой категории (аванс — это удержание)
  const kind: 'premium' | 'kpi' | 'advance' = category === 'deduction' ? 'advance' : category;
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [loanInput, setLoanInput] = useState('');

  // В списке показываем только записи этой категории
  const shownAdjustments = adjustments.filter((a) => a.kind === kind);
  const hasLoan = category === 'deduction' && !!employee.loan_amount && num(employee.loan_amount) > 0;
  const addLabel = kind === 'advance' ? 'Аванс (удержание)' : KIND_LABELS[kind];

  const add = async () => {
    const amt = parseFloat(amount);
    if (!Number.isFinite(amt) || amt <= 0) {
      toast.error('Введите сумму больше нуля');
      return;
    }
    if (reason.trim().length < 3) {
      toast.error('Обоснование обязательно (минимум 3 символа)');
      return;
    }
    setBusy(true);
    try {
      await timesheetApi.createAdjustment({
        employee_id: employee.id, position_id: positionIdParam(position) ?? null,
        year, month, kind,
        amount: String(amt), reason: reason.trim(),
      });
      setAmount('');
      setReason('');
      toast.success('Добавлено');
      onChanged();
    } catch (err: any) {
      toast.error('Не удалось добавить: ' + (err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setBusy(true);
    try {
      await timesheetApi.deleteAdjustment(id);
      onChanged();
    } catch (err: any) {
      toast.error('Не удалось удалить: ' + (err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const applyLoanOverride = async () => {
    const amt = parseFloat(loanInput);
    if (!Number.isFinite(amt) || amt < 0) {
      toast.error('Введите сумму удержания (≥ 0)');
      return;
    }
    setBusy(true);
    try {
      await timesheetApi.setLoanOverride({
        employee_id: employee.id, year, month, actual_amount: String(amt),
      });
      setLoanInput('');
      toast.success('Удержание по займу обновлено');
      onChanged();
    } catch (err: any) {
      toast.error('Не удалось: ' + (err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  const clearLoanOverride = async () => {
    setBusy(true);
    try {
      await timesheetApi.clearLoanOverride(employee.id, year, month);
      toast.success('Возвращено плановое удержание');
      onChanged();
    } catch (err: any) {
      toast.error('Не удалось: ' + (err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {CATEGORY_TITLE[category]} · {employee.full_name}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          {MONTH_NAMES_RU[month - 1]} {year}
          {/* Начисление попадает в «к выплате» ЭТОГО рабочего места */}
          {position.id > 0 && <> · рабочее место: <strong>{position.display_title}</strong></>}
        </p>

        {/* Существующие записи этой категории */}
        <div className="mb-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">{addLabel}</p>
          {shownAdjustments.length === 0 ? (
            <p className="text-sm text-gray-400">Пока ничего не добавлено</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {shownAdjustments.map((a) => (
                <div key={a.id} className="flex items-center gap-2 text-sm border border-gray-200 rounded px-2 py-1.5">
                  <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${a.kind === 'advance' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'}`}>
                    {KIND_LABELS[a.kind]}
                  </span>
                  <span className="font-mono font-semibold">
                    {a.kind === 'advance' ? '−' : '+'}{fmtMoney(a.amount)}
                  </span>
                  <span className="flex-1 text-gray-600 truncate" title={a.reason}>{a.reason}</span>
                  <button onClick={() => remove(a.id)} disabled={busy} className="text-gray-400 hover:text-red-600 text-base leading-none">×</button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Форма добавления — только эта категория, без выбора типа */}
        <div className="mb-5 border border-gray-200 rounded-lg p-3 bg-gray-50">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Добавить: {addLabel}</p>
          <div className="flex flex-col gap-2">
            <div className="flex gap-2">
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="Сумма ₽"
                min={0}
                className="flex-1 border border-gray-300 rounded px-2 py-1.5 text-sm"
              />
            </div>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Обоснование (обязательно)"
              className="border border-gray-300 rounded px-2 py-1.5 text-sm"
            />
            <button
              onClick={add}
              disabled={busy}
              className="self-end px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Добавить
            </button>
          </div>
        </div>

        {/* Займ */}
        {hasLoan && (
          <div className="border border-gray-200 rounded-lg p-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Займ</p>
            <div className="text-sm text-gray-700 flex flex-col gap-1 mb-3">
              <div className="flex justify-between"><span>Сумма займа</span><span className="font-mono">{fmtMoney(employee.loan_amount ?? null)}</span></div>
              <div className="flex justify-between"><span>Плановая доля / мес.</span><span className="font-mono">{fmtMoney(payroll?.loan_planned_deduction ?? null)}</span></div>
              <div className="flex justify-between">
                <span>Удержано в этом месяце</span>
                <span className="font-mono font-semibold">
                  {fmtMoney(payroll?.loan_deduction ?? null)}
                  {payroll?.loan_is_manual && <span className="ml-1 text-[10px] text-amber-600">(вручную)</span>}
                </span>
              </div>
              <div className="flex justify-between"><span>Остаток после месяца</span><span className="font-mono">{fmtMoney(payroll?.loan_remaining ?? null)}</span></div>
            </div>
            <div className="flex gap-2 items-center">
              <input
                type="number"
                value={loanInput}
                onChange={(e) => setLoanInput(e.target.value)}
                placeholder="Удержать в этом месяце ₽"
                min={0}
                className="flex-1 border border-gray-300 rounded px-2 py-1.5 text-sm"
              />
              <button onClick={applyLoanOverride} disabled={busy} className="px-3 py-1.5 text-sm rounded bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50">
                Применить
              </button>
              {payroll?.loan_is_manual && (
                <button onClick={clearLoanOverride} disabled={busy} className="px-3 py-1.5 text-sm rounded border border-gray-300 text-gray-600 hover:bg-gray-50">
                  Сбросить
                </button>
              )}
            </div>
            <p className="mt-2 text-xs text-gray-400">Правка меняет только этот месяц. Остаток = сумма − фактически удержанное, поэтому займ гасится дольше.</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default TimesheetPage;
