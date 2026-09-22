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
import { DsButton } from '../components/ds/Button'
import { EmptyState } from '../components/ds/EmptyState'
import { SearchField, SelectField } from '../components/ds/fields'
import { Icon } from '../components/ds/Icon'
import { MonthPicker } from '../components/ds/MonthPicker'
import { Pill } from '../components/ds/Pill'
import { Modal } from '../components/Modal'
import { QuickHireModal } from '../components/vahta/QuickHireModal'
import { usePersistentState } from '../hooks/usePersistentState'
import { ReplaceModal } from '../components/vahta/ReplaceModal'
import { usePeriodStore } from '../store/period'
import { useAuthStore } from '../store/auth'
import { TIMEKEEPER_NEW_POSITION_HINT } from '../utils/guardStaff'
import { toast } from '../store/toasts'
import type {
  Company,
  VahtaCandidate,
  VahtaCard,
  VahtaCrew,
  GuardJobTitle,
  VahtaMonth,
  VahtaRow,
  VahtaSite,
  VahtaView,
} from '../types/api'
import { formatMoney } from '../utils/money'
import { UI_KEYS } from '../utils/persist'
import { companyLabel } from '../utils/companies'
import { defaultJobTitleId, useGuardJobTitles } from '../hooks/useGuardJobTitles'
import { ConfirmDialog } from '../components/ds/ConfirmDialog'
import { RowMenu, type MenuItem } from '../components/ds/Menu'
import { MONTHS_RU, MONTHS_RU_GEN, MONTHS_RU_PREP } from '../utils/ruDate'
import { companyColorByIndex } from '../utils/colors'
import { vahtaSettingsPath } from '../utils/vahtaSettings'

// Должности — справочник вахты (настройки → «Должности»), а не константа
// экрана: грузятся хуком useGuardJobTitles и передаются строкам и окнам.

const isVahtaView = (v: unknown): boolean => v === 'month' || v === 1 || v === 2


/**
 * Ширины липких левых колонок. Фиксированные, потому что смещение каждой
 * следующей считается от суммы предыдущих — «по содержимому» они разъедутся с
 * шапкой (та же причина, что в основном табеле).
 */
const COL_NAME_W = 230
const COL_ROLE_W = 156
const COL_RATE_W = 104
/**
 * Ширина ячейки дня. 24 px — мишень клика не меньше 24×24 (редизайн §4.3);
 * сама отметка 20×20, месяц по-прежнему целиком на экране.
 */
const DAY_W = 24
const LEFT_ROLE = COL_NAME_W
const LEFT_RATE = COL_NAME_W + COL_ROLE_W

/**
 * «4 чел. · 5 строк» у экипажа и объекта. Считаются ЛЮДИ (уникальные), а строк
 * бывает больше — человек на двух половинах месяца или на двух постах одного
 * объекта. Пустое место — «никого».
 */
function placeCount(card: VahtaCard): string {
  const people = new Set(card.rows.filter((r) => r.employee_id).map((r) => r.employee_id)).size
  const head = people ? `${people} чел.` : 'никого'
  return card.rows.length > people ? `${head} · ${plural(card.rows.length, 'строка', 'строки', 'строк')}` : head
}

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
  rate: string | null
}

