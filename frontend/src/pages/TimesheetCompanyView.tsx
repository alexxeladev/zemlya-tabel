// frontend/src/pages/TimesheetCompanyView.tsx
// Альтернативный вид табеля: сотрудник = N строк по РАБОЧИМ МЕСТАМ × компаниям.
// Делается РЯДОМ с классическим видом (TimesheetPage), не вместо. Переключается
// тумблером в шапке (store/timesheetView). Данные — те же, что у классического
// вида (передаются пропсами из TimesheetPage), отличается только рендеринг.
//
// Структура строки (task_positions ч.B — уровней merge стало два):
//   ФИО                                     (rowspan на ВСЕ строки сотрудника)
//   Должность | Отдел | График              (rowspan на строки этой позиции)
//   Компания | дни 1..N | Итого Ч компании |
//   [Оклад | Сверхур.Ч | Вне граф.Ч | Празд.Ч | Сверхур.₽ | Вне граф.₽ | Празд.₽]
//   Итого Ч | [Итого ₽ | Δ] | Норма         (rowspan на строки этой позиции)
//
// Итоги и расчёт — у ПОЗИЦИИ: у совместителя каждое рабочее место со своим
// окладом, графиком и нормой, «к выплате» между ними не суммируется.
//
// Каждая строка компании редактируется как одна ячейка в день (часы по этой
// компании этого рабочего места). Кнопка «+ комп.» добавляет строку компании
// (draft), «×» убирает дополнительную строку с 0 часов. Родительская
// (основная компания позиции) — всегда, без «×».

