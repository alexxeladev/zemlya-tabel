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

import { useEffect, useMemo, useState, useCallback } from 'react';
import { useAuthStore } from '../store/auth';
import { toast } from '../store/toasts';
import { timesheetApi } from '../api/timesheet';
import { apiClient } from '../api/client';

// ──────────────────────────────────────────────────────────────
// Типы (минимальные, чтобы не зависеть от уточнений в api.ts)
// ──────────────────────────────────────────────────────────────
type Employee = {
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
};

type Company = { id: number; code: string; name: string };

type TimesheetEntry = {
  employee_id: number;
  work_date: string; // 'YYYY-MM-DD'
  company_id: number;
  hours: number | string; // decimal на бэке -> может прилететь строкой
};

type DayType = 'work' | 'short' | 'holiday' | 'weekend';

type CompanyBreakdown = {
  company_id: number;
  company_code: string;
  hours: string;
  total: string;
};

type EmployeePayroll = {
  employee_id: number;
  total_hours: string;
  norm_hours: string | null;
  delta_hours: string | null;
  base_amount: string;
  overtime_amount: string;
  holiday_amount: string;
  total_amount: string;
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
};

type Period = {
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

type MonthResponse = {
  year: number;
  month: number;
  employees: Employee[];
  companies: Company[];
  entries: TimesheetEntry[];
  payroll: PayrollSummary | null;
  periods: Period[];
};

type CalendarSummary = {
  days: Array<{ day: number; type: DayType; weekday: number }>;
};

// ──────────────────────────────────────────────────────────────
// Палитра цветов компаний
// ──────────────────────────────────────────────────────────────
const COMPANY_PALETTE = [
  { bg: '#dbeafe', color: '#1d4ed8' }, // blue
  { bg: '#dcfce7', color: '#15803d' }, // green
  { bg: '#fef3c7', color: '#a16207' }, // amber
  { bg: '#fce7f3', color: '#be185d' }, // pink
  { bg: '#e9d5ff', color: '#7e22ce' }, // purple
  { bg: '#cffafe', color: '#0e7490' }, // cyan
  { bg: '#fed7aa', color: '#c2410c' }, // orange
  { bg: '#fecaca', color: '#b91c1c' }, // red
];

function getCompanyColor(companyId: number, companies: Company[]) {
  const idx = companies.findIndex((c) => c.id === companyId);
  return COMPANY_PALETTE[Math.max(0, idx) % COMPANY_PALETTE.length];
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

// ──────────────────────────────────────────────────────────────
// Основной компонент
// ──────────────────────────────────────────────────────────────
export function TimesheetPage() {
  const user = useAuthStore((s: any) => s.user);
  const role: string | null = user?.role ?? null;
  const canSeeMoney = role === 'admin' || role === 'accountant';

  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);

  const [data, setData] = useState<MonthResponse | null>(null);
  const [calendar, setCalendar] = useState<CalendarSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [departmentFilter, setDepartmentFilter] = useState<number | null>(null);

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

  // ── Видны ли все периоды в draft? Для autofill / submit ──
  const allEditable = useMemo(() => {
    if (!data?.periods?.length) return true;
    return data.periods.every((p) => p.can_edit);
  }, [data]);

  // ── Список доступных отделов (для admin/accountant) ──
  const departments = useMemo(() => {
    if (!data) return [];
    const seen = new Map<number, string>();
    for (const e of data.employees) {
      if (e.department_id != null && e.department?.name) {
        seen.set(e.department_id, e.department.name);
      }
    }
    return Array.from(seen.entries()).map(([id, name]) => ({ id, name }));
  }, [data]);

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

  // ── Расчёт итогов по дням и компаниям ──
  const dayTotals = useMemo(() => {
    const numDays = daysInMonth(year, month);
    const totals: number[] = new Array(numDays + 1).fill(0);
    if (!data) return totals;
    for (const e of data.entries) {
      const d = parseInt(e.work_date.slice(-2), 10);
      totals[d] += num(e.hours);
    }
    return totals;
  }, [data, year, month]);

  const companyTotals = useMemo(() => {
    const totals = new Map<number, number>();
    if (!data) return totals;
    for (const e of data.entries) {
      totals.set(e.company_id, (totals.get(e.company_id) ?? 0) + num(e.hours));
    }
    return totals;
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

  if (loading && !data) {
    return <div className="p-8 text-gray-500">Загрузка…</div>;
  }
  if (!data) {
    return <div className="p-8 text-gray-500">Нет данных</div>;
  }

  const periodForDept = (deptId: number | null) =>
    data.periods.find((p) => p.department_id === deptId);

  // отфильтрованный список сотрудников
  const visibleEmployees = data.employees.filter((e) => {
    if (e.is_system_admin) return false;
    if (!e.is_active) return false;
    if (departmentFilter !== null && e.department_id !== departmentFilter) return false;
    return true;
  });

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
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {canSeeMoney && departments.length > 1 && (
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

          {/* Статусы периодов */}
          {data.periods.map((p) => {
            const isSelected = departmentFilter === p.department_id;
            const showAlways = data.periods.length === 1 || isSelected;
            if (!showAlways && data.periods.length > 1) return null;
            return (
              <PeriodBadge
                key={p.id}
                period={p}
                onSubmit={() => submitPeriod(p.id)}
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
      <div className="flex-1 overflow-auto bg-white min-h-0 min-w-0">
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

            {visibleEmployees.map((emp) => {
              const pay = payrollByEmp.get(emp.id);
              const empTotal = num(pay?.total_hours, 0) || sumEmployeeHours(emp.id, data.entries);
              const periodEditable = periodForDept(emp.department_id)?.can_edit ?? false;

              return (
                <tr key={emp.id} className="hover:bg-blue-50/30">
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
                    {emp.schedule?.name ?? '—'}
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
                              onHoursChange={(h) =>
                                saveSlot(emp.id, d, slot.company_id, h)
                              }
                              onCompanyChange={(newCompId) =>
                                changeSlotCompany(
                                  emp.id,
                                  d,
                                  slot.company_id,
                                  newCompId,
                                  num(slot.hours)
                                )
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
                  {canSeeMoney && (
                    <>
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
                    </>
                  )}
                </tr>
              );
            })}

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
                  </>
                ) : (
                  [0,1,2,3,4,5].map(i => <td key={i} className="border border-gray-300 px-2 py-2" />)
                ))}
              </tr>
            )}

            {/* ===== ИТОГО по компаниям ===== */}
            {visibleEmployees.length > 0 && data.companies.length > 0 && (
              <>
                <tr>
                  <td
                    className="sticky left-0 bg-gray-50 border border-gray-200 px-3 py-2 text-xs uppercase tracking-wide text-gray-500 font-semibold"
                    colSpan={3 + numDays + 1 + (canSeeMoney ? 6 : 0)}
                    style={{ minWidth: 200, zIndex: 10 }}
                  >
                    По компаниям
                  </td>
                </tr>
                {data.companies.map((c) => {
                  const col = getCompanyColor(c.id, data.companies);
                  const hours = companyTotals.get(c.id) ?? 0;
                  // Money по компании — суммируем из payroll.breakdown_by_company
                  let money = 0;
                  if (canSeeMoney && data.payroll) {
                    for (const pe of data.payroll.employees) {
                      const b = pe.breakdown_by_company?.find((x) => x.company_id === c.id);
                      if (b) money += num(b.total);
                    }
                  }
                  return (
                    <tr key={c.id}>
                      <td
                        className="sticky left-0 bg-white border border-gray-200 px-3 py-2 text-xs"
                        style={{
                          color: col.color,
                          minWidth: 200,
                          zIndex: 10,
                        }}
                      >
                        <span
                          className="inline-block w-2 h-2 rounded-full mr-2"
                          style={{ background: col.color }}
                        />
                        {c.code} — {c.name}
                      </td>
                      <td
                        className="border border-gray-200 px-2 py-2"
                        colSpan={2 + numDays}
                      ></td>
                      <td
                        className="border border-gray-200 px-3 py-2 text-center font-mono font-semibold"
                        style={{ color: col.color }}
                      >
                        {fmtHours(hours)}
                      </td>
                      {canSeeMoney && (
                        <>
                          <td className="border border-gray-200 px-2 py-2" colSpan={5}></td>
                          <td
                            className="border border-gray-200 px-2 py-2 text-right font-mono font-semibold"
                            style={{ color: col.color }}
                          >
                            {fmtMoney(money > 0 ? String(money) : null)}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </>
            )}
          </tbody>
        </table>
      </div>
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

function DeltaCell({ delta }: { delta: number }) {
  if (delta === 0) return <span className="text-gray-400">0</span>;
  const cls = delta > 0 ? 'text-amber-600' : 'text-red-600';
  return (
    <span className={cls + ' font-semibold'}>
      {delta > 0 ? '+' : ''}
      {fmtHours(delta)}
    </span>
  );
}

function PeriodBadge({
  period,
  onSubmit,
}: {
  period: Period;
  onSubmit: () => void;
}) {
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
    <div className="flex items-center gap-2">
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
    const n = parseFloat(hours);
    if (Number.isNaN(n) || n < 0) {
      setHours(String(slot.hours));
      return;
    }
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
        step={0.5}
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


export default TimesheetPage;
