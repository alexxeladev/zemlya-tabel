import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { listCompanies } from '../../api/companies'
import {
  createVahtaCrew,
  getVahtaMonth,
  createVahtaPost,
  createVahtaSite,
  createVahtaZone,
  deleteVahtaCrew,
  deleteVahtaPost,
  deleteVahtaSite,
  deleteVahtaZone,
  getVahtaDepartments,
  getVahtaSettings,
  listVahtaCrews,
  listVahtaSites,
  listVahtaZones,
  updateVahtaCrew,
  updateVahtaPost,
  updateVahtaSettings,
  updateVahtaSite,
  updateVahtaZone,
} from '../../api/vahta'
import type { VahtaShare } from '../../types/api'
import { Button } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { CrewRoster } from '../../components/vahta/CrewRoster'
import { usePeriodStore } from '../../store/period'
import { useAuthStore } from '../../store/auth'
import { toast } from '../../store/toasts'
import type {
  Company,
  VahtaCrew,
  VahtaDepartment,
  VahtaPost,
  VahtaMonth,
  VahtaSite,
  VahtaZone,
} from '../../types/api'
import { companyLabel } from '../../utils/companies'
import { companyColorByIndex } from '../../utils/colors'
import { formatMoney } from '../../utils/money'

const MONTH_GENITIVE = [
  'январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
  'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре',
]

/** Подсветка найденного — иначе в трёх десятках строк совпадение не заметить. */
function highlight(text: string, query: string) {
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
 * Юрлицо места.
 *
 * У 25 объектов из 26 расклад — «100 % на одно юрлицо», и «100 %» там не
 * информация, а шум: показываем одно название с цветной засечкой. Делённый
 * расклад — исключение, его показываем цифрами и выделяем.
 */
function Companies({
  shares,
  companies,
}: {
  shares: VahtaShare[]
  companies: Company[]
}) {
  if (shares.length === 0)
    return <span className="text-slate-400">не задано</span>

  const colorOf = (id: number) => {
    const idx = companies.findIndex((c) => c.id === id)
    return companyColorByIndex(idx < 0 ? 0 : idx).color
  }

  if (shares.length === 1) {
    const only = shares[0]
    return (
      <span className="inline-flex items-center gap-2">
        <span
          className="inline-block h-3.5 w-[3px] rounded-sm"
          style={{ background: colorOf(only.company_id) }}
        />
        {only.company_display_name ?? only.company_name}
      </span>
    )
  }
  return (
    <span className="whitespace-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-900">
      {shares
        .map((s) => `${s.company_display_name ?? s.company_name} ${parseFloat(s.percent)}`)
        .join(' · ')}
    </span>
  )
}

/** Сколько человек стоит на месте в этом месяце. Ноль — это дыра. */
function People({ n }: { n: number }) {
  return n > 0 ? (
    <span>{n}</span>
  ) : (
    <span className="font-semibold text-red-700" title="Место заведено, а людей нет">
      0
    </span>
  )
}

/** Ставка правится на месте: это самая частая правка в справочнике. */
function RateCell({
  value,
  editable,
  onSave,
}: {
  value: string | null
  editable: boolean
  onSave: (value: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  if (!editable) return <>{formatMoney(value, { showZero: true })}</>
  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          setEditing(false)
          if (draft && draft !== String(parseFloat(value ?? '0'))) onSave(draft)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') {
            setDraft('')
            setEditing(false)
          }
        }}
        className="w-[92px] rounded border border-sky-400 px-1.5 py-0.5 text-right tabular-nums"
      />
    )
  }
  return (
    <button
      type="button"
      onClick={() => {
        setDraft(String(parseFloat(value ?? '0')))
        setEditing(true)
      }}
      className="cursor-pointer rounded border border-transparent px-1.5 py-0.5 tabular-nums hover:border-slate-300 hover:bg-white"
    >
      {formatMoney(value, { showZero: true })}
    </button>
  )
}

/**
 * Ставка налога на официальную часть выплаты (task_vahta_taxes).
 *
 * Одна на все месяцы: правка пересчитывает разнесение и прошлых месяцев —
 * об этом сказано прямо у поля, чтобы её не меняли «на новый месяц».
 */