import { Fragment, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { companyColorByIndex } from '../utils/colors';
import { absenceMeta } from '../utils/absences';
import type { AbsenceKind } from '../types/api';
import {
  PeriodBadge,
  DeltaCell,
  NormCell,
  posKey,
  positionIdParam,
  type Absence,
  type Employee,
  type Company,
  type DayType,
  type EmployeePayroll,
  type CompanyBreakdown,
  type MonthResponse,
  type Period,
  type Position,
  type PositionRow,
  type TimesheetEntry,
} from './TimesheetPage';

type Group = {
  deptId: number | null;
  name: string;
  rows: PositionRow[];
  period: Period | null;
};

type Props = {
  data: MonthResponse;
  year: number;
  month: number;
  numDays: number;
  dayTypes: Record<number, DayType>;
  /** строки «сотрудник × рабочее место», уже отфильтрованные и со span-ами */
  rows: PositionRow[];
  grouped: boolean;
  groups: Group[];
  payrollFor: (emp: Employee, position: Position) => EmployeePayroll | undefined;
  /** позиция часов: строки без position_id принадлежат основной */
  entryPositionId: (entry: TimesheetEntry) => number | undefined;
  // Коды отсутствий: `empId:day` → отметка. День с кодом часов не имеет.
  absenceByEmpDay: Map<string, Absence>;
  canSeeMoney: boolean;
  /** Табельщик: часы по категориям видит, рубли — нет (task_timekeeper_role) */
  canSeeHourStats: boolean;
  saveSlot: (
    employeeId: number, day: number, companyId: number, hours: number, positionId?: number,
  ) => void;
  setAbsence: (employeeId: number, day: number, kind: AbsenceKind | null) => void;
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
const COL_W = { name: 170, position: 120, dept: 100, sched: 60, company: 140 };
const COL_LEFT = {
  name: 0,
  position: COL_W.name,
  dept: COL_W.name + COL_W.position,
  sched: COL_W.name + COL_W.position + COL_W.dept,
  company: COL_W.name + COL_W.position + COL_W.dept + COL_W.sched,
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

// Описание одной строки-компании внутри рабочего места
type CompanyRow = {
  companyId: number | null;
  isParent: boolean;
  removable: boolean; // дополнительная строка с 0 часов в draft
};

export function TimesheetCompanyView(props: Props) {
  const {
    data, year, month, numDays, dayTypes, rows, grouped, groups,
    payrollFor, entryPositionId, absenceByEmpDay, canSeeMoney, canSeeHourStats, saveSlot, setAbsence,
    periodForDept, dayTotals,
    onSubmit, onClose, onReturn, onReopen,
  } = props;

  // Локальные «добавленные вручную» компании (без zustand — как expanded в классике).
  // Ключ — рабочее место: у совместителя компании подработки свои.
  const [addedByPos, setAddedByPos] = useState<Map<string, number[]>>(new Map());
  // Какому рабочему месту открыт выпадающий список «+ комп.»
  const [adderOpenFor, setAdderOpenFor] = useState<string | null>(null);

  // ── Индексы по entries: всё с разрезом по позиции ──
  const { cellHours, compHours, hoursCompaniesByPos } = useMemo(() => {
    const cellHours = new Map<string, number>(); // emp:pos:comp:day -> hours
    const compHours = new Map<string, number>(); // emp:pos:comp -> total
    const hoursCompaniesByPos = new Map<string, Set<number>>();
    for (const e of data.entries) {
      const day = parseInt(e.work_date.slice(-2), 10);
      const h = num(e.hours);
      const pk = posKey(e.employee_id, entryPositionId(e));
      cellHours.set(`${pk}:${e.company_id}:${day}`, h);
      const ck = `${pk}:${e.company_id}`;
      compHours.set(ck, (compHours.get(ck) ?? 0) + h);
      if (!hoursCompaniesByPos.has(pk)) hoursCompaniesByPos.set(pk, new Set());
      if (h > 0) hoursCompaniesByPos.get(pk)!.add(e.company_id);
    }
    return { cellHours, compHours, hoursCompaniesByPos };
  }, [data.entries, entryPositionId]);

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

  // Строки-компании рабочего места: основная компания позиции + где есть часы
  // + добавленные вручную.
  const rowsForPosition = (emp: Employee, position: Position): CompanyRow[] => {
    const pk = posKey(emp.id, position.id);
    const parentId = position.company_id ?? emp.default_company_id;
    const withHours = hoursCompaniesByPos.get(pk) ?? new Set<number>();
    const added = addedByPos.get(pk) ?? [];
    const seen = new Set<number>();
    const result: CompanyRow[] = [];

    if (parentId != null) {
      result.push({ companyId: parentId, isParent: true, removable: false });
      seen.add(parentId);
    }
    const others = Array.from(new Set<number>([...withHours, ...added]))
      .filter((cid) => !seen.has(cid))
      .sort((a, b) => (companyOrder.get(a) ?? 0) - (companyOrder.get(b) ?? 0));
    for (const cid of others) {
      const total = compHours.get(`${pk}:${cid}`) ?? 0;
      result.push({ companyId: cid, isParent: false, removable: total === 0 });
      seen.add(cid);
    }
    if (result.length === 0) {
      // нет ни основной компании, ни часов — плейсхолдер-строка
      result.push({ companyId: null, isParent: true, removable: false });
    }
    return result;
  };

  const availableCompanies = (companyRows: CompanyRow[]): Company[] => {
    const shown = new Set(
      companyRows.map((r) => r.companyId).filter((x): x is number => x != null),
    );
    return data.companies.filter((c) => !shown.has(c.id));
  };

  const addCompany = (pk: string, companyId: number) => {
    setAddedByPos((prev) => {
      const next = new Map(prev);
      const arr = next.get(pk) ?? [];
      if (!arr.includes(companyId)) next.set(pk, [...arr, companyId]);
      return next;
    });
    setAdderOpenFor(null);
  };

  const removeCompany = (pk: string, companyId: number) => {
    setAddedByPos((prev) => {
      const next = new Map(prev);
      next.set(pk, (next.get(pk) ?? []).filter((c) => c !== companyId));
      return next;
    });
  };

  // Кол-во денежных колонок по компании и position-level (для colSpan строки ИТОГО)
  const companyMoneyCols = canSeeMoney ? 7 : 0; // Оклад, Сверхур.Ч, Вне граф.Ч, Празд.Ч, Сверхур.₽, Вне граф.₽, Празд.₽
  const posMoneyCols = canSeeMoney ? 2 : 0; // Итого ₽, Δ
  const normCols = canSeeMoney ? 1 : 0; // Норма
  // Табельщику вместо денежного блока — только часы: по компании Сверхур.Ч /
  // Вне граф.Ч / Празд.Ч, по позиции Δ и Норма.
  const hoursOnly = canSeeHourStats && !canSeeMoney;
  const companyHourCols = hoursOnly ? 3 : 0;
  const posHourCols = hoursOnly ? 2 : 0;
  // ФИО,Должность,Отдел,График(4) + Компания(1) + дни + ИтогоЧ компании(1)
  // + companyMoney + ИтогоЧ позиции(1) + posMoney + Норма
  const totalCols =
    4 + 1 + numDays + 1 + companyMoneyCols + companyHourCols + 1 + posMoneyCols + normCols
    + posHourCols;

  // ── Рендер строк одного рабочего места ──
  // `index`/`count` — место позиции среди позиций сотрудника: ФИО объединяется
  // по ВСЕМ его строкам, поэтому его высоту надо знать заранее.
  const renderPosition = (
    { emp, position, index, count }: PositionRow,
    nameSpan: number,
  ) => {
    const pk = posKey(emp.id, position.id);
    const positionId = positionIdParam(position);
    const companyRows = rowsForPosition(emp, position);
    const n = companyRows.length;
    const pay = payrollFor(emp, position);
    const periodEditable = periodForDept(position.department_id)?.can_edit ?? false;
    const schedule = position.schedule ?? null;
    const noSchedule = !schedule;
    const posTotal = num(pay?.total_hours, 0)
      || companyRows.reduce(
        (s, r) => s + (r.companyId != null ? (compHours.get(`${pk}:${r.companyId}`) ?? 0) : 0),
        0,
      );

    const avail = availableCompanies(companyRows);

    const breakdownFor = (companyId: number | null): CompanyBreakdown | undefined =>
      companyId == null
        ? undefined
        : pay?.breakdown_by_company?.find((b) => b.company_id === companyId);

    return (
      <Fragment key={pk}>
        {companyRows.map((row, ri) => {
          const first = ri === 0;
          const last = ri === n - 1;
          const cid = row.companyId;
          const col = cid != null ? getCompanyColor(cid, data.companies) : null;
          const company = cid != null ? companyById.get(cid) : undefined;
          const bd = breakdownFor(cid);
          const compTotalHours = cid != null ? (compHours.get(`${pk}:${cid}`) ?? 0) : 0;
          // Код отсутствия — на человека целиком (он отсутствует на всех
          // работах), поэтому рисуем его на самой первой строке сотрудника.
          const isEmployeeFirstRow = index === 0 && first;

          return (
            <tr key={`${pk}-${cid ?? 'none'}-${ri}`} className="hover:bg-blue-50/20">
              {/* ── ФИО: merge на ВСЕ строки сотрудника (все его позиции) ── */}
              {index === 0 && first && (
                <td
                  rowSpan={nameSpan}
                  className="bg-white border border-gray-200 px-3 py-2 font-medium text-gray-900 align-top"
                  style={stickyLeft(COL_LEFT.name, COL_W.name)}
                  title={emp.full_name}
                >
                  <div className="truncate" style={{ maxWidth: COL_W.name - 24 }}>
                    {emp.full_name}
                  </div>
                  {count > 1 && (
                    <div className="text-[10px] font-normal text-gray-400">
                      совместительство: {count}
                    </div>
                  )}
                </td>
              )}

              {/* ── Должность / Отдел / График: merge на строки ЭТОЙ позиции ── */}
              {first && (
                <>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-2 py-2 text-xs text-gray-700 align-top bg-white"
                    style={stickyLeft(COL_LEFT.position, COL_W.position)}
                    title={position.display_title}
                  >
                    <div className="truncate" style={{ maxWidth: COL_W.position - 16 }}>
                      {position.display_title}
                    </div>
                    {position.is_primary && count > 1 && (
                      <span className="text-[9px] text-gray-400">осн.</span>
                    )}
                  </td>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-2 py-2 text-xs text-gray-600 align-top bg-white"
                    style={stickyLeft(COL_LEFT.dept, COL_W.dept)}
                  >
                    {position.department?.name ?? '—'}
                  </td>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-2 py-2 text-xs text-center font-mono text-gray-600 align-top bg-white"
                    style={stickyLeft(COL_LEFT.sched, COL_W.sched)}
                  >
                    {noSchedule ? (
                      <span className="italic text-gray-400 font-sans">не задан</span>
                    ) : (
                      schedule?.name
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
                        title="Основная компания этого рабочего места"
                      >
                        осн.
                      </span>
                    )}
                    <span className="flex-1" />
                    {row.removable && periodEditable && cid != null && (
                      <button
                        type="button"
                        onClick={() => removeCompany(pk, cid)}
                        className="text-gray-400 hover:text-red-600 leading-none text-base px-0.5"
                        title="Убрать строку компании (0 часов)"
                      >
                        ×
                      </button>
                    )}
                    {/* «+» — справа на последней строке компаний, только draft */}
                    {last && periodEditable && avail.length > 0 && adderOpenFor !== pk && (
                      <button
                        type="button"
                        onClick={() => setAdderOpenFor(pk)}
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
                  {last && periodEditable && avail.length > 0 && adderOpenFor === pk && (
                    <select
                      autoFocus
                      className="text-[11px] border border-blue-300 rounded px-1 py-1 w-full mt-0.5"
                      defaultValue=""
                      onChange={(e) => {
                        const v = parseInt(e.target.value, 10);
                        if (Number.isFinite(v)) addCompany(pk, v);
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
                const h = cid != null ? (cellHours.get(`${pk}:${cid}:${d}`) ?? 0) : 0;
                const absence = absenceByEmpDay.get(`${emp.id}:${d}`);
                return (
                  <td key={d} className={`border border-gray-200 p-0.5 text-center ${bgClass}`} style={{ minWidth: 44 }}>
                    {absence ? (
                      isEmployeeFirstRow ? (
                        <AbsenceCodeCell
                          absence={absence}
                          disabled={!periodEditable}
                          onClear={() => setAbsence(emp.id, d, null)}
                        />
                      ) : null
                    ) : cid != null ? (
                      <CompanyDayCell
                        value={h}
                        disabled={!periodEditable}
                        dim={isOff}
                        color={col?.color}
                        onChange={(nh) => saveSlot(emp.id, d, cid, nh, positionId)}
                      />
                    ) : null}
                  </td>
                );
              })}

              {/* ── Итого Ч по компании ── */}
              <td className="border border-gray-200 px-2 py-1 text-center font-mono font-semibold bg-gray-50">
                {fmtHours(compTotalHours)}
              </td>

              {/* ── Табельщику: только часы по категориям ── */}
              {hoursOnly && (
                <>
                  <td
                    className="border border-gray-200 px-2 py-1 text-center font-mono text-xs text-gray-600"
                    title="Переработка по этой компании"
                  >
                    {bd ? fmtHours(num(bd.overtime_hours)) || '—' : '—'}
                  </td>
                  <td
                    className="border border-gray-200 px-2 py-1 text-center font-mono text-xs text-gray-600"
                    title="Часы вне графика по этой компании"
                  >
                    {bd ? fmtHours(num(bd.off_schedule_hours)) || '—' : '—'}
                  </td>
                  <td
                    className="border border-gray-200 px-2 py-1 text-center font-mono text-xs text-gray-600"
                    title="Праздничные часы по этой компании"
                  >
                    {bd ? fmtHours(num(bd.holiday_hours)) || '—' : '—'}
                  </td>
                </>
              )}

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
                    {bd ? fmtHours(num(bd.off_schedule_hours)) || '—' : '—'}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-center font-mono text-xs text-gray-600">
                    {bd ? fmtHours(num(bd.holiday_hours)) || '—' : '—'}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
                    {fmtMoney(bd?.overtime_amount)}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
                    {fmtMoney(bd?.off_schedule_amount)}
                  </td>
                  <td className="border border-gray-200 px-2 py-1 text-right font-mono text-xs">
                    {fmtMoney(bd?.holiday_amount)}
                  </td>
                </>
              )}

              {/* ── Итоги ПОЗИЦИИ (merge на её строки компаний) ──
                  «К выплате» между позициями не суммируется — платят разные
                  компании, поэтому и итоги здесь позиционные, а не по человеку. */}
              {first && (
                <>
                  <td
                    rowSpan={n}
                    className="border border-gray-200 px-3 py-2 text-center font-mono font-bold bg-gray-100 align-top"
                  >
                    {fmtHours(posTotal)}
                  </td>
                  {hoursOnly && (
                    <>
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
                        <NormCell pay={pay} />
                      </td>
                    </>
                  )}
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
                        <NormCell pay={pay} />
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

  // Высота ФИО = сумма строк-компаний по ВСЕМ позициям сотрудника в этом блоке.
  // Считаем заранее: rowspan указывается на первой строке, а сколько их будет,
  // известно только после разбора всех позиций.
  const renderRows = (list: PositionRow[]) => {
    const nameSpans = new Map<number, number>();
    for (const row of list) {
      const n = rowsForPosition(row.emp, row.position).length;
      nameSpans.set(row.emp.id, (nameSpans.get(row.emp.id) ?? 0) + n);
    }
    return list.map((row) => renderPosition(row, nameSpans.get(row.emp.id) ?? 1));
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
          <th
            className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-left font-medium text-gray-600"
            style={{ ...stickyLeft(COL_LEFT.position, COL_W.position, 30), top: 0 }}
            title="Рабочее место: у совместителя строки на каждое, со своим графиком и расчётом"
          >
            Должность
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
          {hoursOnly && (
            <>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }} title="Переработка по компании">Свер.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }} title="Часы вне графика по компании">Вне граф.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }} title="Праздничные часы по компании">Празд.Ч</th>
            </>
          )}
          {canSeeMoney && (
            <>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 80, zIndex: 20 }}>Оклад</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Свер.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Вне граф.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Празд.Ч</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 70, zIndex: 20 }}>Свер.₽</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 70, zIndex: 20 }}>Вне граф.₽</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-right font-medium text-gray-600" style={{ minWidth: 70, zIndex: 20 }}>Празд.₽</th>
            </>
          )}
          <th className="sticky top-0 bg-gray-100 border border-gray-200 px-2 py-2 text-center font-semibold text-gray-700" style={{ minWidth: 70, zIndex: 20 }}>
            Итого Ч
          </th>
          {hoursOnly && (
            <>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }} title="Отклонение факта от нормы">Δ</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 72, zIndex: 20 }} title="Норма по графику за месяц: часов и рабочих дней (смен)">Норма ч / дн</th>
            </>
          )}
          {canSeeMoney && (
            <>
              <th className="sticky top-0 bg-blue-50 border border-gray-200 px-2 py-2 text-right font-semibold text-blue-700" style={{ minWidth: 100, zIndex: 20 }}>Итого ₽</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 50, zIndex: 20 }}>Δ</th>
              <th className="sticky top-0 bg-gray-50 border border-gray-200 px-2 py-2 text-center font-medium text-gray-600" style={{ minWidth: 72, zIndex: 20 }} title="Норма по графику за месяц: часов и рабочих дней (смен)">Норма ч / дн</th>
            </>
          )}
        </tr>
      </thead>

      {/* ===== ТЕЛО ===== */}
      <tbody>
        {rows.length === 0 && (
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
                {renderRows(g.rows)}
              </Fragment>
            ))
          : renderRows(rows)}

        {/* ===== ИТОГО строка ===== */}
        {rows.length > 0 && (
          <tr className="bg-gray-100 font-semibold">
            <td className="bg-gray-200 border border-gray-300 px-3 py-2" style={stickyLeft(COL_LEFT.name, COL_W.name)}>
              ИТОГО
            </td>
            <td
              className="bg-gray-200 border border-gray-300 px-2 py-2"
              colSpan={4}
              style={stickyLeft(
                COL_LEFT.position,
                COL_W.position + COL_W.dept + COL_W.sched + COL_W.company,
              )}
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
                <td className="border border-gray-300 px-2 py-2" colSpan={3}></td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono">{fmtMoney(data.payroll.total_overtime_amount)}</td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono">{fmtMoney(data.payroll.total_off_schedule_amount ?? null)}</td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono">{fmtMoney(data.payroll.total_holiday_amount)}</td>
                <td className="border border-gray-300 px-2 py-2"></td>
                <td className="border border-gray-300 px-2 py-2 text-right font-mono font-bold text-blue-700 bg-blue-100">{fmtMoney(data.payroll.grand_total)}</td>
                <td className="border border-gray-300 px-2 py-2" colSpan={2}></td>
              </>
            ) : canSeeMoney ? (
              Array.from({ length: 11 }, (_, i) => <td key={i} className="border border-gray-300 px-2 py-2" />)
            ) : hoursOnly ? (
              Array.from({ length: companyHourCols + posHourCols }, (_, i) => (
                <td key={i} className="border border-gray-300 px-2 py-2" />
              ))
            ) : null}
          </tr>
        )}
      </tbody>
    </table>
  );
}

// ── День с кодом отсутствия (ОТ / ДО / Б / Н) ───────────────────
// Поставить код можно в классическом виде; здесь код виден и снимается.
function AbsenceCodeCell({
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
  const label = over
    ? 'Больничный сверх годового лимита — за свой счёт'
    : meta?.label ?? absence.code;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClear}
      className="w-full rounded text-[11px] font-mono font-bold leading-5 disabled:cursor-default"
      style={{
        background: bg,
        color,
        border: over ? `1px dashed ${color}80` : `1px solid ${color}40`,
      }}
      title={label + (disabled ? '' : ' — нажмите, чтобы убрать отметку')}
    >
      {absence.code}
      {over && '*'}
    </button>
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
