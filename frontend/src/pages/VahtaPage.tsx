import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import {
  copyPreviousVahtaPeriod,
  createVahtaAssignment,
  deleteVahtaAssignment,
  getVahtaMonth,
  listVahtaCandidates,
  listVahtaCrews,
  listVahtaSites,
  setVahtaDays,
  updateVahtaAssignment,
  vahtaExcelUrl,
} from '../api/vahta'
import { listCompanies } from '../api/companies'
import { apiClient } from '../api/client'
import { Button } from '../components/Button'
import { Modal } from '../components/Modal'
import { QuickHireModal } from '../components/vahta/QuickHireModal'
import { usePersistentState } from '../hooks/usePersistentState'
import { ReplaceModal } from '../components/vahta/ReplaceModal'
import { usePeriodStore } from '../store/period'
import { useAuthStore } from '../store/auth'
import { toast } from '../store/toasts'
import type {
  Company,
  VahtaCandidate,
  VahtaCard,
  VahtaCrew,
  VahtaKind,
  VahtaMonth,
  VahtaRow,
  VahtaSite,
  VahtaView,
} from '../types/api'
import { formatMoney } from '../utils/money'
import { UI_KEYS } from '../utils/persist'
import { companyLabel } from '../utils/companies'

/** Должности строк. Из должности следует и способ оплаты начальника охраны. */
const KINDS: { value: VahtaKind; label: string }[] = [
  { value: 'guard', label: 'Охранник' },
  { value: 'gbr', label: 'ГБР' },
  { value: 'dispatcher', label: 'Диспетчер' },
  { value: 'chief', label: 'Начальник охраны' },
]

/** Родительный падеж — «1–15 сентября». */
const MONTHS_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

const isVahtaView = (v: unknown): boolean => v === 'month' || v === 1 || v === 2

const MONTHS = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

/**
 * Ширины липких левых колонок. Фиксированные, потому что смещение каждой
 * следующей считается от суммы предыдущих — «по содержимому» они разъедутся с
 * шапкой (та же причина, что в основном табеле).
 */
const COL_NAME_W = 200
const COL_ROLE_W = 112
const COL_RATE_W = 76
/** Ширина ячейки дня. 31 × 20 = 620 px — вместе с остальным влезает в ноутбук. */
const DAY_W = 20
const LEFT_ROLE = COL_NAME_W
const LEFT_RATE = COL_NAME_W + COL_ROLE_W

function money(value: string | null | undefined): string {
  return formatMoney(value ?? null, { showZero: true })
}

/** Русские числительные: «1 объект», «2 объекта», «5 объектов». */
function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return `${n} ${many}`
  if (b > 1 && b < 5) return `${n} ${few}`
  return `${n} ${b === 1 ? one : many}`
}

/**
 * Место работы для окон постановки и найма: пост внутри объекта или выездной
 * экипаж ГБР. Отсюда берутся ставка по умолчанию и проценты, но из разных
 * мест — поэтому в запрос уходит либо `post_id`, либо `crew_id`.
 */
interface Place {
  key: string
  kind: 'post' | 'crew'
  id: number
  label: string
  /** Должность, обычная для этого места: у экипажа ГБР, у поста охранник. */
  defaultKind: VahtaKind
  rate: string | null
}

function placesOf(sites: VahtaSite[], crews: VahtaCrew[]): Place[] {
  const fromCrews: Place[] = crews.map((c) => ({
    key: `crew-${c.id}`,
    kind: 'crew',
    id: c.id,
    label: c.name,
    defaultKind: 'gbr',
    rate: c.shift_rate,
  }))
  const fromPosts: Place[] = sites.flatMap((site) =>
    site.posts.map((post) => ({
      key: `post-${post.id}`,
      kind: 'post' as const,
      id: post.id,
      label: `${site.name} · ${post.name}`,
      defaultKind: 'guard',
      rate: post.effective_rate,
    })),
  )
  return [...fromCrews, ...fromPosts]
}

const placeRef = (place: Place) =>
  place.kind === 'post' ? { post_id: place.id } : { crew_id: place.id }

// ── Строка человека ───────────────────────────────────────────────────────────

interface PersonRowProps {
  row: VahtaRow
  /** Показанный отрезок сетки: весь месяц или одна расчётная половина. */
  firstDay: number
  lastDay: number
  midDay: number
  showMoney: boolean
  canManage: boolean
  canEdit: boolean
  canOpenCard: boolean
  query: string
  onPaintStart: (row: VahtaRow, day: number) => void
  onPaintOver: (row: VahtaRow, day: number) => void
  onMoney: (row: VahtaRow, anchor: DOMRect) => void
  onReplace: (row: VahtaRow) => void
  onRemove: (row: VahtaRow) => void
  onKind: (row: VahtaRow, kind: VahtaKind) => void
}

/** Подсветка найденного куска — без неё в 85 строках совпадение не заметить. */
function highlight(text: string | null, query: string) {
  if (!text) return null
  if (!query) return text
  const i = text.toLowerCase().indexOf(query.toLowerCase())
  if (i < 0) return text
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded-[2px] bg-amber-200 text-inherit">
        {text.slice(i, i + query.length)}
      </mark>
      {text.slice(i + query.length)}
    </>
  )
}

/**
 * Строка мемоизирована: при протяжке по дням перерисовываться должна ТОЛЬКО та
 * строка, по которой ведут. Дни сравниваются по содержимому — массив
 * пересобирается на каждый патч и по ссылке не совпал бы никогда.
 */