function TaxRateSetting({ editable }: { editable: boolean }) {
  const [percent, setPercent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getVahtaSettings()
      .then((data) => {
        setPercent(data.employer_tax_percent)
        setDraft(String(parseFloat(data.employer_tax_percent)))
      })
      .catch(() => setPercent(null))
  }, [])

  if (percent === null) return null
  const current = String(parseFloat(percent))
  const dirty = draft.trim() !== '' && draft.replace(',', '.') !== current

  const save = async () => {
    const value = draft.trim().replace(',', '.')
    const n = Number(value)
    if (!Number.isFinite(n) || n < 0 || n > 100) {
      toast.error('Ставка — число от 0 до 100 %')
      return
    }
    if (
      !window.confirm(
        `Сменить ставку налога с ${current} % на ${n} %? Разнесение по юрлицам ` +
          'пересчитается во всех месяцах, включая прошлые.',
      )
    )
      return
    setSaving(true)
    try {
      const data = await updateVahtaSettings({ employer_tax_percent: value })
      setPercent(data.employer_tax_percent)
      setDraft(String(parseFloat(data.employer_tax_percent)))
      toast.success('Ставка налога сохранена')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mb-4 flex max-w-4xl flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm">
      <span className="font-medium text-slate-800">Налог на официальную выплату</span>
      {editable ? (
        <span className="inline-flex items-center gap-1">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && dirty) void save()
            }}
            inputMode="decimal"
            aria-label="Ставка налога, %"
            className="w-[64px] rounded border border-slate-300 px-1.5 py-0.5 text-right tabular-nums"
          />
          <span className="text-slate-600">%</span>
          {dirty && (
            <Button size="sm" onClick={() => void save()} loading={saving}>
              Сохранить
            </Button>
          )}
        </span>
      ) : (
        <b className="tabular-nums">{current} %</b>
      )}
      <span className="basis-full text-xs text-slate-500">
        Налог = официальная выплата × ставка. Он добавляется к «итого начислено» в
        базе разнесения по юрлицам: сумма разнесения больше начисленного ровно на
        налог. Неофициальная часть налогом не облагается. Ставка одна на все
        месяцы — её правка пересчитывает и прошлые.
      </span>
    </div>
  )
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
 * Настройки вахты так, как их заводит руководитель охраны:
 *
 *   ЗОНА ОБСЛУЖИВАНИЯ
 *     ├─ ЭКИПАЖ ГБР зоны (своя ставка и своё распределение)
 *     └─ ОБЪЕКТ (ставка по умолчанию + распределение по юрлицам)
 *          └─ ПОСТЫ (должность и, если надо, своя ставка)
 *
 * Проценты по юрлицам задаются у ОБЪЕКТА и у ЭКИПАЖА, но НЕ у поста: внутри
 * объекта расклад один на всех, а два разных расклада на одном месте означают
 * два разных объекта.
 */
