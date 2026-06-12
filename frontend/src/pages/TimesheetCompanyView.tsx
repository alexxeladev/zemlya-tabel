// frontend/src/pages/TimesheetCompanyView.tsx
// Альтернативный вид табеля: каждый сотрудник = N строк по компаниям.
// Делается РЯДОМ с классическим видом (TimesheetPage), не вместо. Переключается
// тумблером в шапке (store/timesheetView). Данные — те же, что у классического
// вида (передаются пропсами из TimesheetPage), отличается только рендеринг.
//
// Структура строки:
//   ФИО | Отдел | График  (merge через rowspan на все строки сотрудника)
//   Компания | дни 1..N | Итого Ч компании | [Оклад | Сверхур.Ч | Праздн.Ч | Сверхур.₽ | Праздн.₽]
//   Итого Ч | [Итого ₽ | Δ] | Норма  (merge через rowspan)
//
// Каждая строка компании редактируется как одна ячейка в день (часы по этой
// компании). Кнопка «+ комп.» добавляет строку компании (draft), «×» убирает
// дополнительную строку с 0 часов. Родительская (default_company) — всегда, без «×».

import { Fragment, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { companyColorByIndex } from '../utils/colors';
import {
  PeriodBadge,
  DeltaCell,
  type Employee,
  type Company,
  type DayType,
  type EmployeePayroll,
  type CompanyBreakdown,
  type MonthResponse,
  type Period,
} from './TimesheetPage';

type Group = {
  deptId: number | null;
  name: string;
  employees: Employee[];
  period: Period | null;
};

type Props = {
  data: MonthResponse;
  year: number;
  month: number;
  numDays: number;
  dayTypes: Record<number, DayType>;
  visibleEmployees: Employee[];
  grouped: boolean;
  groups: Group[];
  payrollByEmp: Map<number, EmployeePayroll>;
  canSeeMoney: boolean;
  saveSlot: (employeeId: number, day: number, companyId: number, hours: number) => void;
  periodForDept: (deptId: number | null) => Period | undefined;
  dayTotals: number[];
  onSubmit: (periodId: number) => void;
  onClose: (periodId: number) => void;
  onReturn: (periodId: number, reason: string) => void;
  onReopen: (periodId: number, reason: string) => void;
};

const WEEKDAY_RU = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

// Закреплённые слева колонки: фиксированные ширины + накопленные смещения.
// Фикс. ширина обязательна, иначе sticky-смещения «разъезжаются».
const COL_W = { name: 190, dept: 110, sched: 64, company: 150 };
const COL_LEFT = {
  name: 0,
  dept: COL_W.name,
  sched: COL_W.name + COL_W.dept,
  company: COL_W.name + COL_W.dept + COL_W.sched,
};
function stickyLeft(left: number, width: number, z = 10): CSSProperties {
  return { position: 'sticky', left, width, minWidth: width, maxWidth: width, zIndex: z };
}

function jsWeekdayMonFirst(year: number, month: number, day: number): number {
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

function fmtMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const n = num(value);
  if (n === 0) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(n) + ' ₽';
}

function getCompanyColor(companyId: number, companies: Company[]) {
  return companyColorByIndex(companies.findIndex((c) => c.id === companyId));
}

function dayTypeLabel(t: DayType): string {
  return {
    work: 'Рабочий день',
    short: 'Сокращённый день (−1 ч)',
    weekend: 'Выходной',
    holiday: 'Праздник',
  }[t];
}

// Описание одной строки-компании сотрудника
type CompanyRow = {
  companyId: number | null;
  isParent: boolean;
  removable: boolean; // дополнительная строка с 0 часов в draft
};

export function TimesheetCompanyView(props: Props) {
  const {
    data, year, month, numDays, dayTypes, visibleEmployees, grouped, groups,
    payrollByEmp, canSeeMoney, saveSlot, periodForDept, dayTotals,
    onSubmit, onClose, onReturn, onReopen,
  } = props;

  // Локальные «добавленные вручную» компании (без zustand — как expanded в классике)
  const [addedByEmp, setAddedByEmp] = useState<Map<number, number[]>>(new Map());
  // Какому сотруднику открыт выпадающий список «+ комп.»
  const [adderOpenFor, setAdderOpenFor] = useState<number | null>(null);

  // ── Индексы по entries ──
  const { cellHours, compHours, hoursCompaniesByEmp } = useMemo(() => {
    const cellHours = new Map<string, number>(); // emp:comp:day -> hours
    const compHours = new Map<string, number>(); // emp:comp -> total
    const hoursCompaniesByEmp = new Map<number, Set<number>>();
    for (const e of data.entries) {
      const day = parseInt(e.work_date.slice(-2), 10);
      const h = num(e.hours);
      cellHours.set(`${e.employee_id}:${e.company_id}:${day}`, h);
      const ck = `${e.employee_id}:${e.company_id}`;
      compHours.set(ck, (compHours.get(ck) ?? 0) + h);
      if (!hoursCompaniesByEmp.has(e.employee_id)) hoursCompaniesByEmp.set(e.employee_id, new Set());
      if (h > 0) hoursCompaniesByEmp.get(e.employee_id)!.add(e.company_id);
    }
    return { cellHours, compHours, hoursCompaniesByEmp };
  }, [data.entries]);

  // Порядок компаний (для стабильной сортировки строк) — по data.companies
  const companyOrder = useMemo(() => {
    const m = new Map<number, number>();
    data.companies.forEach((c, i) => m.set(c.id, i));
    return m;
  }, [data.companies]);

  const companyById = useMemo(() => {
    const m = new Map<number, Company>();
    for (const c of data.companies) m.set(c.id, c);
    return m;
  }, [data.companies]);

  // Строки-компании сотрудника (родительская + где есть часы + добавленные вручную)
  const rowsForEmp = (emp: Employee): CompanyRow[] => {
    const parentId = emp.default_company_id;
    const withHours = hoursCompaniesByEmp.get(emp.id) ?? new Set<number>();
    const added = addedByEmp.get(emp.id) ?? [];
    const seen = new Set<number>();
    const rows: CompanyRow[] = [];

    if (parentId != null) {
      rows.push({ companyId: parentId, isParent: true, removable: false });
      seen.add(parentId);
    }
    const others = Array.from(new Set<number>([...withHours, ...added]))
      .filter((cid) => !seen.has(cid))
      .sort((a, b) => (companyOrder.get(a) ?? 0) - (companyOrder.get(b) ?? 0));
    for (const cid of others) {
      const total = compHours.get(`${emp.id}:${cid}`) ?? 0;
      rows.push({ companyId: cid, isParent: false, removable: total === 0 });
      seen.add(cid);
    }
    if (rows.length === 0) {
      // нет ни родительской, ни часов — плейсхолдер-строка
      rows.push({ companyId: null, isParent: true, removable: false });
    }
    return rows;
  };

  const breakdownFor = (emp: Employee, companyId: number | null): CompanyBreakdown | undefined => {
    if (companyId == null) return undefined;
    const pay = payrollByEmp.get(emp.id);
    return pay?.breakdown_by_company?.find((b) => b.company_id === companyId);
  };

  const availableCompanies = (rows: CompanyRow[]): Company[] => {
    const shown = new Set(rows.map((r) => r.companyId).filter((x): x is number => x != null));
    return data.companies.filter((c) => !shown.has(c.id));
  };

  const addCompany = (empId: number, companyId: number) => {
    setAddedByEmp((prev) => {
      const next = new Map(prev);
      const arr = next.get(empId) ?? [];
      if (!arr.includes(companyId)) next.set(empId, [...arr, companyId]);
      return next;
    });
    setAdderOpenFor(null);
  };

  const removeCompany = (empId: number, companyId: number) => {
    setAddedByEmp((prev) => {
      const next = new Map(prev);
      const arr = (next.get(empId) ?? []).filter((c) => c !== companyId);
      next.set(empId, arr);
      return next;
    });
  };

  // Кол-во денежных колонок по компании и emp-level (для colSpan строки ИТОГО)
  const companyMoneyCols = canSeeMoney ? 5 : 0; // Оклад, Сверхур.Ч, Праздн.Ч, Сверхур.₽, Праздн.₽
  const empMoneyCols = canSeeMoney ? 2 : 0; // Итого ₽, Δ
  const normCols = canSeeMoney ? 1 : 0; // Норма
  // ФИО,Отдел,График(3) + Компания(1) + дни + ИтогоЧ компании(1) + companyMoney + ИтогоЧ emp(1) + empMoney + Норма
  const totalCols = 3 + 1 + numDays + 1 + companyMoneyCols + 1 + empMoneyCols + normCols;

  // ── Рендер строк одного сотрудника ──
  const renderEmployee = (emp: Employee) => {
    const rows = rowsForEmp(emp);
    const n = rows.length;
    const pay = payrollByEmp.get(emp.id);
    const periodEditable = periodForDept(emp.department_id)?.can_edit ?? false;
    const noSchedule = !emp.schedule;
    const empTotal = num(pay?.total_hours, 0)
      || rows.reduce((s, r) => s + (r.companyId != null ? (compHours.get(`${emp.id}:${r.companyId}`) ?? 0) : 0), 0);

    const avail = availableCompanies(rows);

    return (
      <Fragment key={emp.id}>
        {rows.map((row, ri) => {
          const first = ri === 0;
          const last = ri === n - 1;
          const cid = row.companyId;
          const col = cid != null ? getCompanyColor(cid, data.companies) : null;
          const company = cid != null ? companyById.get(cid) : undefined;
          const bd = breakdownFor(emp, cid);
          const compTotalHours = cid != null ? (compHours.get(`${emp.id}:${cid}`) ?? 0) : 0;

          return (
            <tr key={`${emp.id}-${cid ?? 'none'}-${ri}`} className="hover:bg-blue-50/20">
              {/* ── Sticky emp-level (merge через rowspan) ── */}
              {first && (
                <>
                  <td
                    rowSpan={n}
                    className="bg-white border border-gray-200 px-3 py-2 font-medium text-gray-900 align-top"
                    style={stickyLeft(COL_LEFT.name, COL_W.name)}
                    title={noSchedule ? 'График не задан' : emp.full_name}
                  >
                    <div className="truncate" style={{ maxWidth: COL_W.name - 24 }}>{emp.full_name}</div>
                  </td>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-2 py-2 text-xs text-gray-600 align-top bg-white"
                    style={stickyLeft(COL_LEFT.dept, COL_W.dept)}
                  >
                    {emp.department?.name ?? '—'}
                  </td>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-2 py-2 text-xs text-center font-mono text-gray-600 align-top bg-white"
                    style={stickyLeft(COL_LEFT.sched, COL_W.sched)}
                  >
                    {noSchedule ? (
                      <span className="italic text-gray-400 font-sans">не задан</span>
                    ) : (
                      emp.schedule?.name
                    )}
                  </td>
                </>
              )}

              {/* ── Компания (sticky) ── */}
              <td
                className="border border-gray-200 px-2 py-1.5 align-top bg-white"
                style={stickyLeft(COL_LEFT.company, COL_W.company)}
              >
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-1.5 h-5">
                    {cid != null && col ? (
                      <span
                        className="inline-flex items-center justify-center rounded text-[11px] font-mono font-semibold px-1 h-5 min-w-[40px]"
                        style={{ background: col.bg, color: col.color, border: `1px solid ${col.color}40` }}
                      >
                        {company?.code ?? cid}
                      </span>
                    ) : (
                      <span className="text-[11px] text-gray-400 italic">нет компании</span>
                    )}
                    {row.isParent && cid != null && (
                      <span
                        className="text-[9px] px-1 rounded bg-gray-100 text-gray-500 leading-4"
                        title="Основная (родительская) компания"
                      >
                        осн.
                      </span>
                    )}
                    <span className="flex-1" />
                    {row.removable && periodEditable && cid != null && (
                      <button
                        type="button"
                        onClick={() => removeCompany(emp.id, cid)}
                        className="text-gray-400 hover:text-red-600 leading-none text-base px-0.5"
                        title="Убрать строку компании (0 часов)"
                      >
                        ×
                      </button>
                    )}
                    {/* «+» — справа на последней строке компаний, только draft */}
                    {last && periodEditable && avail.length > 0 && adderOpenFor !== emp.id && (
                      <button
                        type="button"
                        onClick={() => setAdderOpenFor(emp.id)}
                        className="inline-flex items-center justify-center h-5 px-1.5 rounded border border-dashed border-blue-300 text-blue-500 text-sm font-bold leading-none hover:bg-blue-50 hover:border-blue-400"
                        title="Добавить компанию"
                      >
                        +
                      </button>
                    )}
                  </div>
                  {company && (
                    <span className="text-[10px] text-gray-400 truncate" title={company.name}>
                      {company.name}
                    </span>
                  )}
                  {/* Выпадающий список выбора компании — снизу при клике на «+» */}
                  {last && periodEditable && avail.length > 0 && adderOpenFor === emp.id && (
                    <select
                      autoFocus
                      className="text-[11px] border border-blue-300 rounded px-1 py-1 w-full mt-0.5"
                      defaultValue=""
                      onChange={(e) => {
                        const v = parseInt(e.target.value, 10);
                        if (Number.isFinite(v)) addCompany(emp.id, v);
                      }}
                      onBlur={() => setAdderOpenFor(null)}
                    >
                      <option value="" disabled>
                        Выберите…
                      </option>
                      {avail.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.code} — {c.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              </td>

              {/* ── Дни ── */}
              {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => {
                const t = dayTypes[d];
                const isOff = t === 'weekend' || t === 'holiday';
                const bgClass =
                  t === 'holiday' ? 'bg-red-50/40'
                  : t === 'short' ? 'bg-yellow-50/40'
                  : t === 'weekend' ? 'bg-gray-50/60'
                  : '';
                const h = cid != null ? (cellHours.get(`${emp.id}:${cid}:${d}`) ?? 0) : 0;
                return (
                  <td key={d} className={`border border-gray-200 p-0.5 text-center ${bgClass}`} style={{ minWidth: 44 }}>
                    {cid != null ? (
                      <CompanyDayCell
                        value={h}
                        disabled={!periodEditable}
                        dim={isOff}
                        color={col?.color}
                        onChange={(nh) => saveSlot(emp.id, d, cid, nh)}
                      />
                    ) : null}
                  </td>
                );
              })}

              {/* ── Итого Ч по компании ── */}
              <td className="border border-gray-200 px-2 py-1 text-center font-mono font-semibold bg-gray-50">
                {fmtHours(compTotalHours)}
              </td>

              {/* ── Финансы по компании ── */}
              {canSeeMoney && (
                <>
                  <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
                    {fmtMoney(bd?.base_amount)}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-center font-mono text-xs text-gray-600">
                    {bd ? fmtHours(num(bd.overtime_hours)) || '—' : '—'}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-center font-mono text-xs text-gray-600">
                    {bd ? fmtHours(num(bd.holiday_hours)) || '—' : '—'}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
                    {fmtMoney(bd?.overtime_amount)}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
                    {fmtMoney(bd?.holiday_amount)}
                  </td>
                </>
              )}

              {/* ── Emp-level (merge через rowspan) ── */}
              {first && (
                <>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-3 py-2 text-center font-mono font-bold bg-gray-100 align-top"
                  >
                    {fmtHours(empTotal)}
                  </td>
                  {canSeeMoney && (
                    <>
                      <td
                        rowSpan={n}
                        className="border border-gray-200 px-2 py-2 text-right font-mono font-semibold text-blue-700 bg-blue-50/40 align-top"
                      >
                        {fmtMoney(pay?.total_amount)}
                      </td>
                      <td
                        rowSpan={n}
                        className="border border-gray-200 px-2 py-2 text-center font-mono text-xs align-top"
                      >
                        {pay?.delta_hours ? <DeltaCell delta={num(pay.delta_hours)} /> : '—'}
                      </td>
                      <td
                        rowSpan={n}
                        className="border border-gray-200 px-2 py-2 text-center font-mono text-xs text-gray-600 align-top"
                      >
                        {pay?.norm_hours ? fmtHours(num(pay.norm_hours)) : '—'}
                      </td>
                    </>
                  )}
                </>
              )}
            </tr>
          );
        })}
      </Fragment>
    );
  };

  const renderGroupDivider = (g: Group) => (
    <tr key={`group-${g.deptId ?? 'null'}`}>
      <td colSpan={totalCols} className="bg-slate-100 border border-gray-300 p-0">
        <div className="sticky left-0 flex items-center gap-3 px-3 py-2 w-fit">
          <span className="text-sm font-bold uppercase tracking-wide text-gray-700">{g.name}</span>
          {g.period && (
            <PeriodBadge
              period={g.period}
              onSubmit={() => onSubmit(g.period!.id)}
              onClose={() => onClose(g.period!.id)}
              onReturn={(reason) => onReturn(g.period!.id, reason)}
              onReopen={(reason) => onReopen(g.period!.id, reason)}
            />
          )}
        </div>
      </td>
    </tr>
  );

  return (
    <table className="border-collapse text-xs" style={{ minWidth: 'max-content' }}>
      {/* ===== ШАПКА ===== */}
      <thead>
        <tr>
          <th className="sticky top-0 bg-gray-50 border border-gray-200 px-3 py-2 text-left font-medium text-gray-600" style={{ ...stickyLeft(COL_LEFT.name, COL_W.name, 30), top: 0 }}>
            Сотрудник
          </th>
          <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-left font-medium text-gray-600" style={{ ...stickyLeft(COL_LEFT.dept, COL_W.dept, 30), top: 0 }}>
            Отдел
          </th>
          <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ ...stickyLeft(COL_LEFT.sched, COL_W.sched, 30), top: 0 }}>
            График
          </th>
          <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-left font-medium text-gray-600" style={{ ...stickyLeft(COL_LEFT.company, COL_W.company, 30), top: 0 }}>
            Компания
          </th>
          {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => {
            const t = dayTypes[d];
            const wd = jsWeekdayMonFirst(year, month, d);
            const cls =
              t === 'holiday' ? 'bg-red-50 text-red-600'
              : t === 'short' ? 'bg-yellow-50 text-yellow-700'
              : t === 'weekend' ? 'bg-gray-100 text-gray-500'
              : 'bg-gray-50 text-gray-600';
            return (
              <th key={d} className={`sticky top-0 ${cls} border border-gray-200 px-1 py-1 text-center font-medium`} style={{ minWidth: 44, zIndex: 20 }} title={dayTypeLabel(t)}>
                <div className="text-sm font-semibold">{d}</div>
                <div className="text-[10px] font-normal opacity-75">{WEEKDAY_RU[wd]}</div>
              </th>
            );
          })}
          <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 60, zIndex: 20 }}>
            Ч комп.
          </th>
          {canSeeMoney && (
            <>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 80, zIndex: 20 }}>Оклад</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Свер.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Празд.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 70, zIndex: 20 }}>Свер.₽</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 70, zIndex: 20 }}>Празд.₽</th>
            </>
          )}
          <th className="sticky top-0 bg-gray-100 border border-gray-200 px-2 py-2 text-center font-semibold text-gray-700" style={{ minWidth: 70, zIndex: 20 }}>
            Итого Ч
          </th>
          {canSeeMoney && (
            <>
              <th className="sticky top-0 bg-blue-50 border border-gray-200 px-2 py-2 text-right font-semibold text-blue-700" style={{ minWidth: 100, zIndex: 20 }}>Итого ₽</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Δ</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 60, zIndex: 20 }}>Норма</th>
            </>
          )}
        </tr>
      </thead>

      {/* ===== ТЕЛО ===== */}
      <tbody>
        {visibleEmployees.length === 0 && (
          <tr>
            <td colSpan={totalCols} className="text-center text-gray-500 py-10">
              Нет сотрудников
            </td>
          </tr>
        )}

        {grouped
          ? groups.map((g) => (
              <Fragment key={`grp-${g.deptId ?? 'null'}`}>
                {renderGroupDivider(g)}
                {g.employees.map((emp) => renderEmployee(emp))}
              </Fragment>
            ))
          : visibleEmployees.map((emp) => renderEmployee(emp))}

        {/* ===== ИТОГО строка ===== */}
        {visibleEmployees.length > 0 && (
          <tr className="bg-gray-100 font-semibold">
            <td className="bg-gray-200 border border-gray-300 px-3 py-2" style={stickyLeft(COL_LEFT.name, COL_W.name)}>
              ИТОГО
            </td>
            <td
              className="bg-gray-200 border border-gray-300 px-2 py-2"
              colSpan={3}
              style={stickyLeft(COL_LEFT.dept, COL_W.dept + COL_W.sched + COL_W.company)}
            ></td>
            {Array.from({ length: numDays }, (_, i) => i + 1).map((d) => (
              <td key={d} className="border border-gray-300 px-1 py-2 text-center font-mono text-xs text-gray-700">
                {dayTotals[d] > 0 ? fmtHours(dayTotals[d]) : ''}
              </td>
            ))}
            <td className="border border-gray-300 px-2 py-2 text-center font-mono font-bold">
              {fmtHours(dayTotals.reduce((a, b) => a + b, 0))}
            </td>
            {canSeeMoney && data.payroll ? (
              <>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono">{fmtMoney(data.payroll.total_base_amount)}</td>
                <td className="border border-gray-300 px-2 py-2"></td>
                <td className="border border-gray-300 px-2 py-2"></td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono">{fmtMoney(data.payroll.total_overtime_amount)}</td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono">{fmtMoney(data.payroll.total_holiday_amount)}</td>
                <td className="border border-gray-300 px-2 py-2"></td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-blue-700 bg-blue-100">{fmtMoney(data.payroll.grand_total)}</td>
                <td className="border border-gray-300 px-2 py-2" colSpan={2}></td>
              </>
            ) : canSeeMoney ? (
              [0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => <td key={i} className="border border-gray-300 px-2 py-2" />)
            ) : null}
          </tr>
        )}
      </tbody>
    </table>
  );
}

