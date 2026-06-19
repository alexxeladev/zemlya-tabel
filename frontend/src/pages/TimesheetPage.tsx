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

import { Fragment, useEffect, useMemo, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../store/auth';
import { toast } from '../store/toasts';
import { timesheetApi } from '../api/timesheet';
import { apiClient } from '../api/client';
import { listDepartments } from '../api/departments';
import { companyColorByIndex } from '../utils/colors';
import { useTimesheetViewStore } from '../store/timesheetView';
import { TimesheetCompanyView } from './TimesheetCompanyView';

// ──────────────────────────────────────────────────────────────
// Типы (минимальные, чтобы не зависеть от уточнений в api.ts)
// ──────────────────────────────────────────────────────────────
export type Employee = {
  id: number;
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

export type Adjustment = {
  id: number;
  employee_id: number;
  year: number;
  month: number;
  kind: 'premium' | 'kpi' | 'advance';
  amount: string;
  reason: string;
};

export type Company = { id: number; code: string; name: string };

export type TimesheetEntry = {
  employee_id: number;
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
  holiday_amount?: string;
  overtime_hours?: string;
  holiday_hours?: string;
  total: string;
};

export type EmployeePayroll = {
  employee_id: number;
  total_hours: string;
  norm_hours: string | null;
  delta_hours: string | null;
  base_amount: string;
  overtime_amount: string;
  holiday_amount: string;
  total_amount: string;
  weekend_pay_type?: 'coefficient' | 'fixed_rate' | null;
  weekend_coefficient?: string | null;
  weekend_fixed_rate?: string | null;
  premium_amount?: string;
  kpi_amount?: string;
  advance_deduction?: string;
  loan_deduction?: string;
  loan_remaining?: string;
  loan_planned_deduction?: string;
  loan_is_manual?: boolean;
  total_deductions?: string;
  net_payout?: string;
  breakdown_by_company: CompanyBreakdown[];
  is_calculable: boolean;
  reason_if_not_calculable: string | null;
};

type PayrollSummary = {
  employees: EmployeePayroll[];
  total_hours: string;
  total_base_amount: string;
  total_overtime_amount: string;
  total_holiday_amount: string;
  grand_total: string;
  total_premium?: string;
  total_kpi?: string;
  total_deductions?: string;
  total_net_payout?: string;
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
  payroll: PayrollSummary | null;
  periods: Period[];
  adjustments?: Adjustment[];
};

type CalendarSummary = {
  days: Array<{ day: number; type: DayType; weekday: number }>;
};

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

// Коэффициент/режим оплаты выходных сотрудника — для колонки «Коэф.» (п.3)
export function fmtCoeff(pay?: EmployeePayroll | null): string {
  if (!pay) return '—';
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
  const canSeeMoney = role === 'admin' || role === 'accountant' || role === 'manager';
  const canExport = role === 'admin' || role === 'accountant' || role === 'manager';
  const canSelectDept = role === 'admin' || role === 'accountant';

  const viewMode = useTimesheetViewStore((s) => s.mode);
  const setViewMode = useTimesheetViewStore((s) => s.setMode);

  // ── Начальное состояние из URL (?year=&month=&department_id=) — для перехода из «Задач» ──
  const [searchParams] = useSearchParams();
  const now = new Date();
  const [year, setYear] = useState(() => {
    const y = parseInt(searchParams.get('year') ?? '', 10);
    return y >= 2000 && y <= 2100 ? y : now.getFullYear();
  });
  const [month, setMonth] = useState(() => {
    const m = parseInt(searchParams.get('month') ?? '', 10);
    return m >= 1 && m <= 12 ? m : now.getMonth() + 1;
  });
  const [departmentFilter, setDepartmentFilter] = useState<number | null>(() => {
    const d = parseInt(searchParams.get('department_id') ?? '', 10);
    return Number.isFinite(d) ? d : null;
  });

  const [data, setData] = useState<MonthResponse | null>(null);
  const [calendar, setCalendar] = useState<CalendarSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  // ── Загрузка данных ──
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [monthData, cal] = await Promise.all([
        timesheetApi.getMonth(year, month, {
          department_id: departmentFilter ?? undefined,
          include_payroll: canSeeMoney,
        }) as Promise<MonthResponse>,
        apiClient.get<CalendarSummary>(`/api/calendar/${year}/${month}/summary`)
          .then(r => r.data)
          .catch(() => ({ days: [] } as CalendarSummary)),
      ]);
      setData(monthData);
      setCalendar(cal);
    } catch (err: any) {
      toast.error('Ошибка загрузки табеля: ' + (err?.message ?? err));
    } finally {
      setLoading(false);
    }
  }, [year, month, departmentFilter, canSeeMoney]);

  useEffect(() => {
    reload();
  }, [reload]);

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

  // ── Индекс entries для быстрого доступа ──
  const entriesByEmpDay = useMemo(() => {
    const map = new Map<string, TimesheetEntry[]>();
    if (!data) return map;
    for (const e of data.entries) {
      const day = parseInt(e.work_date.slice(-2), 10);
      const key = `${e.employee_id}:${day}`;
      const arr = map.get(key);
      if (arr) arr.push(e);
      else map.set(key, [e]);
    }
    return map;
  }, [data]);

  const payrollByEmp = useMemo(() => {
    const map = new Map<number, EmployeePayroll>();
    if (!data?.payroll) return map;
    for (const p of data.payroll.employees) map.set(p.employee_id, p);
    return map;
  }, [data]);

  // Премии/KPI/авансы по сотруднику (задача 3.11a)
  const adjByEmp = useMemo(() => {
    const map = new Map<number, Adjustment[]>();
    for (const a of data?.adjustments ?? []) {
      if (!map.has(a.employee_id)) map.set(a.employee_id, []);
      map.get(a.employee_id)!.push(a);
    }
    return map;
  }, [data]);

  // Сотрудник, для которого открыт модал управления премиями/KPI/авансом/займом
  const [adjEmp, setAdjEmp] = useState<Employee | null>(null);

  // ── Видимые сотрудники (бэк уже исключил системных админов и применил видимость) ──
  const visibleEmployees = useMemo(() => {
    if (!data) return [] as Employee[];
    return data.employees.filter((e) => !e.is_system_admin);
  }, [data]);

  const visibleEmpIds = useMemo(
    () => new Set(visibleEmployees.map((e) => e.id)),
    [visibleEmployees]
  );

  // ── Группировка по отделам (Bug 5): только при «Все отделы» для admin/accountant ──
  const grouped = canSelectDept && departmentFilter === null;
  const groups = useMemo(() => {
    const byDept = new Map<number | null, Employee[]>();
    for (const e of visibleEmployees) {
      const k = e.department_id ?? null;
      if (!byDept.has(k)) byDept.set(k, []);
      byDept.get(k)!.push(e);
    }
    const entries = Array.from(byDept.entries());
    entries.sort((a, b) => {
      if (a[0] === null) return 1; // «Без отдела» — в самый низ
      if (b[0] === null) return -1;
      const na = a[1][0]?.department?.name ?? '';
      const nb = b[1][0]?.department?.name ?? '';
      return na.localeCompare(nb, 'ru');
    });
    return entries.map(([deptId, emps]) => ({
      deptId,
      name: deptId === null ? 'Без отдела' : emps[0]?.department?.name ?? `Отдел ${deptId}`,
      employees: emps,
      period: data?.periods.find((p) => p.department_id === deptId) ?? null,
    }));
  }, [visibleEmployees, data]);

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

  // ── Действия со слотами ──
  const saveSlot = useCallback(
    async (employeeId: number, day: number, companyId: number, hours: number) => {
      try {
        await timesheetApi.saveCell({
          employee_id: employeeId,
          work_date: dateStr(year, month, day),
          company_id: companyId,
          hours,
        });
        await reload();
      } catch (err: any) {
        toast.error('Не удалось сохранить: ' + (err?.message ?? err));
      }
    },
    [year, month, reload]
  );

  const changeSlotCompany = useCallback(
    async (employeeId: number, day: number, oldCompanyId: number, newCompanyId: number, hours: number) => {
      try {
        await timesheetApi.saveCell({
          employee_id: employeeId,
          work_date: dateStr(year, month, day),
          company_id: oldCompanyId,
          hours: 0,
        });
        await timesheetApi.saveCell({
          employee_id: employeeId,
          work_date: dateStr(year, month, day),
          company_id: newCompanyId,
          hours,
        });
        await reload();
      } catch (err: any) {
        toast.error('Не удалось сменить компанию: ' + (err?.message ?? err));
      }
    },
    [year, month, reload]
  );

  const addSlot = useCallback(
    (employeeId: number, day: number) => {
      if (!data) return;
      const existing = entriesByEmpDay.get(`${employeeId}:${day}`) ?? [];
      const used = new Set(existing.map((e) => e.company_id));
      // выбираем первую доступную компанию (default если свободна, иначе любую свободную)
      const emp = data.employees.find((e) => e.id === employeeId);
      let chosen: Company | undefined;
      if (emp?.default_company_id && !used.has(emp.default_company_id)) {
        chosen = data.companies.find((c) => c.id === emp.default_company_id);
      }
      if (!chosen) {
        chosen = data.companies.find((c) => !used.has(c.id));
      }
      if (!chosen) {
        toast.info('Нет свободных компаний');
        return;
      }
      // дефолтное значение часов — длительность смены сотрудника или 8
      const def = emp?.schedule?.hours_per_shift ?? 8;
      saveSlot(employeeId, day, chosen.id, def);
    },
    [data, entriesByEmpDay, saveSlot]
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

  if (loading && !data) {
    return <div className="p-8 text-gray-500">Загрузка…</div>;
  }
  if (!data) {
    return <div className="p-8 text-gray-500">Нет данных</div>;
  }

  const periodForDept = (deptId: number | null) =>
    data.periods.find((p) => p.department_id === deptId);

  // Денежный блок: Коэф,Норма,Δ,Оклад,Сверхур,Праздн,Итого₽,Премии/KPI,Удержано,К выплате
  const totalCols = 3 + numDays + (canSeeMoney ? 11 : 1);

  const renderEmployeeRow = (emp: Employee) => {
    const pay = payrollByEmp.get(emp.id);
    const empTotal = num(pay?.total_hours, 0) || sumEmployeeHours(emp.id, data.entries);
    const periodEditable = periodForDept(emp.department_id)?.can_edit ?? false;
    const noSchedule = !emp.schedule;

    return (
      <tr
        key={emp.id}
        className="hover:bg-blue-50/30"
        title={noSchedule ? 'График не задан, автозаполнение по графику недоступно' : undefined}
      >
        {/* ── Sticky-колонка сотрудника ── */}
        <td
          className="sticky left-0 bg-white border border-gray-200 px-3 py-2 font-medium text-gray-900"
          style={{ minWidth: 200, zIndex: 10 }}
          title={emp.full_name}
        >
          <div className="truncate max-w-[200px]">{emp.full_name}</div>
        </td>
        <td className="border border-gray-200 px-2 py-2 text-xs text-gray-600">
          {emp.department?.name ?? '—'}
        </td>
        <td className="border border-gray-200 px-2 py-2 text-xs text-center font-mono text-gray-600">
          {noSchedule ? (
            <span className="italic text-gray-400 font-sans">не задан</span>
          ) : (
            emp.schedule?.name
          )}
        </td>

        {/* ── Дни ── */}
        {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => {
          const t = dayTypes[d];
          const slots = entriesByEmpDay.get(`${emp.id}:${d}`) ?? [];
          const isOff = t === 'weekend' || t === 'holiday';

          const bgClass =
            t === 'holiday'
              ? 'bg-red-50/40'
              : t === 'short'
              ? 'bg-yellow-50/40'
              : t === 'weekend'
              ? 'bg-gray-50/60'
              : '';

          return (
            <td
              key={d}
              className={`border border-gray-200 align-top p-1 ${bgClass}`}
              style={{ minWidth: 60 }}
            >
              <div className="flex flex-col gap-1">
                {slots.map((slot) => (
                  <SlotChip
                    key={`${slot.employee_id}-${slot.work_date}-${slot.company_id}`}
                    slot={slot}
                    companies={data.companies}
                    disabled={!periodEditable}
                    onHoursChange={(h) => saveSlot(emp.id, d, slot.company_id, h)}
                    onCompanyChange={(newCompId) =>
                      changeSlotCompany(emp.id, d, slot.company_id, newCompId, num(slot.hours))
                    }
                    onDelete={() => saveSlot(emp.id, d, slot.company_id, 0)}
                  />
                ))}
                {periodEditable && !isOff && (
                  <button
                    type="button"
                    onClick={() => addSlot(emp.id, d)}
                    className="text-[10px] text-gray-400 border border-dashed border-gray-300 rounded px-1 py-0.5 hover:text-blue-600 hover:border-blue-300"
                    title="Добавить слот"
                  >
                    +
                  </button>
                )}
                {periodEditable && isOff && slots.length === 0 && (
                  <button
                    type="button"
                    onClick={() => addSlot(emp.id, d)}
                    className="text-[10px] text-gray-300 border border-dashed border-gray-200 rounded px-1 py-0.5 hover:text-amber-600 hover:border-amber-300"
                    title="Добавить работу в выходной/праздник"
                  >
                    +
                  </button>
                )}
              </div>
            </td>
          );
        })}

        {/* ── Итого часов ── */}
        <td className="border border-gray-200 px-3 py-2 text-center font-mono font-semibold bg-gray-50">
          {fmtHours(empTotal)}
        </td>

        {/* ── Финансы ── */}
        {canSeeMoney && (() => {
          const adjs = adjByEmp.get(emp.id) ?? [];
          const bonus = num(pay?.premium_amount) + num(pay?.kpi_amount);
          const deductions = num(pay?.total_deductions);
          return (
          <>
            <td className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-600" title="Оплата выходных">
              {fmtCoeff(pay)}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-600">
              {pay?.norm_hours ? fmtHours(num(pay.norm_hours)) : '—'}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-center font-mono text-xs">
              {pay?.delta_hours ? <DeltaCell delta={num(pay.delta_hours)} /> : '—'}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-right font-mono text-xs">
              {fmtMoney(pay?.base_amount ?? null)}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-right font-mono text-xs">
              {fmtMoney(pay?.overtime_amount ?? null)}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-right font-mono text-xs">
              {fmtMoney(pay?.holiday_amount ?? null)}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-right font-mono font-semibold text-blue-700 bg-blue-50/30">
              {fmtMoney(pay?.total_amount ?? null)}
            </td>
            {/* Премии/KPI — клик открывает модал управления */}
            <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
              <button
                type="button"
                onClick={() => setAdjEmp(emp)}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-blue-50 text-gray-700"
                title="Премии, KPI, аванс, займ"
              >
                <span>{bonus > 0 ? fmtMoney(String(bonus)) : '—'}</span>
                <span className="text-blue-500 font-sans">✎</span>
                {adjs.length > 0 && (
                  <span className="rounded-full bg-gray-200 text-gray-600 text-[9px] px-1">{adjs.length}</span>
                )}
              </button>
            </td>
            <td className="border border-gray-200 px-2 py-2 text-right font-mono text-xs text-red-600">
              {deductions > 0 ? '−' + fmtMoney(String(deductions)) : '—'}
            </td>
            <td className="border border-gray-200 px-2 py-2 text-right font-mono font-bold text-emerald-700 bg-emerald-50/40">
              {pay?.is_calculable ? fmtMoney(pay?.net_payout ?? null) : '—'}
            </td>
          </>
          );
        })()}
      </tr>
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
          {canSelectDept && departments.length > 0 && (
            <select
              className="border border-gray-300 rounded px-2 py-1 text-sm"
              value={departmentFilter ?? ''}
              onChange={(e) =>
                setDepartmentFilter(e.target.value === '' ? null : parseInt(e.target.value, 10))
              }
            >
              <option value="">Все отделы</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
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
              className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50"
              onClick={() =>
                toast.info('Кнопка автозаполнения — см. отдельный модал на странице')
              }
            >
              Заполнить по графику
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
                {c.code} — {c.name}
              </span>
            );
          })}
          <span className="text-gray-500">
            «+» = добавить слот компании · серый = выходной
          </span>
        </div>
      </div>

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
            visibleEmployees={visibleEmployees}
            grouped={grouped}
            groups={groups}
            payrollByEmp={payrollByEmp}
            canSeeMoney={canSeeMoney}
            saveSlot={saveSlot}
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
                    style={{ minWidth: 60, zIndex: 20 }}
                  >
                    Норма
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
                    Праздн.
                  </th>
                  <th
                    className="sticky top-0 bg-blue-50 border border-gray-200 px-2 py-2 text-right font-semibold text-blue-700"
                    style={{ minWidth: 100, zIndex: 20 }}
                  >
                    Итого ₽
                  </th>
                  <th
                    className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600"
                    style={{ minWidth: 110, zIndex: 20 }}
                    title="Премии и KPI"
                  >
                    Премии/KPI
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
            </tr>
          </thead>

          {/* ===== ТЕЛО ===== */}
          <tbody>
            {visibleEmployees.length === 0 && (
              <tr>
                <td
                  colSpan={3 + numDays + (canSeeMoney ? 7 : 1)}
                  className="text-center text-gray-500 py-10"
                >
                  Нет сотрудников
                </td>
              </tr>
            )}

            {grouped
              ? groups.map((g) => (
                  <Fragment key={`grp-${g.deptId ?? 'null'}`}>
                    {renderGroupDivider(g.deptId, g.name, g.period)}
                    {g.employees.map((emp) => renderEmployeeRow(emp))}
                  </Fragment>
                ))
              : visibleEmployees.map((emp) => renderEmployeeRow(emp))}

            {/* ===== ИТОГО строка ===== */}
            {visibleEmployees.length > 0 && (
              <tr className="bg-gray-100 font-semibold">
                <td
                  className="sticky left-0 bg-gray-200 border border-gray-300 px-3 py-2"
                  style={{ minWidth: 200, zIndex: 10 }}
                >
                  ИТОГО
                </td>
                <td className="border border-gray-300 px-2 py-2" colSpan={2}></td>
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
                      {fmtMoney(data.payroll.total_holiday_amount)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-blue-700 bg-blue-100">
                      {fmtMoney(data.payroll.grand_total)}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono">
                      {fmtMoney(String(num(data.payroll.total_premium) + num(data.payroll.total_kpi)))}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono text-red-600">
                      {num(data.payroll.total_deductions) > 0 ? '−' + fmtMoney(data.payroll.total_deductions ?? null) : '—'}
                    </td>
                    <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-emerald-700 bg-emerald-100">
                      {fmtMoney(data.payroll.total_net_payout ?? null)}
                    </td>
                  </>
                ) : (
                  [0,1,2,3,4,5,6,7,8,9].map(i => <td key={i} className="border border-gray-300 px-2 py-2" />)
                ))}
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
          <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200">
            По компаниям
          </div>
          {data.companies.map((c) => {
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
                <span className="flex items-center gap-1.5 w-52 font-mono" style={{ color: col.color }}>
                  <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: col.color }} />
                  {c.code} — {c.name}
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

      {/* ── Модал управления премиями/KPI/авансом/займом (задача 3.11a) ── */}
      {adjEmp && (
        <AdjustmentsModal
          employee={adjEmp}
          year={year}
          month={month}
          payroll={payrollByEmp.get(adjEmp.id) ?? null}
          adjustments={adjByEmp.get(adjEmp.id) ?? []}
          onClose={() => setAdjEmp(null)}
          onChanged={() => reload()}
        />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Подкомпоненты
// ──────────────────────────────────────────────────────────────

function dayTypeLabel(t: DayType): string {
  return {
    work: 'Рабочий день',
    short: 'Сокращённый день (−1 ч)',
    weekend: 'Выходной',
    holiday: 'Праздник',
  }[t];
}

function sumEmployeeHours(empId: number, entries: TimesheetEntry[]): number {
  let s = 0;
  for (const e of entries) {
    if (e.employee_id === empId) s += num(e.hours);
  }
  return s;
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
// SlotChip — один слот компании в ячейке дня
// ──────────────────────────────────────────────────────────────
function SlotChip({
  slot,
  companies,
  disabled,
  onHoursChange,
  onCompanyChange,
  onDelete,
}: {
  slot: TimesheetEntry;
  companies: Company[];
  disabled: boolean;
  onHoursChange: (hours: number) => void;
  onCompanyChange: (newCompanyId: number) => void;
  onDelete: () => void;
}) {
  const col = getCompanyColor(slot.company_id, companies);
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
      className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono"
      style={{
        background: col.bg,
        color: col.color,
        border: `1px solid ${col.color}40`,
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <select
        value={slot.company_id}
        onChange={(e) => onCompanyChange(parseInt(e.target.value, 10))}
        disabled={disabled}
        className="bg-transparent border-0 outline-none cursor-pointer pr-0 text-[11px] font-semibold"
        style={{ color: col.color, width: 36 }}
      >
        {companies.map((c) => (
          <option key={c.id} value={c.id}>
            {c.code}
          </option>
        ))}
      </select>
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
        className="bg-transparent border-0 outline-none text-center w-9 text-[11px] font-mono"
        style={{ color: col.color }}
      />
      {!disabled && (
        <button
          type="button"
          onClick={onDelete}
          className="opacity-40 hover:opacity-100 leading-none"
          title="Удалить"
        >
          ×
        </button>
      )}
    </div>
  );
}


// ──────────────────────────────────────────────────────────────
// AdjustmentsModal — премии / KPI / аванс / правка займа за месяц
// ──────────────────────────────────────────────────────────────
const KIND_LABELS: Record<string, string> = {
  premium: 'Премия',
  kpi: 'KPI',
  advance: 'Аванс (удержание)',
};

function AdjustmentsModal({
  employee,
  year,
  month,
  payroll,
  adjustments,
  onClose,
  onChanged,
}: {
  employee: Employee;
  year: number;
  month: number;
  payroll: EmployeePayroll | null;
  adjustments: Adjustment[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [kind, setKind] = useState<'premium' | 'kpi' | 'advance'>('premium');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [loanInput, setLoanInput] = useState('');

  const hasLoan = !!employee.loan_amount && num(employee.loan_amount) > 0;

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
        employee_id: employee.id, year, month, kind,
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
            Премии и удержания · {employee.full_name}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
        </div>
        <p className="text-xs text-gray-500 mb-4">{MONTH_NAMES_RU[month - 1]} {year}</p>

        {/* Существующие премии/KPI/авансы */}
        <div className="mb-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Начисления и удержания</p>
          {adjustments.length === 0 ? (
            <p className="text-sm text-gray-400">Пока ничего не добавлено</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {adjustments.map((a) => (
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

        {/* Форма добавления */}
        <div className="mb-5 border border-gray-200 rounded-lg p-3 bg-gray-50">
          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Добавить</p>
          <div className="flex flex-col gap-2">
            <div className="flex gap-2">
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as any)}
                className="border border-gray-300 rounded px-2 py-1.5 text-sm"
              >
                <option value="premium">Премия</option>
                <option value="kpi">KPI</option>
                <option value="advance">Аванс (удержание)</option>
              </select>
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="Сумма ₽"
                min={0}
                className="w-32 border border-gray-300 rounded px-2 py-1.5 text-sm"
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