export function VahtaSettingsPage() {
  const [departments, setDepartments] = useState<VahtaDepartment[]>([])
  const [zones, setZones] = useState<VahtaZone[]>([])
  const [sites, setSites] = useState<VahtaSite[]>([])
  const [crews, setCrews] = useState<VahtaCrew[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [tab, setTab] = useState<'zones' | 'roster'>('zones')
  const [month, setMonth] = useState<VahtaMonth | null>(null)
  const [query, setQuery] = useState('')
  const [editZone, setEditZone] = useState<VahtaZone | 'new' | null>(null)
  const [editSite, setEditSite] = useState<{ zone: VahtaZone; site: VahtaSite | null } | null>(null)
  const [editCrew, setEditCrew] = useState<{ zone: VahtaZone; crew: VahtaCrew | null } | null>(null)
  const [editPost, setEditPost] = useState<{ site: VahtaSite; post: VahtaPost | null } | null>(null)
  const [loading, setLoading] = useState(true)
  const role = useAuthStore((s) => s.user?.role)
  const canManage = role === 'admin' || role === 'manager'
  const period = usePeriodStore()

  const reload = useCallback(async () => {
    try {
      const [deps, zoneList, siteList, crewList, companyList, monthData] =
        await Promise.all([
          getVahtaDepartments(),
          listVahtaZones(),
          listVahtaSites(),
          listVahtaCrews(),
          listCompanies(),
          // Колонка «людей» отвечает на вопрос «где дыра»: место заведено, а
          // никто не стоит. Ради неё справочник и грузит месяц.
          getVahtaMonth(period.year, period.month).catch(() => null),
        ])
      setDepartments(deps)
      setZones(zoneList)
      setSites(siteList)
      setCrews(crewList)
      setCompanies(companyList)
      setMonth(monthData)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось загрузить настройки')
    } finally {
      setLoading(false)
    }
  }, [period.year, period.month])

  useEffect(() => {
    void reload()
  }, [reload])

  const peopleOnSite = useCallback(
    (siteId: number) => {
      for (const z of month?.zones ?? [])
        for (const c of z.cards)
          if (c.kind === 'site' && c.id === siteId)
            return c.rows.filter((r) => r.employee_id).length
      return 0
    },
    [month],
  )
  const peopleOnCrew = useCallback(
    (crewId: number) => {
      for (const z of month?.zones ?? [])
        for (const c of z.cards)
          if (c.kind === 'crew' && c.id === crewId)
            return c.rows.filter((r) => r.employee_id).length
      return 0
    },
    [month],
  )

  const hit = useCallback(
    (text: string) => !query || text.toLowerCase().includes(query.toLowerCase()),
    [query],
  )

  const shownZones = useMemo(
    () =>
      zones
        .map((zone) => {
          const zoneHit = hit(zone.name)
          const zoneCrews = crews.filter(
            (c) => c.zone_id === zone.id && (zoneHit || hit(c.name)),
          )
          const zoneSites = sites.filter(
            (s) =>
              s.zone_id === zone.id &&
              (zoneHit || hit(s.name) || s.posts.some((p) => hit(p.name))),
          )
          return { zone, zoneCrews, zoneSites }
        })
        .filter((z) => z.zoneCrews.length > 0 || z.zoneSites.length > 0),
    [zones, crews, sites, hit],
  )

  const saveSiteRate = useCallback(
    async (site: VahtaSite, value: string) => {
      try {
        await updateVahtaSite(site.id, { shift_rate: value })
        await reload()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
      }
    },
    [reload],
  )
  const saveCrewRate = useCallback(
    async (crew: VahtaCrew, value: string) => {
      try {
        await updateVahtaCrew(crew.id, { shift_rate: value })
        await reload()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
      }
    },
    [reload],
  )
  const savePostRate = useCallback(
    async (post: VahtaPost, value: string) => {
      try {
        await updateVahtaPost(post.id, { shift_rate: value })
        await reload()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
      }
    },
    [reload],
  )

  if (loading) return <p className="text-sm text-gray-500">Загрузка…</p>

  if (departments.length === 0) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        Нет подразделений охраны. Отметьте нужный отдел галочкой «подразделение
        охраны» в{' '}
        <Link to="/admin/org" className="underline">
          оргструктуре
        </Link>
        .
      </div>
    )
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800">Настройки вахты</h1>
          <p className="mt-1 text-sm text-slate-500">
            Зоны обслуживания, объекты с постами, выездные экипажи ГБР и налог на
            официальную выплату
          </p>
        </div>
        <Link
          to="/vahta"
          className="rounded-md bg-gray-100 px-3 py-1.5 text-sm text-gray-800 hover:bg-gray-200"
        >
          ← К табелю
        </Link>
      </div>

      <TaxRateSetting editable={canManage} />

      <div className="mb-4 flex gap-1 border-b border-gray-200">
        {(
          [
            ['zones', 'Зоны, объекты и посты'],
            ['roster', 'Состав'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`cursor-pointer px-4 py-2 text-sm ${
              tab === key
                ? 'border-b-2 border-blue-600 font-medium text-blue-700'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'zones' && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Найти зону, объект или пост"
              className="w-[300px] rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="cursor-pointer rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm hover:bg-slate-100"
              >
                Сбросить
              </button>
            )}
            {canManage && (
              <Button size="sm" onClick={() => setEditZone('new')}>
                Добавить зону
              </Button>
            )}
            <span className="ml-auto text-xs text-slate-500">
              {plural(zones.length, 'зона', 'зоны', 'зон')},{' '}
              {plural(sites.length, 'объект', 'объекта', 'объектов')},{' '}
              {plural(
                sites.reduce((a, s) => a + s.posts.length, 0),
                'пост', 'поста', 'постов',
              )}
            </span>
          </div>

          <p className="mb-3 max-w-4xl text-xs text-slate-500">
            Зона — группа географически близких объектов. В зоне заводятся её
            экипажи ГБР (за пределы зоны они не выезжают) и охраняемые объекты, а
            внутри объекта — посты. Ставка правится по клику. Цветная засечка —
            юрлицо объекта; делённый расклад выделен, потому что он исключение.
            «Людей» — сколько стоит в {MONTH_GENITIVE[period.month - 1]}.
            Должности здесь нет: точка не бывает «охранником», должность у
            человека — она в табеле, в строке.
          </p>

          {zones.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-400">
              Зон пока нет. Начните с зоны обслуживания — объекты заводятся внутри неё.
            </p>
          ) : (
            <table className="w-full border-separate border-spacing-0 text-[13px]">
              <thead>
                <tr className="text-[11px] font-semibold text-slate-500">
                  <th className="border-b border-slate-200 px-2 py-1.5 text-left">
                    Зона, объект и пост
                  </th>
                  <th className="w-[150px] border-b border-slate-200 px-2 py-1.5 text-right">
                    Ставка за смену
                  </th>
                  <th className="w-[300px] border-b border-slate-200 px-2 py-1.5 text-left">
                    Юрлицо
                  </th>
                  <th className="w-[70px] border-b border-slate-200 px-2 py-1.5 text-right">
                    Людей
                  </th>
                  <th className="w-[230px] border-b border-slate-200" />
                </tr>
              </thead>
              <tbody>
                {shownZones.map(({ zone, zoneCrews, zoneSites }) => (
                  <Fragment key={zone.id}>
                    <tr className="group">
                      <td className="border-b-2 border-slate-800 px-2 pb-1.5 pt-5">
                        <span className="text-[15px] font-bold tracking-tight">
                          {highlight(zone.name, query)}
                        </span>
                        <span className="ml-2.5 text-xs text-slate-500">
                          {plural(zoneCrews.length, 'экипаж', 'экипажа', 'экипажей')},{' '}
                          {plural(zoneSites.length, 'объект', 'объекта', 'объектов')},{' '}
                          {plural(
                            zoneSites.reduce((a, s) => a + s.posts.length, 0),
                            'пост', 'поста', 'постов',
                          )}
                        </span>
                      </td>
                      <td className="border-b-2 border-slate-800" colSpan={3} />
                      <td className="border-b-2 border-slate-800 px-2 pb-1.5 pt-5 text-right">
                        {canManage && (
                          <span className="invisible flex justify-end gap-3 whitespace-nowrap text-[11.5px] group-hover:visible">
                            <button
                              type="button"
                              onClick={() => setEditSite({ zone, site: null })}
                              className="cursor-pointer text-blue-700 hover:underline"
                            >
                              Добавить объект
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditCrew({ zone, crew: null })}
                              className="cursor-pointer text-blue-700 hover:underline"
                            >
                              Добавить экипаж
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditZone(zone)}
                              className="cursor-pointer text-blue-700 hover:underline"
                            >
                              Изменить
                            </button>
                          </span>
                        )}
                      </td>
                    </tr>

                    {zoneCrews.map((crew) => (
                      <tr key={`crew-${crew.id}`} className="group hover:bg-slate-50">
                        <td className="border-b border-slate-100 px-2 py-[3px]">
                          <span className="mr-2 rounded bg-amber-100 px-1.5 py-px text-[9.5px] font-bold text-amber-900">
                            ГБР
                          </span>
                          <span className="font-semibold">
                            {highlight(crew.name, query)}
                          </span>
                        </td>
                        <td className="border-b border-slate-100 px-2 py-[3px] text-right">
                          <RateCell
                            value={crew.shift_rate}
                            editable={canManage}
                            onSave={(v) => saveCrewRate(crew, v)}
                          />
                        </td>
                        <td className="border-b border-slate-100 px-2 py-[3px]">
                          <Companies shares={crew.shares} companies={companies} />
                        </td>
                        <td className="border-b border-slate-100 px-2 py-[3px] text-right tabular-nums">
                          <People n={peopleOnCrew(crew.id)} />
                        </td>
                        <td className="border-b border-slate-100 px-2 py-[3px] text-right">
                          {canManage && (
                            <button
                              type="button"
                              onClick={() => setEditCrew({ zone, crew })}
                              className="invisible cursor-pointer text-[11.5px] text-blue-700 hover:underline group-hover:visible"
                            >
                              Изменить
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}

                    {zoneSites.map((site) => {
                      // Один пост, названный как объект и без своей ставки, — это
                      // тот же объект другими словами: отдельной строки не рисуем.
                      const only =
                        site.posts.length === 1 &&
                        site.posts[0].name === site.name &&
                        site.posts[0].shift_rate === null
                      return (
                        <Fragment key={`site-${site.id}`}>
                          <tr className="group hover:bg-slate-50">
                            <td className="border-b border-slate-100 px-2 py-[3px]">
                              <span className="font-semibold">
                                {highlight(site.name, query)}
                              </span>
                            </td>
                            <td className="border-b border-slate-100 px-2 py-[3px] text-right">
                              <RateCell
                                value={site.shift_rate}
                                editable={canManage}
                                onSave={(v) => saveSiteRate(site, v)}
                              />
                            </td>
                            <td className="border-b border-slate-100 px-2 py-[3px]">
                              <Companies shares={site.shares} companies={companies} />
                            </td>
                            <td className="border-b border-slate-100 px-2 py-[3px] text-right tabular-nums">
                              <People n={peopleOnSite(site.id)} />
                            </td>
                            <td className="border-b border-slate-100 px-2 py-[3px] text-right">
                              {canManage && (
                                <span className="invisible flex justify-end gap-3 whitespace-nowrap text-[11.5px] group-hover:visible">
                                  <button
                                    type="button"
                                    onClick={() => setEditPost({ site, post: null })}
                                    className="cursor-pointer text-blue-700 hover:underline"
                                  >
                                    Добавить пост
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setEditSite({ zone, site })}
                                    className="cursor-pointer text-blue-700 hover:underline"
                                  >
                                    Изменить
                                  </button>
                                </span>
                              )}
                            </td>
                          </tr>

                          {site.posts.length === 0 && (
                            <tr>
                              <td
                                className="border-b border-slate-100 py-[3px] pl-8 text-[12px] text-red-700"
                                colSpan={5}
                              >
                                Постов нет — на объект никого не поставить
                              </td>
                            </tr>
                          )}

                          {!only &&
                            site.posts.map((post) => (
                              <tr key={post.id} className="group hover:bg-slate-50">
                                <td className="border-b border-slate-100 py-[3px] pl-8 text-slate-600">
                                  <span className="mr-2 text-slate-300">└</span>
                                  {highlight(post.name, query)}
                                </td>
                                <td className="border-b border-slate-100 px-2 py-[3px] text-right text-slate-600">
                                  <RateCell
                                    value={post.effective_rate}
                                    editable={canManage}
                                    onSave={(v) => savePostRate(post, v)}
                                  />
                                  {post.shift_rate === null && (
                                    <span
                                      className="ml-1 text-[11px] text-slate-400"
                                      title="Своей ставки нет — применяется ставка объекта"
                                    >
                                      от объекта
                                    </span>
                                  )}
                                </td>
                                <td className="border-b border-slate-100" colSpan={2} />
                                <td className="border-b border-slate-100 px-2 py-[3px] text-right">
                                  {canManage && (
                                    <button
                                      type="button"
                                      onClick={() => setEditPost({ site, post })}
                                      className="invisible cursor-pointer text-[11.5px] text-blue-700 hover:underline group-hover:visible"
                                    >
                                      Изменить
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ))}
                        </Fragment>
                      )
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}

          {zones.length > 0 && shownZones.length === 0 && (
            <p className="p-8 text-center text-sm text-slate-400">
              Ничего не нашлось. Измените запрос.
            </p>
          )}
        </>
      )}

      {tab === 'roster' && <CrewRoster canManage={canManage} />}

      {editZone && (
        <ZoneModal
          zone={editZone === 'new' ? null : editZone}
          departments={departments}
          onClose={() => setEditZone(null)}
          onDone={() => {
            setEditZone(null)
            void reload()
          }}
        />
      )}

      {editSite && (
        <SiteModal
          zone={editSite.zone}
          site={editSite.site}
          zones={zones}
          companies={companies}
          onClose={() => setEditSite(null)}
          onDone={() => {
            setEditSite(null)
            void reload()
          }}
        />
      )}

      {editCrew && (
        <CrewModal
          zone={editCrew.zone}
          crew={editCrew.crew}
          companies={companies}
          onClose={() => setEditCrew(null)}
          onDone={() => {
            setEditCrew(null)
            void reload()
          }}
        />
      )}

      {editPost && (
        <PostModal
          site={editPost.site}
          post={editPost.post}
          onClose={() => setEditPost(null)}
          onDone={() => {
            setEditPost(null)
            void reload()
          }}
        />
      )}
    </div>
  )
}

/** Редактор процентов по юрлицам — общий для объекта и экипажа. */
function SharesEditor({
  companies,
  shares,
  onChange,
  hint,
}: {
  companies: Company[]
  shares: Record<number, string>
  onChange: (next: Record<number, string>) => void
  hint: string
}) {
  const total = useMemo(
    () => Object.values(shares).reduce((sum, v) => sum + (parseFloat(v) || 0), 0),
    [shares],
  )
  const ok = Object.keys(shares).length === 0 || Math.abs(total - 100) < 1
  return (
    <div className="mt-4">
      <p className="mb-2 text-sm font-medium text-gray-700">
        Распределение по юрлицам, %{' '}
        <span className={ok ? 'text-gray-400' : 'text-rose-600'}>
          (сейчас {total.toFixed(2)}, нужно 100)
        </span>
      </p>
      <div className="grid grid-cols-2 gap-2">
        {companies.map((company) => (
          <label key={company.id} className="flex items-center gap-2 text-sm">
            <span className="flex-1 truncate text-gray-600">{companyLabel(company)}</span>
            <input
              value={shares[company.id] ?? ''}
              onChange={(e) => onChange({ ...shares, [company.id]: e.target.value })}
              placeholder="0"
              className="w-20 rounded-md border border-gray-300 px-2 py-1 text-right"
            />
          </label>
        ))}
      </div>
      <p className="mt-2 text-xs text-gray-500">{hint}</p>
    </div>
  )
}

function sharesToList(shares: Record<number, string>) {
  return Object.entries(shares)
    .filter(([, v]) => (parseFloat(v) || 0) > 0)
    .map(([id, percent]) => ({
      company_id: Number(id),
      percent: String(parseFloat(percent)),
    }))
}

function sharesFrom(list: { company_id: number; percent: string }[]) {
  const initial: Record<number, string> = {}
  list.forEach((s) => {
    initial[s.company_id] = String(parseFloat(s.percent))
  })
  return initial
}

function ZoneModal({
  zone,
  departments,
  onClose,
  onDone,
}: {
  zone: VahtaZone | null
  departments: VahtaDepartment[]
  onClose: () => void
  onDone: () => void
}) {
  const [name, setName] = useState(zone?.name ?? '')
  const [departmentId, setDepartmentId] = useState(
    zone?.department_id ?? departments[0]?.id ?? 0,
  )
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    try {
      if (zone) await updateVahtaZone(zone.id, { name: name.trim() })
      else await createVahtaZone({ name: name.trim(), department_id: departmentId })
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить зону')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!zone) return
    if (
      !window.confirm(
        'Снять зону? Её объекты и экипажи тоже будут отключены — история табеля сохранится.',
      )
    )
      return
    try {
      await deleteVahtaZone(zone.id)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось снять зону')
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={zone ? `Зона: ${zone.name}` : 'Новая зона обслуживания'}
      actions={
        <div className="flex w-full items-center justify-between">
          {zone ? (
            <Button variant="ghost" onClick={remove}>
              Снять
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button onClick={submit} loading={saving} disabled={!name.trim()}>
              Сохранить
            </Button>
          </div>
        </div>
      }
    >
      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Название зоны</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Зона 1"
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        />
      </label>

      {!zone && (
        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Подразделение</span>
          <select
            value={departmentId}
            onChange={(e) => setDepartmentId(Number(e.target.value))}
            className="w-full rounded-md border border-gray-300 px-3 py-2"
          >
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[11px] text-gray-400">
            Отдел задаётся у зоны — объекты и экипажи внутри берут его отсюда.
          </span>
        </label>
      )}
    </Modal>
  )
}

function SiteModal({
  zone,
  site,
  zones,
  companies,
  onClose,
  onDone,
}: {
  zone: VahtaZone
  site: VahtaSite | null
  zones: VahtaZone[]
  companies: Company[]
  onClose: () => void
  onDone: () => void
}) {
  const [name, setName] = useState(site?.name ?? '')
  const [zoneId, setZoneId] = useState(site?.zone_id ?? zone.id)
  const [rate, setRate] = useState(site ? String(parseFloat(site.shift_rate ?? '0')) : '')
  const [shares, setShares] = useState<Record<number, string>>(() =>
    sharesFrom(site?.shares ?? []),
  )
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    const payload = {
      name: name.trim(),
      shift_rate: rate || '0',
      shares: sharesToList(shares),
    }
    try {
      if (site) await updateVahtaSite(site.id, { ...payload, zone_id: zoneId })
      else await createVahtaSite({ ...payload, zone_id: zoneId })
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить объект')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!site) return
    if (!window.confirm('Удалить объект? Если по его постам вели табель, он будет отключён.'))
      return
    try {
      await deleteVahtaSite(site.id)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось удалить объект')
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={site ? `Объект: ${site.name}` : `Новый объект — ${zone.name}`}
      size="xl"
      actions={
        <div className="flex w-full items-center justify-between">
          {site ? (
            <Button variant="ghost" onClick={remove}>
              Удалить
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button onClick={submit} loading={saving} disabled={!name.trim()}>
              Сохранить
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <label className="col-span-2 block text-sm">
          <span className="mb-1 block text-gray-600">Название объекта</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Green Wood / КП Олимп / РЖД Архив"
            className="w-full rounded-md border border-gray-300 px-3 py-2"
          />
        </label>

        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Зона обслуживания</span>
          <select
            value={zoneId}
            onChange={(e) => setZoneId(Number(e.target.value))}
            className="w-full rounded-md border border-gray-300 px-3 py-2"
          >
            {zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[11px] text-gray-400">
            Отсюда берётся подразделение и экипаж ГБР объекта.
          </span>
        </label>

        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Ставка за смену по умолчанию</span>
          <input
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-right"
          />
          <span className="mt-1 block text-[11px] text-gray-400">
            Применяется ко всем постам объекта; пост может её переопределить.
          </span>
        </label>
      </div>

      <SharesEditor
        companies={companies}
        shares={shares}
        onChange={setShares}
        hint="Эти проценты применяются ко ВСЕМ постам объекта — своих у поста нет. Нужны два разных расклада на одном месте — заведите два объекта."
      />
    </Modal>
  )
}

function PostModal({
  site,
  post,
  onClose,
  onDone,
}: {
  site: VahtaSite
  post: VahtaPost | null
  onClose: () => void
  onDone: () => void
}) {
  const [name, setName] = useState(post?.name ?? '')
  const [rate, setRate] = useState(
    post?.shift_rate != null ? String(parseFloat(post.shift_rate)) : '',
  )
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    // Пустая ставка — «как у объекта», а не ноль: ноль означал бы бесплатный пост.
    const payload = { name: name.trim(), shift_rate: rate.trim() === '' ? null : rate }
    try {
      if (post) await updateVahtaPost(post.id, payload)
      else await createVahtaPost({ ...payload, site_id: site.id })
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить пост')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!post) return
    if (!window.confirm('Удалить пост? Если по нему уже вели табель, он будет отключён.')) return
    try {
      await deleteVahtaPost(post.id)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось удалить пост')
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={post ? `Пост: ${post.name}` : `Новый пост — ${site.name}`}
      actions={
        <div className="flex w-full items-center justify-between">
          {post ? (
            <Button variant="ghost" onClick={remove}>
              Удалить
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button onClick={submit} loading={saving} disabled={!name.trim()}>
              Сохранить
            </Button>
          </div>
        </div>
      }
    >
      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Название поста</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="GW 1 / КПП / Обход"
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        />
      </label>

      <label className="block text-sm">
        <span className="mb-1 block text-gray-600">Своя ставка за смену</span>
        <input
          value={rate}
          onChange={(e) => setRate(e.target.value)}
          placeholder={`как у объекта — ${parseFloat(site.shift_rate ?? '0')}`}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-right"
        />
        <span className="mt-1 block text-[11px] text-gray-400">
          Пусто — применяется ставка объекта. Заполняют, когда на объекте посты
          разной цены: на «КП Олимп» ГБР стоит 4 500 при 3 000 у охраны.
        </span>
      </label>

      <p className="mt-3 rounded-md bg-blue-50 p-2 text-xs text-blue-900">
        У поста нет ни процентов, ни должности: проценты берутся от объекта
        «{site.name}», а должность — у человека, которого на пост ставят. На
        одном посту могут стоять и ГБР, и охранник с разными ставками.
      </p>
    </Modal>
  )
}

function CrewModal({
  zone,
  crew,
  companies,
  onClose,
  onDone,
}: {
  zone: VahtaZone
  crew: VahtaCrew | null
  companies: Company[]
  onClose: () => void
  onDone: () => void
}) {
  const [name, setName] = useState(crew?.name ?? '')
  const [rate, setRate] = useState(crew ? String(parseFloat(crew.shift_rate ?? '0')) : '')
  const [shares, setShares] = useState<Record<number, string>>(() =>
    sharesFrom(crew?.shares ?? []),
  )
  const [saving, setSaving] = useState(false)

  const submit = async () => {
    setSaving(true)
    const payload = {
      name: name.trim(),
      shift_rate: rate || '0',
      shares: sharesToList(shares),
    }
    try {
      if (crew) await updateVahtaCrew(crew.id, payload)
      else await createVahtaCrew({ ...payload, zone_id: zone.id })
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить экипаж')
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!crew) return
    if (!window.confirm('Снять экипаж? Объекты зоны это не затронет.')) return
    try {
      await deleteVahtaCrew(crew.id)
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось снять экипаж')
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={crew ? `Экипаж ГБР: ${crew.name}` : `Новый экипаж ГБР — ${zone.name}`}
      size="xl"
      actions={
        <div className="flex w-full items-center justify-between">
          {crew ? (
            <Button variant="ghost" onClick={remove}>
              Снять
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>
              Отмена
            </Button>
            <Button onClick={submit} loading={saving} disabled={!name.trim()}>
              Сохранить
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Название</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="1 экипаж"
            className="w-full rounded-md border border-gray-300 px-3 py-2"
          />
        </label>

        <label className="block text-sm">
          <span className="mb-1 block text-gray-600">Ставка за смену</span>
          <input
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-right"
          />
        </label>
      </div>

      <p className="mt-3 rounded-md bg-amber-50 p-2 text-xs text-amber-900">
        Экипаж принадлежит зоне «{zone.name}» и выезжает только на её объекты —
        зоны разнесены географически. В зоне экипажей может быть несколько.
      </p>

      <SharesEditor
        companies={companies}
        shares={shares}
        onChange={setShares}
        hint="У экипажа своё распределение — оно не связано с объектами зоны: в образце у экипажей 35/60/5, а у объектов 100 % на одно юрлицо."
      />
    </Modal>
  )
}