const PersonRow = memo(
  function PersonRow({
    row, firstDay, lastDay, midDay, showMoney, canManage, canEdit, canOpenCard, query,
    onPaintStart, onPaintOver, onMoney, onReplace, onRemove, onKind,
  }: PersonRowProps) {
    const marked = useMemo(() => new Set(row.days), [row.days])
    const days = useMemo(
      () => Array.from({ length: lastDay - firstDay + 1 }, (_, i) => firstDay + i),
      [firstDay, lastDay],
    )
    const hasAdjustments =
      parseFloat(row.premium ?? '0') > 0 ||
      parseFloat(row.penalty ?? '0') > 0 ||
      parseFloat(row.official_payout ?? '0') > 0

    return (
      <tr
        data-row-id={row.id}
        className="group border-b border-slate-100 hover:bg-slate-50"
      >
        {/* Одна строка с многоточием: перенос ФИО ломал высоту строки и
            разъезжался с сеткой дней. Полное имя — в подсказке. */}
        <td
          className="sticky left-0 z-10 overflow-hidden text-ellipsis whitespace-nowrap bg-[#FBFBF9] px-2 py-1 group-hover:bg-slate-50"
          style={{ width: COL_NAME_W, minWidth: COL_NAME_W, maxWidth: COL_NAME_W }}
          title={row.employee_name ?? 'вакансия'}
        >
          {row.employee_id && canOpenCard ? (
            <Link
              to={`/admin/employees?employee_id=${row.employee_id}`}
              title="Открыть карточку сотрудника"
              className="font-medium text-slate-800 hover:text-blue-700 hover:underline"
            >
              {highlight(row.employee_name, query)}
            </Link>
          ) : row.employee_name ? (
            <span className="font-medium text-slate-800">
              {highlight(row.employee_name, query)}
            </span>
          ) : (
            <span className="italic text-slate-400">вакансия</span>
          )}
          {row.tab_number && (
            <span className="ml-1.5 font-mono text-[10px] text-slate-400">
              {highlight(row.tab_number, query)}
            </span>
          )}
        </td>

        {/* Должность — свойство ЧЕЛОВЕКА на месте, а не места: на одном посту
            стоят и ГБР, и охранник. Поэтому правится прямо в строке. */}
        <td
          className="sticky z-10 bg-[#FBFBF9] px-1 py-1 text-[11.5px] text-slate-500 group-hover:bg-slate-50"
          style={{ left: LEFT_ROLE, width: COL_ROLE_W, minWidth: COL_ROLE_W, maxWidth: COL_ROLE_W }}
        >
          {canEdit ? (
            <select
              value={row.kind}
              onChange={(e) => onKind(row, e.target.value as VahtaKind)}
              className="w-full cursor-pointer truncate rounded border border-transparent bg-transparent px-1 py-0.5 text-[11.5px] hover:border-slate-300 hover:bg-white"
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          ) : (
            row.kind_label
          )}
        </td>

        <td
          className="sticky z-10 border-r border-slate-200 bg-[#FBFBF9] px-2 py-1 text-right tabular-nums group-hover:bg-slate-50"
          style={{ left: LEFT_RATE, width: COL_RATE_W, minWidth: COL_RATE_W }}
        >
          {showMoney ? money(row.rate) : ''}
        </td>

        {days.map((day) => (
          <td
            key={day}
            className={`p-0 text-center ${
              day === midDay + 1 && day !== firstDay ? 'border-l-2 border-slate-800' : ''
            }`}
            style={{ width: DAY_W, minWidth: DAY_W }}
          >
            <button
              type="button"
              disabled={!canEdit}
              onMouseDown={(e) => {
                e.preventDefault()
                onPaintStart(row, day)
              }}
              onMouseEnter={() => onPaintOver(row, day)}
              className={`m-px h-[18px] w-[18px] rounded-[3px] text-[10.5px] leading-none tabular-nums ${
                marked.has(day)
                  ? 'bg-emerald-600 text-white'
                  : 'bg-slate-200 text-slate-400'
              } ${canEdit ? 'cursor-pointer hover:outline hover:outline-2 hover:outline-sky-400' : ''}`}
            >
              {day}
            </button>
          </td>
        ))}

        <td className="px-1.5 py-1 text-center font-bold tabular-nums" style={{ width: 40 }}>
          {row.shifts || ''}
        </td>

        {showMoney && (
          <>
            <td className="px-2 py-1 text-right font-bold tabular-nums text-emerald-800">
              {parseFloat(row.accrued ?? '0') ? money(row.accrued) : '—'}
            </td>
            {/* Налог на официальную часть: сверх начисленного, но входит в
                разнесение по юрлицам — отсюда и разница сумм в подвале. */}
            <td
              className="px-2 py-1 text-right tabular-nums text-slate-600"
              title={
                parseFloat(row.tax ?? '0')
                  ? `Налог с официальной выплаты ${money(row.official_payout)}. ` +
                    `Входит в разнесение по юрлицам: ${money(row.accrued)} + ` +
                    `${money(row.tax)} = ${money(row.distribution_base)}`
                  : 'Официальной выплаты нет — налога нет, разносится начисленное'
              }
            >
              {parseFloat(row.tax ?? '0') ? money(row.tax) : '—'}
            </td>
            <td className="px-2 py-1 text-right" style={{ width: 150 }}>
              {/* Премия и штраф вводятся отсюда: пустая ячейка прямо предлагает
                  их завести, заполненная показывает суммы словами. */}
              {canManage ? (
                <button
                  type="button"
                  onClick={(e) =>
                    onMoney(row, (e.currentTarget as HTMLElement).getBoundingClientRect())
                  }
                  className={`cursor-pointer rounded px-1.5 py-0.5 text-[11.5px] ${
                    hasAdjustments
                      ? 'text-slate-600 hover:bg-white hover:outline hover:outline-1 hover:outline-slate-300'
                      : 'text-slate-400 outline outline-1 outline-dashed outline-slate-300 hover:bg-white'
                  }`}
                >
                  {hasAdjustments ? (
                    <>
                      {parseFloat(row.premium ?? '0') > 0 && (
                        <span className="text-emerald-700">+{money(row.premium)} </span>
                      )}
                      {parseFloat(row.penalty ?? '0') > 0 && (
                        <span className="text-red-700">−{money(row.penalty)} </span>
                      )}
                      {parseFloat(row.official_payout ?? '0') > 0 && (
                        <span>оф. {money(row.official_payout)}</span>
                      )}
                    </>
                  ) : (
                    'премия, штраф'
                  )}
                </button>
              ) : (
                <span className="text-[11.5px] text-slate-500">
                  {hasAdjustments ? money(row.accrued) : '—'}
                </span>
              )}
            </td>
            {/* К выплате = начислено − оф. выплата, вверх до 500 ₽ по половинам. */}
            <td
              className="px-2 py-1 text-right font-bold tabular-nums text-slate-800"
              title={
                `Начислено минус официальная выплата — остаток из кассы. ` +
                `Точно ${money(row.net_payout_exact)}, округлено вверх до 500 ₽ ` +
                `по каждой половине`
              }
            >
              {parseFloat(row.accrued ?? '0') ? money(row.net_payout) : '—'}
            </td>
          </>
        )}

        <td className="px-1 py-1 text-right whitespace-nowrap" style={{ width: 48 }}>
          {canEdit && (
            <button
              type="button"
              title="Кто сменит на посту"
              onClick={() => onReplace(row)}
              className="cursor-pointer rounded px-1.5 text-slate-300 hover:bg-slate-200 hover:text-slate-700"
            >
              ⋯
            </button>
          )}
          {canManage && (
            <button
              type="button"
              title="Убрать из табеля"
              onClick={() => onRemove(row)}
              className="cursor-pointer rounded px-1.5 text-slate-300 hover:bg-slate-200 hover:text-red-700"
            >
              ✕
            </button>
          )}
        </td>
      </tr>
    )
  },
  (a, b) =>
    a.row.id === b.row.id &&
    a.row.days.join(',') === b.row.days.join(',') &&
    a.row.accrued === b.row.accrued &&
    a.row.tax === b.row.tax &&
    a.row.net_payout === b.row.net_payout &&
    a.row.net_payout_exact === b.row.net_payout_exact &&
    a.row.premium === b.row.premium &&
    a.row.penalty === b.row.penalty &&
    a.row.official_payout === b.row.official_payout &&
    a.row.rate === b.row.rate &&
    a.row.kind === b.row.kind &&
    a.row.employee_name === b.row.employee_name &&
    a.query === b.query &&
    a.showMoney === b.showMoney &&
    a.canManage === b.canManage &&
    a.canEdit === b.canEdit &&
    a.firstDay === b.firstDay &&
    a.lastDay === b.lastDay,
)

