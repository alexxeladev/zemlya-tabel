import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'

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
  listVahtaCrews,
  listVahtaSites,
  listVahtaZones,
  updateVahtaCrew,
  updateVahtaPost,
  updateVahtaSite,
  updateVahtaZone,
} from '../../api/vahta'
import type { VahtaShare } from '../../types/api'
import { Button } from '../Button'
import { Modal } from '../Modal'
import { DsButton } from '../ds/Button'
import { EmptyState } from '../ds/EmptyState'
import { RowMenu, type MenuItem } from '../ds/Menu'
import { SearchField } from '../ds/fields'
import { MONTHS_RU, MONTHS_RU_PREP } from '../../utils/ruDate'
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
  if (shares.length === 0) return <span className="text-ds-muted">не задано</span>

  const colorOf = (id: number) => {
    const idx = companies.findIndex((c) => c.id === id)
    return companyColorByIndex(idx < 0 ? 0 : idx).color
  }

  if (shares.length === 1) {
    const only = shares[0]
    return (
      <span className="inline-flex items-center gap-2 text-[12.5px]">
        <span
          className="inline-block h-4 w-[3px] rounded-sm"
          style={{ background: colorOf(only.company_id) }}
          title="Юрлицо объекта: все затраты на него"
        />
        {only.company_display_name ?? only.company_name}
      </span>
    )
  }
  return (
    <span
      className="whitespace-nowrap rounded-ds-sm border border-ds-warn-line bg-ds-warn-soft px-2 py-px text-[12px] text-ds-warn"
      title="Делёный расклад — исключение: затраты делятся между юрлицами"
    >
      {shares
        .map((s) => `${s.company_display_name ?? s.company_name} ${parseFloat(s.percent)}`)
        .join(' · ')}
    </span>
  )
}