function placesOf(sites: VahtaSite[], crews: VahtaCrew[]): Place[] {
  const fromCrews: Place[] = crews.map((c) => ({
    key: `crew-${c.id}`,
    kind: 'crew',
    id: c.id,
    label: c.name,
    rate: c.shift_rate,
  }))
  const fromPosts: Place[] = sites.flatMap((site) =>
    site.posts.map((post) => ({
      key: `post-${post.id}`,
      kind: 'post' as const,
      id: post.id,
      label: `${site.name} · ${post.name}`,
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
  onKind: (row: VahtaRow, jobTitleId: number) => void
  /** Ставка СТРОКИ правится прямо в ячейке (была только во вкладке «Состав»). */
  onRate: (row: VahtaRow, value: string) => Promise<boolean>
  /** Ставка места по умолчанию — чтобы пометить строку со своей ставкой. */
  placeRate: string | null
  /** Пост строки — только у объектов с двумя и больше постами, иначе null. */
  postLabel: string | null
  jobTitles: GuardJobTitle[]
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
    onPaintStart, onPaintOver, onMoney, onReplace, onRemove, onKind, onRate, placeRate,
    postLabel, jobTitles,
  }: PersonRowProps) {
    const [rateDraft, setRateDraft] = useState<string | null>(null)
    const ownRate =
      placeRate !== null && parseFloat(row.rate ?? '0') !== parseFloat(placeRate)
    const saveRate = async () => {
      if (rateDraft === null) return
      const value = rateDraft.trim().replace(',', '.')
      if (value === String(parseFloat(row.rate ?? '0'))) return setRateDraft(null)
      if (!/^\d+(\.\d{1,2})?$/.test(value)) {
        toast.error('Ставка — число, например 4500')
        return
      }
      if (await onRate(row, value)) setRateDraft(null)
    }
    // Меню строки: разрушающее «Снять с поста» — в меню, а не красной кнопкой в
    // каждой из 85 строк (редизайн §4.3). Табельщик снимает с поста тоже —
    // бэк ему это разрешает (решение заказчика).
    const menuItems: MenuItem[] = canEdit
      ? row.employee_id
        ? [
            { label: 'Заменить…', hint: 'кто сменит на посту с выбранного дня', onSelect: () => onReplace(row) },
            { label: 'Снять с поста…', danger: true, onSelect: () => onRemove(row) },
          ]
        : [
            { label: 'Поставить человека…', onSelect: () => onReplace(row) },
            { label: 'Убрать свободное место…', danger: true, onSelect: () => onRemove(row) },
          ]
      : []
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
        className="group hover:bg-ds-surface-2"
      >
        {/* Одна строка с многоточием: перенос ФИО ломал высоту строки и
            разъезжался с сеткой дней. Полное имя — в подсказке. */}
        <td
          className="sticky left-0 z-10 overflow-hidden text-ellipsis whitespace-nowrap border-b border-ds-line bg-ds-surface px-2 py-1 group-hover:bg-ds-surface-2"
          style={{ width: COL_NAME_W, minWidth: COL_NAME_W, maxWidth: COL_NAME_W }}
          title={row.employee_name ?? 'вакансия'}
        >
          {row.employee_id && canOpenCard ? (
            <Link
              to={`/admin/employees?employee_id=${row.employee_id}`}
              title="Открыть карточку сотрудника"
              className="font-medium text-ds-ink hover:text-ds-accent hover:underline"
            >
              {highlight(row.employee_name, query)}
            </Link>
          ) : row.employee_name ? (
            <span className="font-medium text-ds-ink">
              {highlight(row.employee_name, query)}
            </span>
          ) : (
            <span className="italic text-ds-muted">Вакансия — место свободно</span>
          )}
          {row.tab_number && (
            <span className="ml-1.5 font-ds-mono text-[11px] text-ds-muted">
              {highlight(row.tab_number, query)}
            </span>
          )}
        </td>

        {/* Должность — свойство ЧЕЛОВЕКА на месте, а не места: на одном посту
            стоят и ГБР, и охранник. Поэтому правится прямо в строке. */}
        <td
          className="sticky z-10 border-b border-ds-line bg-ds-surface px-1 py-1 text-[12.5px] text-ds-ink-2 group-hover:bg-ds-surface-2"
          style={{ left: LEFT_ROLE, width: COL_ROLE_W, minWidth: COL_ROLE_W, maxWidth: COL_ROLE_W }}
        >
          {/* Видимая подпись «Должность · пост ˅» со стрелкой В КОНЦЕ (как в
              макете), а поверх — прозрачный select: клики и клавиатура уходят в
              него, кольцо фокуса рисуется на подписи. Системная стрелка select
              вставала между должностью и постом. */}
          <div
            className={`relative flex h-[26px] min-w-0 items-center gap-1 rounded-ds-sm border border-transparent px-1.5 ${
              canEdit
                ? 'hover:border-ds-control-line hover:bg-ds-surface has-[select:focus-visible]:outline-2 has-[select:focus-visible]:outline-ds-focus has-[select:focus-visible]:outline-offset-1'
                : ''
            }`}
          >
            <span className="min-w-0 truncate">{row.job_title_name}</span>
            {/* Пост — в той же строке: подписью ниже строка становилась вдвое выше. */}
            {postLabel && (
              <span className="flex-none whitespace-nowrap text-[12px] text-ds-muted" title={`Пост: ${postLabel}`}>
                · {postLabel}
              </span>
            )}
            {canEdit && (
              <>
                <Icon name="chevronDown" size={10} className="ml-auto text-ds-faint" />
                <select
                  value={row.job_title_id}
                  onChange={(e) => onKind(row, Number(e.target.value))}
                  aria-label={`Должность: ${row.employee_name ?? 'свободное место'}`}
                  className="absolute inset-0 h-full w-full cursor-pointer appearance-none opacity-0 focus:outline-none"
                >
                  {/* Снятая должность строки остаётся в списке, иначе select показал бы пустоту. */}
                  {!jobTitles.some((t) => t.id === row.job_title_id) && (
                    <option value={row.job_title_id}>{row.job_title_name}</option>
                  )}
                  {jobTitles.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
        </td>

        <td
          className="sticky z-10 whitespace-nowrap border-b border-r border-b-ds-line border-r-ds-line-strong bg-ds-surface px-1.5 py-1 text-right tabular-nums group-hover:bg-ds-surface-2"
          style={{ left: LEFT_RATE, width: COL_RATE_W, minWidth: COL_RATE_W }}
        >
          {!showMoney ? (
            ''
          ) : rateDraft !== null ? (
            <input
              autoFocus
              value={rateDraft}
              aria-label={`${
                row.pay_type === 'salary' ? 'Оклад за месяц' : 'Ставка за смену'
              }: ${row.employee_name ?? 'вакансия'}`}
              onChange={(e) => setRateDraft(e.target.value)}
              onBlur={() => void saveRate()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveRate()
                if (e.key === 'Escape') setRateDraft(null)
              }}
              className="h-[26px] w-[88px] rounded-ds-sm border border-ds-accent px-1.5 text-right font-ds-mono text-[12.5px] shadow-[0_0_0_3px_color-mix(in_srgb,var(--ds-accent)_16%,transparent)] outline-none"
            />
          ) : canManage ? (
            <button
              type="button"
              onClick={() => setRateDraft(String(parseFloat(row.rate ?? '0')))}
              title={
                // У окладной должности (начальник охраны) в этой же колонке
                // лежит ОКЛАД ЗА МЕСЯЦ, а не цена смены: способ оплаты задаёт
                // должность строки. Другого места ввода нет — в форме
                // сотрудника суммы не бывает (task_guard_form_rate_official).
                (row.pay_type === 'salary'
                  ? 'Оклад за месяц по этой строке. Клик — изменить'
                  : 'Ставка за смену по этой строке. Клик — изменить') +
                (ownRate ? `; у места ${money(placeRate)}` : '')
              }
              className="inline-flex h-[26px] cursor-pointer items-center gap-1 rounded-ds-sm border border-dashed border-transparent px-1.5 tabular-nums hover:border-ds-control-line hover:bg-ds-surface"
            >
              {ownRate && <span className="text-[10.5px] font-medium text-ds-accent">своя</span>}
              {money(row.rate)}
              {row.pay_type === 'salary' && (
                <span className="text-[10.5px] text-ds-muted">/мес</span>
              )}
            </button>
          ) : (
            <>
              {ownRate && <span className="mr-1 text-[10.5px] font-medium text-ds-accent">своя</span>}
              {money(row.rate)}
            </>
          )}
        </td>

        {days.map((day) => (
          <td
            key={day}
            className={`border-b border-ds-line p-0 text-center ${
              day === midDay + 1 && day !== firstDay ? 'border-l-2 border-l-ds-ink-2' : ''
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
              aria-label={`${day} число: ${marked.has(day) ? 'смена' : 'нет смены'}`}
              aria-pressed={marked.has(day)}
              className={`h-5 w-5 rounded-[4px] text-[10.5px] leading-none tabular-nums ${
                marked.has(day)
                  ? 'bg-ds-accent text-ds-on-accent'
                  : 'bg-ds-surface-3 text-ds-muted'
              } ${canEdit ? 'cursor-pointer hover:outline hover:outline-2 hover:outline-ds-focus' : ''}`}
            >
              {day}
            </button>
          </td>
        ))}

        <td className="border-b border-ds-line px-1.5 py-1 text-right font-semibold tabular-nums" style={{ width: 44 }}>
          {row.shifts || '—'}
        </td>

        {showMoney && (
          <>
            <td className="whitespace-nowrap border-b border-ds-line px-2 py-1 text-right font-semibold tabular-nums text-ds-ok">
              {parseFloat(row.accrued ?? '0') ? money(row.accrued) : '—'}
            </td>
            {/* Налог на официальную часть: сверх начисленного, но входит в
                разнесение по юрлицам — отсюда и разница сумм в подвале. */}
            <td
              className="whitespace-nowrap border-b border-ds-line px-2 py-1 text-right tabular-nums text-ds-ink-2"
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
            <td className="whitespace-nowrap border-b border-ds-line px-2 py-1 text-right" style={{ width: 170 }}>
              {/* Премия и штраф вводятся отсюда: пустая ячейка прямо предлагает
                  их завести, заполненная показывает суммы словами. */}
              {canManage ? (
                <button
                  type="button"
                  onClick={(e) =>
                    onMoney(row, (e.currentTarget as HTMLElement).getBoundingClientRect())
                  }
                  className={`inline-flex h-[26px] cursor-pointer items-center rounded-ds-sm border px-2 text-[12px] ${
                    hasAdjustments
                      ? 'border-transparent text-ds-ink-2 hover:border-ds-control-line hover:bg-ds-surface'
                      : 'border-dashed border-ds-control-line text-ds-muted hover:bg-ds-surface'
                  }`}
                >
                  {hasAdjustments ? (
                    <>
                      {parseFloat(row.premium ?? '0') > 0 && (
                        <span className="text-ds-ok">+{money(row.premium)}&nbsp;</span>
                      )}
                      {parseFloat(row.penalty ?? '0') > 0 && (
                        <span className="text-ds-danger">−{money(row.penalty)}&nbsp;</span>
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
                <span className="text-[12px] text-ds-muted">
                  {hasAdjustments ? money(row.accrued) : 'нет'}
                </span>
              )}
            </td>
            {/* К выплате = начислено − оф. выплата, вверх до 500 ₽ по половинам. */}
            <td
              className="whitespace-nowrap border-b border-ds-line px-2 py-1 text-right font-semibold tabular-nums text-ds-ink"
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

        <td className="whitespace-nowrap border-b border-ds-line px-1 py-1 text-center" style={{ width: 44 }}>
          <RowMenu items={menuItems} label={`Действия: ${row.employee_name ?? 'свободное место'}`} />
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
    a.row.job_title_id === b.row.job_title_id &&
    a.row.job_title_name === b.row.job_title_name &&
    a.jobTitles === b.jobTitles &&
    a.row.employee_name === b.row.employee_name &&
    a.placeRate === b.placeRate &&
    a.postLabel === b.postLabel &&
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
  // Ставка места по умолчанию (пометка «своя» у строки) и пост в строке — только
  // у объектов, где постов больше одного: иначе пост совпадает с объектом.
  const postInfo = useMemo(() => {
    const map = new Map<number, { rate: string | null; multi: boolean }>()
    for (const site of sites)
      for (const post of site.posts)
        map.set(post.id, { rate: post.effective_rate ?? null, multi: site.posts.length > 1 })
    return map
  }, [sites])
  const crewRate = useMemo(
    () => new Map(crews.map((c) => [c.id, c.shift_rate ?? null] as const)),
    [crews],
  )
  const showMoney = Boolean(data?.can_see_money)
  const canEdit = Boolean(data?.can_edit)
  const jobTitles = useGuardJobTitles()
  // Месяц на проверке у бухгалтера или закрыт: бэк отклоняет любую правку
  // назначений (409), поэтому и кнопки «поставить / заменить / убрать», и правка
  // сумм гаснут вместе с днями.
  const periodLock = data?.period_lock ?? null
  const canManage = roleCanManage && !periodLock
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
        zones.flatMap((z) => z.cards.flatMap((c) => c.rows.map((r) => r.job_title_name))),
      ),
    ],
    [zones],
  )

  /** Отбор строк: поиск идёт и по человеку, и по месту работы. */
  const matches = useCallback(
    (row: VahtaRow, card: VahtaCard) => {
      if (kindFilter && row.job_title_name !== kindFilter) return false
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

  // Снятие с поста — через подтверждение, которое называет последствия
  // (ux-copy): человек, место, месяц, сколько смен удалится; что он остаётся в штате.
  const [removing, setRemoving] = useState<VahtaRow | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const handleRemove = useCallback((row: VahtaRow) => setRemoving(row), [])
  const confirmRemove = async () => {
    if (!removing) return
    setRemoveBusy(true)
    try {
      await deleteVahtaAssignment(removing.id)
      toast.success(
        removing.employee_name
          ? `${removing.employee_name} снят с поста «${removing.post_name}»`
          : 'Свободное место убрано',
      )
      setRemoving(null)
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось снять с поста')
    } finally {
      setRemoveBusy(false)
    }
  }

  const onRate = useCallback(
    async (row: VahtaRow, value: string) => {
      try {
        await updateVahtaAssignment(row.id, { rate: value })
        await reload()
        return true
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
        return false
      }
    },
    [reload],
  )

  const onMoney = useCallback((row: VahtaRow, anchor: DOMRect) => {
    setMoneyRow({ row, anchor })
  }, [])
  const onReplace = useCallback((row: VahtaRow) => setReplaceRow(row), [])

  const onKind = useCallback(
    async (row: VahtaRow, jobTitleId: number) => {
      try {
        await updateVahtaAssignment(row.id, { job_title_id: jobTitleId })
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
  const companyColor = useCallback(
    (id: number) => companyColorByIndex(companies.findIndex((c) => c.id === id)).color,
    [companies],
  )

  if (loading) return <p className="ds text-[13px] text-ds-muted">Загрузка…</p>

  if (!data || (zones.length === 0 && data.departments.length === 0)) {
    return (
      <div className="ds rounded-ds-md border border-ds-warn-line bg-ds-warn-soft p-4 text-[13px] text-ds-warn">
        Подразделений охраны не найдено. Отметьте отдел как «подразделение охраны» в{' '}
        <Link to="/admin/org" className="underline">
          оргструктуре
        </Link>
        , затем заведите зоны обслуживания, объекты с постами и экипажи ГБР в{' '}
        <Link to={vahtaSettingsPath('zones')} className="underline">
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
  const halfLabel =
    data.view_half === null
      ? ''
      : `${data.view_half === 1 ? 'Первая' : 'Вторая'} половина: ` +
        `${data.first_day}–${data.last_day} ${MONTHS_RU_GEN[month - 1]}`
  const viewOptions: { value: VahtaView; label: string }[] = [
    { value: 'month', label: 'Месяц' },
    { value: 1, label: `1–${mid}` },
    { value: 2, label: `${mid + 1}–${data.days_in_month}` },
  ]

  // Период — заголовок, а не плашка (редизайн, артборд 02): статус рядом с
  // месяцем, а что из него следует — строкой под заголовком.
  const lockPill =
    periodLock === 'closed'
      ? { tone: 'ok' as const, label: 'Закрыт' }
      : periodLock === 'pending_review'
        ? { tone: 'warn' as const, label: 'На проверке у бухгалтера' }
        : { tone: 'neutral' as const, label: 'Черновик' }

  return (
    <div className="ds -m-6 flex h-[calc(100vh-3.5rem)] flex-col bg-ds-ground px-6 pb-4 pt-5 text-ds-ink">
      {/* ── Шапка, строка 1: период одним блоком и действия над ним ──────────
          Блок периода на этапе 5 редизайна уедет в глобальную строку целиком. */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <h1 className="m-0 text-[23px] font-semibold tracking-[-0.025em]">Вахта</h1>
        <div className="flex flex-wrap items-center gap-2.5">
          <MonthPicker year={year} month={month} onChange={setPeriod} />
          {/* Режим отображения: месяц — общая картина, половины — сверка выплат. */}
          <div
            role="group"
            aria-label="Отрезок месяца"
            className="flex h-8 overflow-hidden rounded-ds-md border border-ds-control-line bg-ds-surface text-[13px]"
          >
            {viewOptions.map((opt) => (
              <button
                key={String(opt.value)}
                type="button"
                aria-pressed={view === opt.value}
                onClick={() => setView(opt.value)}
                className={`cursor-pointer border-l border-ds-line px-3 first:border-l-0 ${
                  view === opt.value
                    ? 'bg-ds-ink-2 font-medium text-white'
                    : 'text-ds-ink-2 hover:bg-ds-surface-3'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <Pill tone={lockPill.tone} dot>
            {lockPill.label}
          </Pill>
          {data.view_half !== null && (
            <Pill tone="accent">{halfLabel}: итоги только за неё</Pill>
          )}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {canManage && (
            <DsButton icon="copy" onClick={handleCopy}>
              Скопировать прошлый период
            </DsButton>
          )}
          {showMoney && (
            <DsButton icon="download" onClick={handleExport}>
              Выгрузить в Excel
            </DsButton>
          )}
          {canManage && (
            <Link
              to={vahtaSettingsPath('staff', { year, month })}
              className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-ds-md border border-ds-line bg-ds-surface-3 px-3.5 text-[13px] font-medium text-ds-ink-2 transition-colors hover:bg-ds-line hover:text-ds-ink"
            >
              <Icon name="settings" />
              Настройки
            </Link>
          )}
        </div>
      </div>

      {periodLock && (
        <p className="m-0 mt-1.5 max-w-[80ch] text-[13px] text-ds-muted">
          <b className="font-medium text-ds-warn">
            {periodLock === 'closed' ? 'Период закрыт.' : 'Период на проверке у бухгалтера.'}
          </b>{' '}
          Назначения, смены и суммы вахты за {MONTHS_RU[month - 1].toLowerCase()}{' '}
          не меняются — чтобы внести правку, период нужно вернуть в черновик.
        </p>
      )}

      {/* ── Шапка, строка 2: только сужение выдачи и счётчик ──────────────── */}
      <div className="mb-3 mt-3.5 flex flex-wrap items-center gap-2">
        <SearchField
          aria-label="Поиск по табелю вахты"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Человек, таб. №, пост или объект"
          className="w-[280px]"
        />
        <SelectField
          aria-label="Зона"
          value={zoneFilter}
          onChange={(e) => setZoneFilter(e.target.value)}
          className="w-[150px]"
        >
          <option value="">Все зоны</option>
          {zones.map((z) => (
            <option key={z.zone_id} value={z.zone_name}>
              {z.zone_name}
            </option>
          ))}
        </SelectField>
        <SelectField
          aria-label="Должность"
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value)}
          className="w-[170px]"
        >
          <option value="">Все должности</option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </SelectField>
        {filtering && (
          <DsButton
            variant="ghost"
            onClick={() => {
              setQuery('')
              setZoneFilter('')
              setKindFilter('')
            }}
          >
            Сбросить
          </DsButton>
        )}
        <div className="ml-auto flex items-center gap-3.5 text-[12.5px] text-ds-muted">
          {canEdit && <span>Клик по дню — смена, протяжка — несколько дней</span>}
          <span>
            <b className="font-medium text-ds-ink-2">
              {plural(shownPeople, 'человек', 'человека', 'человек')}
            </b>{' '}
            · {plural(shownRows.length, 'строка', 'строки', 'строк')}
          </span>
        </div>
      </div>

      {/* ── Таблица ───────────────────────────────────────────────────────── */}
      <div className="min-h-0 flex-1 overflow-auto rounded-ds-lg border border-ds-line bg-ds-surface shadow-ds">
        <table className="w-max min-w-full border-separate border-spacing-0 text-[12.5px]">
          <thead>
            <tr>
              <th
                className="sticky left-0 top-0 z-30 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-left"
                style={{ width: COL_NAME_W, minWidth: COL_NAME_W }}
              >
                Сотрудник
              </th>
              <th
                className="sticky top-0 z-30 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-left"
                style={{ left: LEFT_ROLE, width: COL_ROLE_W, minWidth: COL_ROLE_W }}
              >
                Должность
              </th>
              <th
                className="sticky top-0 z-30 border-r border-r-ds-line-strong border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-right"
                style={{ left: LEFT_RATE, width: COL_RATE_W, minWidth: COL_RATE_W }}
              >
                {showMoney ? 'Ставка / оклад' : ''}
              </th>
              {days.map((day) => (
                <th
                  key={day}
                  className={`sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted text-center ${
                    day === mid + 1 && day !== data.first_day
                      ? 'border-l-2 border-l-ds-ink-2'
                      : ''
                  }`}
                  style={{ width: DAY_W, minWidth: DAY_W }}
                >
                  {day}
                </th>
              ))}
              <th className="sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-right">
                Смен
              </th>
              {showMoney && (
                <>
                  <th className="sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-right">
                    Начислено
                  </th>
                  <th
                    className="sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-right"
                    title="Налог на официальную выплату. Входит в разнесение по юрлицам сверх начисленного."
                  >
                    Налог{data.employer_tax_percent ? ` ${Number(data.employer_tax_percent)} %` : ''}
                  </th>
                  <th className="sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-right">
                    Премия и штраф
                  </th>
                  <th className="sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted px-2 text-right">
                    К выплате
                  </th>
                </>
              )}
              <th className="sticky top-0 z-20 border-b border-ds-line-strong bg-ds-surface-2 py-[7px] text-[11px] font-semibold text-ds-muted">
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>

          <tbody>
            {shownZones.map(({ zone, cards }) => {
              const zoneRows = cards.flatMap((c) => c.rows)
              const crewCount = cards.filter((c) => c.card.kind === 'crew').length
              const siteCount = cards.filter((c) => c.card.kind === 'site').length
              // ФИО, должность, ставка · дни · смен · 4 денежные · меню «⋯».
              // Было 4 + дни: колонку «Смен» не считали, и строки зоны и места
              // обрывались на одну колонку раньше таблицы.
              const totalCols = 5 + days.length + (showMoney ? 4 : 0)
              return (
                <ZoneGroup key={zone.zone_id}>
                  {/* Спина зоны: верхний уровень группировки, без карточек. */}
                  <tr>
                    <td
                      className="sticky left-0 z-10 border-b-2 border-ds-ink-2 bg-ds-surface px-2 pb-1.5 pt-4"
                      style={{ width: COL_NAME_W }}
                    >
                      <span className="text-[15px] font-semibold tracking-tight">
                        {zone.zone_name}
                      </span>
                    </td>
                    <td
                      className="sticky z-10 border-b-2 border-ds-ink-2 bg-ds-surface"
                      style={{ left: LEFT_ROLE }}
                    />
                    <td
                      className="sticky z-10 border-r border-r-ds-line-strong border-b-2 border-ds-ink-2 bg-ds-surface"
                      style={{ left: LEFT_RATE }}
                    />
                    <td
                      className="border-b-2 border-ds-ink-2 bg-ds-surface px-2 pb-1.5 pt-4"
                      colSpan={totalCols - 3}
                    >
                      <span className="text-[12px] text-ds-muted">
                        {filtering
                          ? `${plural(zoneRows.length, 'строка', 'строки', 'строк')} найдено`
                          : [
                              plural(crewCount, 'экипаж', 'экипажа', 'экипажей'),
                              plural(siteCount, 'объект', 'объекта', 'объектов'),
                              plural(zone.total_shifts, 'смена', 'смены', 'смен'),
                            ].join(', ')}
                      </span>
                      {showMoney && (
                        <span className="float-right font-semibold tabular-nums">
                          {money(zone.total_accrued)}
                        </span>
                      )}
                    </td>
                  </tr>

                  {cards.map(({ card, rows }) => (
                    <ZoneGroup key={`${card.kind}-${card.id}`}>
                      {/* Подстрока места работы: экипаж ГБР или объект. */}
                      <tr>
                        {/* Название места — в одну строку: «SH - RIVER SH -
                            FOREST» переносилось и ломало высоту подстроки. */}
                        <td
                          className="sticky left-0 z-10 overflow-hidden text-ellipsis whitespace-nowrap border-b border-ds-line bg-ds-surface-2 px-2 py-1"
                          style={{ width: COL_NAME_W, minWidth: COL_NAME_W, maxWidth: COL_NAME_W }}
                          title={card.name}
                        >
                          <span
                            className={`mr-1.5 rounded border px-1.5 py-px text-[10.5px] font-semibold ${
                              card.kind === 'crew'
                                ? 'border-ds-warn-line bg-ds-warn-soft text-ds-warn'
                                : 'border-ds-line bg-ds-surface-3 text-ds-ink-2'
                            }`}
                          >
                            {card.kind === 'crew' ? 'Экипаж ГБР' : 'Объект'}
                          </span>
                          <span className="font-semibold">
                            {highlight(card.name, query)}
                          </span>
                        </td>
                        {/* Сколько ЛЮДЕЙ на месте и сколько строк: у человека на
                            двух половинах месяца строк две, а человек один. */}
                        <td
                          className="sticky z-10 whitespace-nowrap border-b border-ds-line bg-ds-surface-2 px-2 text-[12px] text-ds-muted"
                          style={{ left: LEFT_ROLE }}
                        >
                          {placeCount(card)}
                        </td>
                        <td
                          className="sticky z-10 border-r border-r-ds-line-strong border-b border-ds-line bg-ds-surface-2"
                          style={{ left: LEFT_RATE }}
                        />
                        <td
                          className="border-b border-ds-line bg-ds-surface-2 px-2 py-1"
                          colSpan={totalCols - 3}
                        >
                          <span className="text-[12px] text-ds-muted">
                            {card.objects.length > 0 &&
                              (card.kind === 'crew'
                                ? `выезжает на ${card.objects.join(', ')}`
                                : `посты: ${card.objects.join(', ')}`)}
                          </span>
                          {canManage && (
                            <span className="float-right flex gap-1">
                              <button
                                type="button"
                                onClick={() => setAddTo(card)}
                                className="h-7 cursor-pointer rounded-ds-sm px-2 text-[12.5px] font-medium text-ds-accent hover:bg-ds-accent-soft hover:text-ds-accent-hi"
                              >
                                + Поставить
                              </button>
                              <button
                                type="button"
                                onClick={() => setHireAt(card)}
                                className="h-7 cursor-pointer rounded-ds-sm px-2 text-[12.5px] font-medium text-ds-accent hover:bg-ds-accent-soft hover:text-ds-accent-hi"
                              >
                                Оформить нового
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
                          onRate={onRate}
                          placeRate={
                            row.crew_id !== null
                              ? crewRate.get(row.crew_id) ?? null
                              : row.post_id !== null
                                ? postInfo.get(row.post_id)?.rate ?? null
                                : null
                          }
                          postLabel={
                            row.post_id !== null && postInfo.get(row.post_id)?.multi
                              ? row.post_name
                              : null
                          }
                          jobTitles={jobTitles}
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
          <div className="p-6">
            {filtering ? (
              <EmptyState title="Никого не нашлось" compact>
                Сбросьте фильтры или измените запрос.
              </EmptyState>
            ) : (
              <EmptyState title={`За ${MONTHS_RU[month - 1].toLowerCase()} состав не заведён`} compact>
                Поставьте людей на посты в строках мест или скопируйте прошлый период.
              </EmptyState>
            )}
          </div>
        )}
      </div>

      {/* ── Подвал: итоги карточкой ─────────────────────────────────────────── */}
      <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-2 rounded-ds-lg border border-ds-line bg-ds-surface px-4 py-3 text-[12.5px] text-ds-muted">
        <span>
          Смен<b className="ml-1.5 text-[15px] font-semibold tabular-nums text-ds-ink">{data.total_shifts}</b>
        </span>
        {showMoney && (
          <>
            <span>
              Начислено
              <b className="ml-1.5 text-[15px] font-semibold tabular-nums text-ds-ink">{money(data.total_accrued)}</b>
            </span>
            <span>
              К выплате
              <b className="ml-1.5 text-[15px] font-semibold tabular-nums text-ds-ink">{money(data.total_net_payout)}</b>
            </span>
            {/* Налоги объясняют, почему разнесение больше начисленного. */}
            <span title="Налог на официальную часть выплаты. Затрата компании: входит в разнесение по юрлицам, но не в начислено и не в выплату.">
              Налог{data.employer_tax_percent ? ` ${Number(data.employer_tax_percent)} % от оф. выплаты` : ''}
              <b className="ml-1.5 text-[15px] font-semibold tabular-nums text-ds-ink">{money(data.total_tax)}</b>
            </span>
          </>
        )}
        {data.view_half === null && data.halves.map((half) => (
          <span key={half.half}>
            {half.half === 1 ? `1–${mid}` : `${mid + 1}–${data.days_in_month}`}
            <b className="ml-1.5 text-[15px] font-semibold tabular-nums text-ds-ink">
              {showMoney ? money(half.accrued) : plural(half.shifts, 'смена', 'смены', 'смен')}
            </b>
          </span>
        ))}
        {showMoney && data.company_totals.length > 0 && (
          <div className="flex basis-full flex-wrap items-center gap-2">
            <span>
              Разнесено по юрлицам {money(data.total_distribution)}
              {/* Равенство пишем, только когда оно верно: строка без процентов
                  места работы в разнесение не попадает вовсе. */}
              {parseFloat(data.total_tax ?? '0') > 0 &&
                Math.abs(
                  parseFloat(data.total_distribution ?? '0') -
                    parseFloat(data.total_distribution_base ?? '0'),
                ) < 0.005 &&
                ` = начислено ${money(data.total_accrued)} + налог ${money(data.total_tax)}`}
              :
            </span>
            {data.company_totals.map((t) => (
              <span
                key={t.company_id}
                className="inline-flex items-center gap-1.5 rounded-full border border-ds-line bg-ds-surface-2 px-2.5 py-0.5 text-[12px] text-ds-ink-2"
              >
                {/* Цвет юрлица — общая палитра приложения, как в табеле и дашборде. */}
                <i
                  className="h-2 w-2 flex-none rounded-[2px]"
                  style={{ background: companyColor(t.company_id) }}
                  aria-hidden="true"
                />
                {companyName(t.company_id)}
                <span className="tabular-nums">{money(t.amount)}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ── Окна ──────────────────────────────────────────────────────────── */}
      {removing && (
        <ConfirmDialog
          title={removing.employee_name ? 'Снять с поста?' : 'Убрать свободное место?'}
          confirmLabel={removing.employee_name ? 'Снять с поста' : 'Убрать место'}
          cancelLabel="Оставить"
          danger
          busy={removeBusy}
          onConfirm={() => void confirmRemove()}
          onCancel={() => setRemoving(null)}
        >
          {removing.employee_name ? (
            <>
              Снять {removing.employee_name} с поста «{removing.post_name}» в{' '}
              {MONTHS_RU_PREP[month - 1]}?{' '}
              {removing.days.length > 0
                ? `Отмеченные смены (${removing.days.length}) удалятся из табеля.`
                : 'Отмеченных смен нет.'}{' '}
              Сотрудник остаётся в штате охраны.
            </>
          ) : (
            <>
              Убрать свободное место «{removing.job_title_name}» на «{removing.post_name}» в{' '}
              {MONTHS_RU_PREP[month - 1]}? В других месяцах оно не пропадёт.
            </>
          )}
        </ConfirmDialog>
      )}

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
          jobTitles={jobTitles}
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
            }
          : {}),
        ...(shownHalves.includes(2)
          ? {
              premium_h2: form.premium_h2 || '0',
              penalty_h2: form.penalty_h2 || '0',
            }
          : {}),
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
              <th />
            </tr>
          </thead>
          <tbody>
            {shownHalves.includes(1) && (
            <tr>
              <td className="pr-2 text-right text-[11.5px] text-slate-500">1–{midDay}</td>
              <td className="p-0.5">{field('premium_h1')}</td>
              <td className="p-0.5">{field('penalty_h1')}</td>
              <td className="p-0.5 pl-2 text-right tabular-nums text-slate-600">
                {money(half(1)?.official_payout)}
              </td>
              <td />
            </tr>
            )}
            {shownHalves.includes(2) && (
            <tr>
              <td className="pr-2 text-right text-[11.5px] text-slate-500">
                {midDay + 1}–{daysInMonth}
              </td>
              <td className="p-0.5">{field('premium_h2')}</td>
              <td className="p-0.5">{field('penalty_h2')}</td>
              <td className="p-0.5 pl-2 text-right tabular-nums text-slate-600">
                {money(half(2)?.official_payout)}
              </td>
              <td />
            </tr>
            )}
          </tbody>
        </table>

        <p className="mt-1.5 mb-0 text-[11px] leading-snug text-slate-500">
          Оф. выплата — <b>из формы сотрудника</b>: половина официальной зарплаты на
          каждую половину месяца, пропорционально дням на месте. Руками не вводится.
          {row.is_official
            ? ' Это рабочее место оформлено официально.'
            : ' Это рабочее место оформлено неофициально — выплаты нет.'}
        </p>

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
  jobTitles,
  onClose,
  onDone,
}: {
  year: number
  month: number
  places: Place[]
  jobTitles: GuardJobTitle[]
  onClose: () => void
  onDone: () => void
}) {
  const [placeKey, setPlaceKey] = useState<string>(places[0]?.key ?? '')
  const [jobTitleId, setJobTitleId] = useState<number | ''>('')
  const [candidates, setCandidates] = useState<VahtaCandidate[]>([])
  const [picked, setPicked] = useState<number[]>([])
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const isTimekeeper = useAuthStore((s) => s.user?.role) === 'timekeeper'

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
        job_title_id: jobTitleId || defaultJobTitleId(jobTitles, place.kind) || null,
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
          value={jobTitleId || (place ? defaultJobTitleId(jobTitles, place.kind) : '') || ''}
          onChange={(e) => setJobTitleId(Number(e.target.value))}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        >
          {jobTitles.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
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

      {isTimekeeper && (
        <p className="mt-2 text-xs text-amber-700">{TIMEKEEPER_NEW_POSITION_HINT}</p>
      )}
      <p className="mt-3 text-xs text-gray-500">
        Каждому выбранному заведётся своя строка{place ? ` на «${place.label}»` : ''},
        все дни месяца отметятся сразу — обычно человек отрабатывает весь срок, а
        исключения снимаются в сетке дней.
      </p>
    </Modal>
  )
}