// ── Ячейка дня для одной компании ──────────────────────────────
function CompanyDayCell({
  value,
  disabled,
  dim,
  color,
  onChange,
}: {
  value: number;
  disabled: boolean;
  dim: boolean;
  color?: string;
  onChange: (hours: number) => void;
}) {
  const [text, setText] = useState<string>(value ? String(value) : '');

  useEffect(() => {
    setText(value ? String(value) : '');
  }, [value]);

  const commit = () => {
    if (text.trim() === '') {
      if (value !== 0) onChange(0);
      return;
    }
    const parsed = parseFloat(text);
    if (Number.isNaN(parsed) || parsed < 0) {
      setText(value ? String(value) : '');
      return;
    }
    const n = Math.min(24, Math.round(parsed));
    if (String(n) !== text) setText(n ? String(n) : '');
    if (n === value) return;
    onChange(n);
  };

  if (disabled) {
    return <span className="text-[11px] font-mono" style={{ color }}>{value ? fmtHours(value) : ''}</span>;
  }

  return (
    <input
      type="number"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
      }}
      min={0}
      max={24}
      step={1}
      className={`w-9 text-center text-[11px] font-mono border-0 outline-none bg-transparent ${dim ? 'text-gray-400' : ''}`}
      style={{ color: value ? color : undefined }}
    />
  );
}

export default TimesheetCompanyView;