/** Сколько человек стоит на месте в этом месяце. Ноль — это дыра: словом, красным. */
function People({ n }: { n: number }) {
  return n > 0 ? (
    <span>{n}</span>
  ) : (
    <span className="font-medium text-ds-danger" title="Место заведено, а людей нет">
      никого
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
        aria-label="Ставка за смену"
        className="h-[26px] w-[96px] rounded-ds-sm border border-ds-accent px-1.5 text-right font-ds-mono text-[12.5px] tabular-nums shadow-[0_0_0_3px_color-mix(in_srgb,var(--ds-accent)_16%,transparent)] focus:outline-none"
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
      title="Изменить ставку"
      className="inline-flex h-[26px] cursor-pointer items-center rounded-ds-sm border border-dashed border-transparent px-1.5 tabular-nums hover:border-ds-control-line hover:bg-ds-surface"
    >
      {formatMoney(value, { showZero: true })}
    </button>
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
export function ZonesTab({ year, month }: { year: number; month: number }) {
  const [departments, setDepartments] = useState<VahtaDepartment[]>([])
  const [zones, setZones] = useState<VahtaZone[]>([])
  const [sites, setSites] = useState<VahtaSite[]>([])
  const [crews, setCrews] = useState<VahtaCrew[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [monthView, setMonthView] = useState<VahtaMonth | null>(null)
  const [query, setQuery] = useState('')
  const [editZone, setEditZone] = useState<VahtaZone | 'new' | null>(null)
  const [editSite, setEditSite] = useState<{ zone: VahtaZone; site: VahtaSite | null } | null>(null)
  const [editCrew, setEditCrew] = useState<{ zone: VahtaZone; crew: VahtaCrew | null } | null>(null)
  const [editPost, setEditPost] = useState<{ site: VahtaSite; post: VahtaPost | null } | null>(null)
  const [loading, setLoading] = useState(true)
  const role = useAuthStore((s) => s.user?.role)
  const canManage = role === 'admin' || role === 'manager'

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
          getVahtaMonth(year, month).catch(() => null),
        ])
      setDepartments(deps)
      setZones(zoneList)
      setSites(siteList)
      setCrews(crewList)
      setCompanies(companyList)
      setMonthView(monthData)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось загрузить настройки')
    } finally {
      setLoading(false)
    }
  }, [year, month])

  useEffect(() => {
    void reload()
  }, [reload])

  const peopleOnSite = useCallback(
    (siteId: number) => {
      for (const z of monthView?.zones ?? [])
        for (const c of z.cards)
          if (c.kind === 'site' && c.id === siteId)
            // ЛЮДИ, а не строки: человек на двух половинах месяца — две строки.
            return new Set(c.rows.filter((r) => r.employee_id).map((r) => r.employee_id)).size
      return 0
    },
    [monthView],
  )
  const peopleOnCrew = useCallback(
    (crewId: number) => {
      for (const z of monthView?.zones ?? [])
        for (const c of z.cards)
          if (c.kind === 'crew' && c.id === crewId)
            return new Set(c.rows.filter((r) => r.employee_id).map((r) => r.employee_id)).size
      return 0
    },
    [monthView],
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

  if (loading) return <p className="text-[13px] text-ds-muted">Загрузка…</p>

  const postCount = (list: VahtaSite[]) => list.reduce((a, s) => a + s.posts.length, 0)
  const td = 'border-b border-ds-line px-2.5 py-1.5 align-middle'
  const th = 'border-b border-ds-line-strong bg-ds-surface-2 px-2.5 py-2 text-[11px] font-semibold text-ds-muted'

  // Действия строки — в меню «⋯», видимом всегда: ссылки по наведению были
  // недостижимы с клавиатуры (WCAG 2.1.1) и читались кашей в 60 строках.
  const zoneMenu = (zone: VahtaZone): MenuItem[] =>
    canManage
      ? [
          { label: 'Добавить объект…', onSelect: () => setEditSite({ zone, site: null }) },
          { label: 'Добавить экипаж ГБР…', onSelect: () => setEditCrew({ zone, crew: null }) },
          { label: 'Изменить зону…', onSelect: () => setEditZone(zone) },
        ]
      : []

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <SearchField
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Зона, объект или пост"
          aria-label="Поиск по зонам, объектам и постам"
          className="w-[280px]"
        />
        {canManage && (
          <DsButton variant="primary" icon="plus" onClick={() => setEditZone('new')}>
            Добавить зону
          </DsButton>
        )}
        <span className="ml-auto text-[12.5px] text-ds-muted">
          {plural(zones.length, 'зона', 'зоны', 'зон')},{' '}
          {plural(sites.length, 'объект', 'объекта', 'объектов')},{' '}
          {plural(postCount(sites), 'пост', 'поста', 'постов')}
        </span>
      </div>

      <p className="mb-3 text-[12.5px] text-ds-muted">
        Ставка правится по клику. «Людей» — сколько стоит на месте в {MONTHS_RU_PREP[month - 1]}.
      </p>

      {zones.length === 0 ? (
        <EmptyState
          title="Зон пока нет"
          action={
            canManage && (
              <DsButton variant="primary" icon="plus" onClick={() => setEditZone('new')}>
                Добавить зону
              </DsButton>
            )
          }
        >
          Начните с зоны обслуживания — объекты, посты и экипажи ГБР заводятся внутри неё.
        </EmptyState>
      ) : shownZones.length === 0 ? (
        <EmptyState title="Ничего не нашлось" compact>
          Измените запрос — поиск идёт по зонам, объектам и постам.
        </EmptyState>
      ) : (
        <table className="w-full border-separate border-spacing-0 overflow-hidden rounded-ds-lg border border-ds-line bg-ds-surface text-[13px]">
          <thead>
            <tr>
              <th className={`${th} text-left`}>Зона, объект и пост</th>
              <th className={`${th} w-[170px] text-right`}>Ставка за смену</th>
              <th className={`${th} w-[360px] text-left`}>Юрлицо</th>
              <th className={`${th} w-[120px] text-right`}>Людей ({MONTHS_RU[month - 1].toLowerCase()})</th>
              <th className={`${th} w-12`}>
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shownZones.map(({ zone, zoneCrews, zoneSites }) => (
              <Fragment key={zone.id}>
                <tr>
                  <td className="border-b-2 border-ds-ink-2 px-2.5 pb-1.5 pt-4" colSpan={4}>
                    <span className="text-[15px] font-semibold tracking-tight">
                      {highlight(zone.name, query)}
                    </span>
                    <span className="ml-2.5 text-[12px] text-ds-muted">
                      {plural(zoneCrews.length, 'экипаж', 'экипажа', 'экипажей')},{' '}
                      {plural(zoneSites.length, 'объект', 'объекта', 'объектов')},{' '}
                      {plural(postCount(zoneSites), 'пост', 'поста', 'постов')}
                    </span>
                  </td>
                  <td className="border-b-2 border-ds-ink-2 px-2.5 pb-1 pt-4 text-right">
                    <RowMenu items={zoneMenu(zone)} label={`Действия: ${zone.name}`} />
                  </td>
                </tr>

                {zoneCrews.map((crew) => (
                  <tr key={`crew-${crew.id}`} className="hover:bg-ds-surface-2">
                    <td className={td}>
                      <span className="mr-2 rounded-[4px] border border-ds-warn-line bg-ds-warn-soft px-1.5 py-px text-[10.5px] font-semibold text-ds-warn">
                        ГБР
                      </span>
                      <span className="font-semibold">{highlight(crew.name, query)}</span>
                    </td>
                    <td className={`${td} text-right`}>
                      <RateCell
                        value={crew.shift_rate}
                        editable={canManage}
                        onSave={(v) => saveCrewRate(crew, v)}
                      />
                    </td>
                    <td className={td}>
                      <Companies shares={crew.shares} companies={companies} />
                    </td>
                    <td className={`${td} text-right tabular-nums`}>
                      <People n={peopleOnCrew(crew.id)} />
                    </td>
                    <td className={`${td} text-right`}>
                      <RowMenu
                        items={canManage ? [{ label: 'Изменить экипаж…', onSelect: () => setEditCrew({ zone, crew }) }] : []}
                        label={`Действия: ${crew.name}`}
                      />
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
                      <tr className="hover:bg-ds-surface-2">
                        <td className={td}>
                          <span className="font-semibold">{highlight(site.name, query)}</span>
                        </td>
                        <td className={`${td} text-right`}>
                          <RateCell
                            value={site.shift_rate}
                            editable={canManage}
                            onSave={(v) => saveSiteRate(site, v)}
                          />
                        </td>
                        <td className={td}>
                          <Companies shares={site.shares} companies={companies} />
                        </td>
                        <td className={`${td} text-right tabular-nums`}>
                          <People n={peopleOnSite(site.id)} />
                        </td>
                        <td className={`${td} text-right`}>
                          <RowMenu
                            items={
                              canManage
                                ? [
                                    { label: 'Добавить пост…', onSelect: () => setEditPost({ site, post: null }) },
                                    { label: 'Изменить объект…', onSelect: () => setEditSite({ zone, site }) },
                                  ]
                                : []
                            }
                            label={`Действия: ${site.name}`}
                          />
                        </td>
                      </tr>

                      {site.posts.length === 0 && (
                        <tr>
                          <td className={`${td} pl-9 text-[12.5px] text-ds-danger`} colSpan={5}>
                            Постов нет — на объект никого не поставить
                          </td>
                        </tr>
                      )}

                      {!only &&
                        site.posts.map((post) => (
                          <tr key={post.id} className="hover:bg-ds-surface-2">
                            <td className={`${td} pl-9 text-ds-ink-2`}>{highlight(post.name, query)}</td>
                            <td className={`${td} text-right text-ds-ink-2`}>
                              <RateCell
                                value={post.effective_rate}
                                editable={canManage}
                                onSave={(v) => savePostRate(post, v)}
                              />
                              {post.shift_rate === null && (
                                <span
                                  className="ml-1.5 text-[11.5px] text-ds-muted"
                                  title="Своей ставки нет — применяется ставка объекта"
                                >
                                  от объекта
                                </span>
                              )}
                            </td>
                            <td className={td} colSpan={2} />
                            <td className={`${td} text-right`}>
                              <RowMenu
                                items={canManage ? [{ label: 'Изменить пост…', onSelect: () => setEditPost({ site, post }) }] : []}
                                label={`Действия: ${post.name}`}
                              />
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