// ── Экран ─────────────────────────────────────────────────────────────────────

export function VahtaPage() {
  const { year, month, setPeriod } = usePeriodStore()
  const role = useAuthStore((s) => s.user?.role)
  const roleCanManage = role === 'admin' || role === 'manager'
  const canOpenCard = role === 'admin' || role === 'manager' || role === 'accountant'

  // Режим отображения: месяц целиком или расчётная половина. Запоминается между
  // заходами, как остальные настройки вида.
  const [view, setView] = usePersistentState<VahtaView>(
    UI_KEYS.vahtaView, 'month', isVahtaView,
  )
  const [data, setData] = useState<VahtaMonth | null>(null)
  const [sites, setSites] = useState<VahtaSite[]>([])
  const [crews, setCrews] = useState<VahtaCrew[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [loading, setLoading] = useState(true)

  const [query, setQuery] = useState('')
  const [zoneFilter, setZoneFilter] = useState('')
  const [kindFilter, setKindFilter] = useState('')

  const [replaceRow, setReplaceRow] = useState<VahtaRow | null>(null)
  const [hireAt, setHireAt] = useState<VahtaCard | null>(null)
  const [addTo, setAddTo] = useState<VahtaCard | null>(null)
  const [moneyRow, setMoneyRow] = useState<{ row: VahtaRow; anchor: DOMRect } | null>(null)

  const reload = useCallback(async () => {
    try {
      // Суммы половины считает сервер — фронт их из месячных не вычитает.
      setData(await getVahtaMonth(year, month, null, view === 'month' ? null : view))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось загрузить табель вахты')
    } finally {
      setLoading(false)
    }
  }, [year, month, view])

  useEffect(() => {
    setLoading(true)
    void reload()
  }, [reload])

  useEffect(() => {
    listVahtaSites().then(setSites).catch(() => setSites([]))
    listVahtaCrews().then(setCrews).catch(() => setCrews([]))
    listCompanies().then(setCompanies).catch(() => setCompanies([]))
  }, [])

  const zones = data?.zones ?? []
  const places = useMemo(() => placesOf(sites, crews), [sites, crews])
  const showMoney = Boolean(data?.can_see_money)
  const canEdit = Boolean(data?.can_edit)
  // Закрытый месяц: бэк отклоняет любую правку назначений (409), поэтому и
  // кнопки «поставить / заменить / убрать», и правка сумм гаснут вместе с днями.
  const periodClosed = Boolean(data?.period_closed)
  const canManage = roleCanManage && !periodClosed
  const filtering = Boolean(query || zoneFilter || kindFilter)

  const placesOfCard = useCallback(
    (card: VahtaCard) =>
      places.filter((p) =>
        card.kind === 'crew'
          ? p.kind === 'crew' && p.id === card.id
          : p.kind === 'post' &&
            sites.find((s) => s.id === card.id)?.posts.some((x) => x.id === p.id),
      ),
    [places, sites],
  )

  const kinds = useMemo(
    () => [
      ...new Set(
        zones.flatMap((z) => z.cards.flatMap((c) => c.rows.map((r) => r.kind_label))),
      ),
    ],
    [zones],
  )

  /** Отбор строк: поиск идёт и по человеку, и по месту работы. */
  const matches = useCallback(
    (row: VahtaRow, card: VahtaCard) => {
      if (kindFilter && row.kind_label !== kindFilter) return false
      if (!query) return true
      const q = query.toLowerCase()
      return (
        (row.employee_name ?? 'вакансия').toLowerCase().includes(q) ||
        (row.tab_number ?? '').toLowerCase().includes(q) ||
        card.name.toLowerCase().includes(q) ||
        row.post_name.toLowerCase().includes(q)
      )
    },
    [query, kindFilter],
  )

  const shownZones = useMemo(
    () =>
      zones
        .filter((z) => !zoneFilter || z.zone_name === zoneFilter)
        .map((z) => ({
          zone: z,
          cards: z.cards
            .map((card) => ({ card, rows: card.rows.filter((r) => matches(r, card)) }))
            .filter((g) => g.rows.length > 0 || !filtering),
        }))
        .filter((z) => z.cards.length > 0),
    [zones, zoneFilter, matches, filtering],
  )

  const shownRows = useMemo(
    () => shownZones.flatMap((z) => z.cards.flatMap((c) => c.rows)),
    [shownZones],
  )
  const shownPeople = useMemo(
    () => new Set(shownRows.filter((r) => r.employee_id).map((r) => r.employee_id)).size,
    [shownRows],
  )

  // ── Протяжка по дням ───────────────────────────────────────────────────────
  // Локально красим сразу, а на сервер уходит ОДИН запрос на отпускание кнопки:
  // иначе протяжка по неделе стоила бы семь запросов подряд.
  const painting = useRef<{ rowId: number; on: boolean; days: Set<number> } | null>(null)

  const patchDays = useCallback((rowId: number, days: number[]) => {
    setData((prev) => {
      if (!prev) return prev
      // `days` — отметки всего месяца; смены — только в показанном отрезке.
      const shifts = days.filter((d) => d >= prev.first_day && d <= prev.last_day).length
      return {
        ...prev,
        zones: prev.zones.map((z) => ({
          ...z,
          cards: z.cards.map((c) => ({
            ...c,
            rows: c.rows.map((r) =>
              r.id === rowId ? { ...r, days, shifts } : r,
            ),
          })),
        })),
      }
    })
  }, [])

  const onPaintStart = useCallback(
    (row: VahtaRow, day: number) => {
      if (!canEdit) return
      const set = new Set(row.days)
      const on = !set.has(day)
      on ? set.add(day) : set.delete(day)
      painting.current = { rowId: row.id, on, days: set }
      patchDays(row.id, [...set].sort((a, b) => a - b))
    },
    [canEdit, patchDays],
  )

  const onPaintOver = useCallback(
    (row: VahtaRow, day: number) => {
      const p = painting.current
      if (!p || p.rowId !== row.id) return
      if (p.on ? p.days.has(day) : !p.days.has(day)) return
      p.on ? p.days.add(day) : p.days.delete(day)
      patchDays(row.id, [...p.days].sort((a, b) => a - b))
    },
    [patchDays],
  )

  useEffect(() => {
    const finish = async () => {
      const p = painting.current
      painting.current = null
      if (!p) return
      try {
        await setVahtaDays(p.rowId, [...p.days])
        // Суммы считает сервер — после отметки перечитываем месяц.
        await reload()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось сохранить смены')
        await reload()
      }
    }
    document.addEventListener('mouseup', finish)
    return () => document.removeEventListener('mouseup', finish)
  }, [reload])

  // ── Действия ───────────────────────────────────────────────────────────────
  const handleCopy = async () => {
    try {
      const result = await copyPreviousVahtaPeriod(year, month)
      toast.success(`Скопировано строк: ${result.copied}`)
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось скопировать период')
    }
  }

  const handleExport = async () => {
    try {
      const response = await apiClient.get(vahtaExcelUrl(year, month), {
        responseType: 'blob',
      })
      const url = URL.createObjectURL(response.data as Blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `vahta_${year}_${String(month).padStart(2, '0')}.xlsx`
      link.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось выгрузить табель')
    }
  }

  const handleRemove = useCallback(
    async (row: VahtaRow) => {
      if (!window.confirm('Убрать строку из табеля? Сотрудник останется в справочнике.'))
        return
      try {
        await deleteVahtaAssignment(row.id)
        await reload()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось убрать строку')
      }
    },
    [reload],
  )

  const onMoney = useCallback((row: VahtaRow, anchor: DOMRect) => {
    setMoneyRow({ row, anchor })
  }, [])
  const onReplace = useCallback((row: VahtaRow) => setReplaceRow(row), [])

  const onKind = useCallback(
    async (row: VahtaRow, kind: VahtaKind) => {
      try {
        await updateVahtaAssignment(row.id, { kind })
        await reload()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось сменить должность')
      }
    },
    [reload],
  )

  const companyName = useCallback(
    (id: number) => {
      const company = companies.find((c) => c.id === id)
      return company ? companyLabel(company) : `#${id}`
    },
    [companies],
  )

  if (loading) return <p className="text-sm text-gray-500">Загрузка…</p>

  if (!data || (zones.length === 0 && data.departments.length === 0)) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        Подразделений охраны не найдено. Отметьте отдел как «подразделение охраны» в{' '}
        <Link to="/admin/org" className="underline">
          оргструктуре
        </Link>
        , затем заведите зоны обслуживания, объекты с постами и экипажи ГБР в{' '}
        <Link to="/vahta/posts" className="underline">
          настройках вахты
        </Link>
        .
      </div>
    )
  }

  const days = Array.from(
    { length: data.last_day - data.first_day + 1 },
    (_, i) => data.first_day + i,
  )
  const mid = data.first_half_last_day
  const periodLabel =
    data.view_half === null
      ? `${MONTHS[month - 1]} ${year}, периоды 1–${mid} и ${mid + 1}–${data.days_in_month}`
      : `${data.view_half === 1 ? 'Первая' : 'Вторая'} половина: ` +
        `${data.first_day}–${data.last_day} ${MONTHS_GEN[month - 1]} ${year}`
  const viewOptions: { value: VahtaView; label: string }[] = [
    { value: 'month', label: 'Месяц' },
    { value: 1, label: `1–${mid}` },
    { value: 2, label: `${mid + 1}–${data.days_in_month}` },
  ]

  return (
    <div className="-m-6 flex h-[calc(100vh-3.5rem)] flex-col bg-[#FBFBF9] text-[#12263A]">
      {/* ── Шапка ─────────────────────────────────────────────────────────── */}
      <div className="border-b-2 border-slate-800 px-5 pb-2.5 pt-3.5">
        <div className="flex flex-wrap items-baseline gap-3.5">
          <h1 className="text-[19px] font-bold tracking-tight">Вахта</h1>
          <span
            className={
              data.view_half === null
                ? 'text-slate-500'
                : 'rounded bg-amber-100 px-2 py-0.5 font-semibold text-amber-900'
            }
          >
            {periodLabel}
          </span>
          {data.view_half !== null && (
            <span className="text-xs text-slate-500">
              смены, начисления и итоги — только за эту половину
            </span>
          )}
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Найти человека, пост или объект"
            className="w-[280px] rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm"
          />
          <select
            value={zoneFilter}
            onChange={(e) => setZoneFilter(e.target.value)}
            className="rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm"
          >
            <option value="">Все зоны</option>
            {zones.map((z) => (
              <option key={z.zone_id} value={z.zone_name}>
                {z.zone_name}
              </option>
            ))}
          </select>
          <select
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
            className="rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm"
          >
            <option value="">Все должности</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          {filtering && (
            <button
              type="button"
              onClick={() => {
                setQuery('')
                setZoneFilter('')
                setKindFilter('')
              }}
              className="cursor-pointer rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm hover:bg-slate-100"
            >
              Сбросить
            </button>
          )}

          <select
            value={month}
            onChange={(e) => setPeriod(year, Number(e.target.value))}
            className="ml-2 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm"
          >
            {MONTHS.map((name, i) => (
              <option key={name} value={i + 1}>
                {name}
              </option>
            ))}
          </select>
          <input
            type="number"
            value={year}
            onChange={(e) => setPeriod(Number(e.target.value), month)}
            className="w-20 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm"
          />

          {/* Режим отображения: месяц — общая картина, половины — сверка выплат. */}
          <div
            role="group"
            aria-label="Период отображения"
            className="flex overflow-hidden rounded-md border border-slate-200 bg-white text-sm"
          >
            {viewOptions.map((opt) => (
              <button
                key={String(opt.value)}
                type="button"
                aria-pressed={view === opt.value}
                onClick={() => setView(opt.value)}
                className={`cursor-pointer border-l border-slate-200 px-3 py-1.5 first:border-l-0 ${
                  view === opt.value
                    ? 'bg-slate-800 font-semibold text-white'
                    : 'text-slate-700 hover:bg-slate-100'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {canManage && (
            <Button variant="secondary" size="sm" onClick={handleCopy}>
              Скопировать прошлый период
            </Button>
          )}
          {showMoney && (
            <Button variant="secondary" size="sm" onClick={handleExport}>
              Выгрузить в Excel
            </Button>
          )}
          {canManage && (
            <Link
              to="/vahta/staff"
              className="rounded-md bg-slate-100 px-3 py-1.5 text-sm text-slate-800 hover:bg-slate-200"
            >
              Сотрудники
            </Link>
          )}
          {canManage && (
            <Link
              to="/vahta/posts"
              className="rounded-md bg-slate-100 px-3 py-1.5 text-sm text-slate-800 hover:bg-slate-200"
            >
              Настройки
            </Link>
          )}

          <span className="ml-auto text-xs text-slate-500">
            {plural(shownPeople, 'человек', 'человека', 'человек')},{' '}
            {plural(shownRows.length, 'строка', 'строки', 'строк')}
          </span>
        </div>
      </div>

      {periodClosed && (
        <p className="border-b border-amber-200 bg-amber-50 px-5 py-1.5 text-[12px] text-amber-800">
          Период закрыт — назначения, смены и суммы вахты за этот месяц не меняются.
          Чтобы внести правку, период нужно переоткрыть.
        </p>
      )}

      {canEdit && (
        <p className="border-b border-slate-200 px-5 py-1.5 text-[11.5px] text-slate-500">
          Клик по дню ставит и снимает смену. Зажмите и проведите, чтобы отметить
          сразу несколько дней.
        </p>
      )}

      {/* ── Таблица ───────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto">
        <table className="w-max min-w-full border-separate border-spacing-0 text-[13px]">
          <thead>
            <tr>
              <th
                className="sticky left-0 top-0 z-30 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-left text-[11px] font-semibold text-slate-500"
                style={{ width: COL_NAME_W, minWidth: COL_NAME_W }}
              >
                Сотрудник
              </th>
              <th
                className="sticky top-0 z-30 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-left text-[11px] font-semibold text-slate-500"
                style={{ left: LEFT_ROLE, width: COL_ROLE_W, minWidth: COL_ROLE_W }}
              >
                Должность
              </th>
              <th
                className="sticky top-0 z-30 border-b border-r border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-right text-[11px] font-semibold text-slate-500"
                style={{ left: LEFT_RATE, width: COL_RATE_W, minWidth: COL_RATE_W }}
              >
                {showMoney ? 'Ставка' : ''}
              </th>
              {days.map((day) => (
                <th
                  key={day}
                  className={`sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9] py-1.5 text-center text-[11px] font-semibold text-slate-500 ${
                    day === mid + 1 && day !== data.first_day
                      ? 'border-l-2 border-l-slate-800'
                      : ''
                  }`}
                  style={{ width: DAY_W, minWidth: DAY_W }}
                >
                  {day}
                </th>
              ))}
              <th className="sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-center text-[11px] font-semibold text-slate-500">
                Смен
              </th>
              {showMoney && (
                <>
                  <th className="sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-right text-[11px] font-semibold text-slate-500">
                    Начислено
                  </th>
                  <th
                    className="sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-right text-[11px] font-semibold text-slate-500"
                    title="Налог на официальную выплату. Входит в разнесение по юрлицам сверх начисленного."
                  >
                    Налог{data.employer_tax_percent ? ` ${Number(data.employer_tax_percent)} %` : ''}
                  </th>
                  <th className="sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-right text-[11px] font-semibold text-slate-500">
                    Премия и штраф
                  </th>
                  <th className="sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9] px-2 py-1.5 text-right text-[11px] font-semibold text-slate-500">
                    К выплате
                  </th>
                </>
              )}
              <th className="sticky top-0 z-20 border-b border-slate-200 bg-[#FBFBF9]" />
            </tr>
          </thead>

          <tbody>
            {shownZones.map(({ zone, cards }) => {
              const zoneRows = cards.flatMap((c) => c.rows)
              const crewCount = cards.filter((c) => c.card.kind === 'crew').length
              const siteCount = cards.filter((c) => c.card.kind === 'site').length
              const totalCols = 4 + days.length + (showMoney ? 4 : 0)
              return (
                <ZoneGroup key={zone.zone_id}>
                  {/* Спина зоны: верхний уровень группировки, без карточек. */}
                  <tr>
                    <td
                      className="sticky left-0 z-10 border-b-2 border-slate-800 bg-[#FBFBF9] px-2 pb-1.5 pt-4"
                      style={{ width: COL_NAME_W }}
                    >
                      <span className="text-[15px] font-bold tracking-tight">
                        {zone.zone_name}
                      </span>
                    </td>
                    <td
                      className="sticky z-10 border-b-2 border-slate-800 bg-[#FBFBF9]"
                      style={{ left: LEFT_ROLE }}
                    />
                    <td
                      className="sticky z-10 border-b-2 border-r border-slate-800 bg-[#FBFBF9]"
                      style={{ left: LEFT_RATE }}
                    />
                    <td
                      className="border-b-2 border-slate-800 px-2 pb-1.5 pt-4"
                      colSpan={totalCols - 3}
                    >
                      <span className="text-xs text-slate-500">
                        {filtering
                          ? `${plural(zoneRows.length, 'строка', 'строки', 'строк')} найдено`
                          : [
                              plural(crewCount, 'экипаж', 'экипажа', 'экипажей'),
                              plural(siteCount, 'объект', 'объекта', 'объектов'),
                              plural(zone.total_shifts, 'смена', 'смены', 'смен'),
                            ].join(', ')}
                      </span>
                      {showMoney && (
                        <span className="float-right font-bold">
                          {money(zone.total_accrued)}
                        </span>
                      )}
                    </td>
                  </tr>

                  {cards.map(({ card, rows }) => (
                    <ZoneGroup key={`${card.kind}-${card.id}`}>
                      {/* Подстрока места работы: экипаж ГБР или объект. */}
                      <tr className="bg-slate-100/70">
                        {/* Название места — в одну строку: «SH - RIVER SH -
                            FOREST» переносилось и ломало высоту подстроки. */}
                        <td
                          className="sticky left-0 z-10 overflow-hidden text-ellipsis whitespace-nowrap border-b border-slate-200 bg-slate-100/70 px-2 py-1"
                          style={{ width: COL_NAME_W, minWidth: COL_NAME_W, maxWidth: COL_NAME_W }}
                          title={card.name}
                        >
                          <span
                            className={`mr-1.5 rounded px-1 py-px text-[9.5px] font-bold tracking-wide ${
                              card.kind === 'crew'
                                ? 'bg-amber-200 text-amber-900'
                                : 'bg-slate-300 text-slate-700'
                            }`}
                          >
                            {card.kind === 'crew' ? 'Экипаж ГБР' : 'Объект'}
                          </span>
                          <span className="font-semibold">
                            {highlight(card.name, query)}
                          </span>
                        </td>
                        <td
                          className="sticky z-10 border-b border-slate-200 bg-slate-100/70"
                          style={{ left: LEFT_ROLE }}
                        />
                        <td
                          className="sticky z-10 border-b border-r border-slate-200 bg-slate-100/70"
                          style={{ left: LEFT_RATE }}
                        />
                        <td
                          className="border-b border-slate-200 px-2 py-1"
                          colSpan={totalCols - 3}
                        >
                          <span className="text-[11.5px] text-slate-500">
                            {card.objects.length > 0 &&
                              (card.kind === 'crew'
                                ? `выезжает на ${card.objects.join(', ')}`
                                : `посты: ${card.objects.join(', ')}`)}
                          </span>
                          {canManage && (
                            <span className="float-right flex gap-3 text-[11.5px]">
                              <button
                                type="button"
                                onClick={() => setAddTo(card)}
                                className="cursor-pointer text-blue-700 hover:underline"
                              >
                                + поставить
                              </button>
                              <button
                                type="button"
                                onClick={() => setHireAt(card)}
                                className="cursor-pointer text-blue-700 hover:underline"
                              >
                                оформить нового
                              </button>
                            </span>
                          )}
                        </td>
                      </tr>

                      {rows.map((row) => (
                        <PersonRow
                          key={row.id}
                          row={row}
                          firstDay={data.first_day}
                          lastDay={data.last_day}
                          midDay={mid}
                          showMoney={showMoney}
                          canManage={canManage}
                          canEdit={canEdit}
                          canOpenCard={canOpenCard}
                          query={query}
                          onPaintStart={onPaintStart}
                          onPaintOver={onPaintOver}
                          onMoney={onMoney}
                          onReplace={onReplace}
                          onRemove={handleRemove}
                          onKind={onKind}
                        />
                      ))}
                    </ZoneGroup>
                  ))}
                </ZoneGroup>
              )
            })}
          </tbody>
        </table>

        {shownRows.length === 0 && (
          <p className="p-8 text-center text-sm text-slate-400">
            {filtering
              ? 'Никого не нашлось. Сбросьте фильтры или измените запрос.'
              : 'За этот месяц состав не заведён. Поставьте людей на посты или скопируйте прошлый период.'}
          </p>
        )}
      </div>

      {/* ── Подвал ────────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-baseline gap-6 border-t-2 border-slate-800 px-5 py-2 text-[12.5px]">
        <span>
          <span className="mr-1.5 text-slate-500">Смен</span>
          <b className="text-[15px]">{data.total_shifts}</b>
        </span>
        {showMoney && (
          <>
            <span>
              <span className="mr-1.5 text-slate-500">Начислено</span>
              <b className="text-[15px]">{money(data.total_accrued)}</b>
            </span>
            <span>
              <span className="mr-1.5 text-slate-500">К выплате</span>
              <b className="text-[15px]">{money(data.total_net_payout)}</b>
            </span>
            {/* Налоги объясняют, почему разнесение больше начисленного. */}
            <span title="Налог на официальную часть выплаты. Затрата компании: входит в разнесение по юрлицам, но не в начислено и не в выплату.">
              <span className="mr-1.5 text-slate-500">
                Налоги{data.employer_tax_percent ? ` (${Number(data.employer_tax_percent)} % от оф. выплаты)` : ''}
              </span>
              <b className="text-[15px]">{money(data.total_tax)}</b>
            </span>
          </>
        )}
        {data.view_half === null && data.halves.map((half) => (
          <span key={half.half}>
            <span className="mr-1.5 text-slate-500">
              {half.half === 1 ? `1–${mid}` : `${mid + 1}–${data.days_in_month}`}
            </span>
            <b className="text-[15px]">
              {showMoney ? money(half.accrued) : plural(half.shifts, 'смена', 'смены', 'смен')}
            </b>
          </span>
        ))}
        {showMoney && data.company_totals.length > 0 && (
          <span className="text-slate-500">
            <span className="mr-1.5">
              Разнесено по юрлицам {money(data.total_distribution)}
              {/* Равенство пишем, только когда оно верно: строка без процентов
                  места работы в разнесение не попадает вовсе. */}
              {parseFloat(data.total_tax ?? '0') > 0 &&
                Math.abs(
                  parseFloat(data.total_distribution ?? '0') -
                    parseFloat(data.total_distribution_base ?? '0'),
                ) < 0.005 &&
                ` = начислено ${money(data.total_accrued)} + налоги ${money(data.total_tax)}`}
              :
            </span>
            {data.company_totals
              .map((t) => `${companyName(t.company_id)} ${money(t.amount)}`)
              .join(' · ')}
          </span>
        )}
      </div>

      {/* ── Окна ──────────────────────────────────────────────────────────── */}
      {replaceRow && (
        <ReplaceModal
          row={replaceRow}
          year={year}
          month={month}
          daysInMonth={data.days_in_month}
          midDay={mid}
          onClose={() => setReplaceRow(null)}
          onDone={() => {
            setReplaceRow(null)
            void reload()
          }}
        />
      )}

      {hireAt && (
        <QuickHireModal
          year={year}
          month={month}
          places={placesOfCard(hireAt)}
          allPlaces={places}
          onClose={() => setHireAt(null)}
          onDone={() => {
            setHireAt(null)
            void reload()
          }}
        />
      )}

      {addTo && (
        <AddToPostModal
          year={year}
          month={month}
          places={placesOfCard(addTo)}
          onClose={() => setAddTo(null)}
          onDone={() => {
            setAddTo(null)
            void reload()
          }}
        />
      )}

      {moneyRow && (
        <MoneyPopover
          row={moneyRow.row}
          anchor={moneyRow.anchor}
          midDay={mid}
          daysInMonth={data.days_in_month}
          onClose={() => setMoneyRow(null)}
          onDone={() => {
            setMoneyRow(null)
            void reload()
          }}
        />
      )}
    </div>
  )
}

/** Группировка строк без лишнего DOM: `<tbody>` внутри `<tbody>` нельзя. */
function ZoneGroup({ children }: { children: ReactNode }) {
  return <>{children}</>
}

/**
 * Премия, штраф и официальная выплата — СВОИ У КАЖДОЙ расчётной половины:
 * выплат в месяце две, и в образце заказчика эти суммы стоят на каждом из двух
 * листов отдельно.
 *
 * Поповер, а не модалка: таблица остаётся видна, и понятно, к какой строке
 * относятся суммы.
 */
function MoneyPopover({
  row,
  anchor,
  midDay,
  daysInMonth,
  onClose,
  onDone,
}: {
  row: VahtaRow
  anchor: DOMRect
  midDay: number
  daysInMonth: number
  onClose: () => void
  onDone: () => void
}) {
  const half = (n: number) => row.halves.find((h) => h.half === n)
  // В режиме половины строка приходит ТОЛЬКО с ней. Поля другой половины здесь
  // не показываются и НЕ отправляются: иначе их «0» по умолчанию затёр бы
  // премию и оф. выплату, заведённые в другой половине.
  const shownHalves = [1, 2].filter((n) => half(n) !== undefined)
  const [form, setForm] = useState({
    premium_h1: half(1)?.premium ?? '0',
    premium_h2: half(2)?.premium ?? '0',
    penalty_h1: half(1)?.penalty ?? '0',
    penalty_h2: half(2)?.penalty ?? '0',
    official_payout_h1: half(1)?.official_payout ?? '0',
    official_payout_h2: half(2)?.official_payout ?? '0',
    is_official: row.is_official,
    note: row.note ?? '',
  })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const field = (key: keyof typeof form) => (
    <input
      value={String(form[key] ?? '')}
      onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
      className="w-[84px] rounded border border-slate-200 px-1.5 py-1 text-right tabular-nums"
    />
  )

  const submit = async () => {
    setSaving(true)
    try {
      await updateVahtaAssignment(row.id, {
        ...(shownHalves.includes(1)
          ? {
              premium_h1: form.premium_h1 || '0',
              penalty_h1: form.penalty_h1 || '0',
              official_payout_h1: form.official_payout_h1 || '0',
            }
          : {}),
        ...(shownHalves.includes(2)
          ? {
              premium_h2: form.premium_h2 || '0',
              penalty_h2: form.penalty_h2 || '0',
              official_payout_h2: form.official_payout_h2 || '0',
            }
          : {}),
        is_official: form.is_official,
        note: form.note || null,
      })
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
    } finally {
      setSaving(false)
    }
  }

  // Поповер держится у строки, но не уезжает за край окна.
  const left = Math.min(Math.max(8, anchor.left - 210), window.innerWidth - 372)
  const top = Math.min(anchor.bottom + 6, window.innerHeight - 300)

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        className="fixed z-50 w-[356px] rounded-lg border border-slate-200 bg-white p-3 shadow-xl"
        style={{ left, top }}
      >
        <h3 className="mb-2 text-[13px] font-semibold">
          {row.employee_name ?? 'Вакансия'} — начисления
        </h3>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[11px] text-slate-500">
              <th />
              <th className="font-semibold">Премия</th>
              <th className="font-semibold">Штраф</th>
              <th className="font-semibold">Оф. выплата</th>
            </tr>
          </thead>
          <tbody>
            {shownHalves.includes(1) && (
            <tr>
              <td className="pr-2 text-right text-[11.5px] text-slate-500">1–{midDay}</td>
              <td className="p-0.5">{field('premium_h1')}</td>
              <td className="p-0.5">{field('penalty_h1')}</td>
              <td className="p-0.5">{field('official_payout_h1')}</td>
            </tr>
            )}
            {shownHalves.includes(2) && (
            <tr>
              <td className="pr-2 text-right text-[11.5px] text-slate-500">
                {midDay + 1}–{daysInMonth}
              </td>
              <td className="p-0.5">{field('premium_h2')}</td>
              <td className="p-0.5">{field('penalty_h2')}</td>
              <td className="p-0.5">{field('official_payout_h2')}</td>
            </tr>
            )}
          </tbody>
        </table>

        <label className="mt-2.5 flex items-center gap-2 text-[12px]">
          <input
            type="checkbox"
            checked={form.is_official}
            onChange={(e) => setForm((f) => ({ ...f, is_official: e.target.checked }))}
          />
          Трудоустройство: официальный
        </label>

        <input
          value={form.note}
          onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
          placeholder="Примечание"
          className="mt-2 w-full rounded border border-slate-200 px-2 py-1 text-[12px]"
        />

        <p className="mt-2 text-[11px] leading-snug text-slate-500">
          Начислено = зарплата + премия − штраф. К выплате = начислено − оф. выплата,
          остаток идёт из кассы. С оф. выплаты считается налог — он входит в
          разнесение по юрлицам сверх начисленного. К выплате округляется вверх
          до 500 ₽ по каждой половине, остальные суммы — точные.
        </p>

        <div className="mt-2.5 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Отмена
          </Button>
          <Button size="sm" onClick={submit} loading={saving}>
            Сохранить
          </Button>
        </div>
      </div>
    </>
  )
}

/**
 * «Поставить людей на место».
 *
 * Выбор ЛЮДЕЙ множественный: на посту ГБР их и правда несколько, а в новом
 * месяце состав набирают сразу. Список с ПОИСКОМ — в справочнике охраны под
 * сотню имён.
 */
function AddToPostModal({
  year,
  month,
  places,
  onClose,
  onDone,
}: {
  year: number
  month: number
  places: Place[]
  onClose: () => void
  onDone: () => void
}) {
  const [placeKey, setPlaceKey] = useState<string>(places[0]?.key ?? '')
  const [kind, setKind] = useState<VahtaKind | ''>('')
  const [candidates, setCandidates] = useState<VahtaCandidate[]>([])
  const [picked, setPicked] = useState<number[]>([])
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    listVahtaCandidates(year, month).then(setCandidates).catch(() => setCandidates([]))
  }, [year, month])

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return candidates
    return candidates.filter(
      (c) =>
        c.full_name.toLowerCase().includes(needle) ||
        (c.tab_number ?? '').toLowerCase().includes(needle),
    )
  }, [candidates, query])

  const toggle = (id: number) =>
    setPicked((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]))

  const place = places.find((p) => p.key === placeKey)

  const submit = async (empty = false) => {
    if (!place) return
    setSaving(true)
    try {
      const result = await createVahtaAssignment({
        year,
        month,
        ...placeRef(place),
        kind: kind || place.defaultKind,
        ...(empty ? { employee_id: null } : { employee_ids: picked }),
      })
      toast.success(empty ? 'Пустой слот добавлен' : `Поставлено: ${result.created}`)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось поставить')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title="Поставить людей на место"
      size="xl"
      actions={
        <div className="flex w-full items-center justify-between">
          <button
            type="button"
            onClick={() => void submit(true)}
            disabled={!place || saving}
            className="cursor-pointer text-xs text-gray-500 hover:text-gray-700 disabled:opacity-50"
          >
            + пустой слот (незанятое место)
          </button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button
              onClick={() => void submit(false)}
              loading={saving}
              disabled={!place || picked.length === 0}
            >
              Поставить{picked.length > 0 ? ` (${picked.length})` : ''}
            </Button>
          </div>
        </div>
      }
    >
      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Место работы</span>
        <select
          value={placeKey}
          onChange={(e) => setPlaceKey(e.target.value)}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        >
          {places.length === 0 && (
            <option value="">Мест нет — заведите объект с постами в настройках</option>
          )}
          {places.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Должность</span>
        <select
          value={kind || place?.defaultKind || 'guard'}
          onChange={(e) => setKind(e.target.value as VahtaKind)}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        >
          {KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-[11px] text-gray-400">
          У каждого своя: на одном посту могут стоять и ГБР, и охранник.
          В строке табеля её можно поменять.
        </span>
      </label>

      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-sm text-gray-600">Кто встаёт</span>
        <span className="text-xs text-gray-400">
          выбрано {picked.length} из {candidates.length}
          {picked.length > 0 && (
            <button
              type="button"
              onClick={() => setPicked([])}
              className="ml-2 cursor-pointer text-blue-700"
            >
              сбросить
            </button>
          )}
        </span>
      </div>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Поиск по ФИО или табельному"
        className="mb-2 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
      />
      <div className="max-h-64 overflow-auto rounded-md border border-gray-200">
        {shown.length === 0 && (
          <p className="px-3 py-4 text-center text-sm text-gray-400">
            {candidates.length === 0
              ? 'В подразделении охраны пока никого — оформите нового'
              : 'Никого не нашлось'}
          </p>
        )}
        {shown.map((c) => (
          <label
            key={c.employee_id}
            className="flex cursor-pointer items-center gap-2 border-b border-gray-50 px-3 py-1.5 text-sm last:border-b-0 hover:bg-gray-50"
          >
            <input
              type="checkbox"
              checked={picked.includes(c.employee_id)}
              onChange={() => toggle(c.employee_id)}
            />
            <span className="flex-1 text-slate-800">{c.full_name}</span>
            {c.tab_number && (
              <span className="font-mono text-[10px] text-gray-400">{c.tab_number}</span>
            )}
            {c.where && <span className="text-[11px] text-gray-400">{c.where}</span>}
          </label>
        ))}
      </div>

      <p className="mt-3 text-xs text-gray-500">
        Каждому выбранному заведётся своя строка{place ? ` на «${place.label}»` : ''},
        все дни месяца отметятся сразу — обычно человек отрабатывает весь срок, а
        исключения снимаются в сетке дней.
      </p>
    </Modal>
  )
}
