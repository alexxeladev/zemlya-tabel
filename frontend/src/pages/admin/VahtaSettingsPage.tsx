import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { getVahtaDepartments } from '../../api/vahta'
import { Icon } from '../../components/ds/Icon'
import { MonthPicker } from '../../components/ds/MonthPicker'
import { TabPanel, Tabs } from '../../components/ds/Tabs'
import { JobTitlesTab } from '../../components/vahta/JobTitlesTab'
import { StaffTab } from '../../components/vahta/StaffTab'
import { TaxTab } from '../../components/vahta/TaxTab'
import { ZonesTab } from '../../components/vahta/ZonesTab'
import { useAuthStore } from '../../store/auth'
import { usePeriodStore } from '../../store/period'
import type { VahtaDepartment } from '../../types/api'
import {
  MONTH_DEPENDENT_TABS,
  VAHTA_SETTINGS_TABS,
  VAHTA_SETTINGS_TAB_LABELS,
  parseMonthParam,
  parseSettingsTab,
  vahtaSettingsPath,
  type VahtaSettingsTab,
} from '../../utils/vahtaSettings'

/**
 * Настройки вахты (task_vahta_settings_staff).
 *
 * Всё, что в вахте ведётся как справочник, — вкладки ОДНОГО экрана: сотрудники
 * охраны, зоны с объектами и постами, должности, налог. Данные месяца (кто
 * стоит на посту, смены) — только в табеле: прежняя вкладка «Состав» его
 * дублировала и удалена.
 *
 * Состояние — в адресе, а не в useState: `/vahta/settings/<вкладка>`,
 * `?month=YYYY-MM` у вкладок, зависящих от месяца, `?position_id=` у открытого
 * рабочего места. Ссылка открывает ровно то же, перезагрузка ничего не теряет.
 * Собирает адреса только `utils/vahtaSettings`.
 *
 * Вкладки показываются только те, на которые у роли есть права. Сейчас у всех
 * четырёх права одни — admin и менеджер охраны, как у самого экрана, поэтому
 * фильтр пустой; появится вкладка с другими правами — её надо сюда не пускать.
 */
export function VahtaSettingsPage() {
  const params = useParams<{ tab?: string }>()
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const role = useAuthStore((s) => s.user?.role)
  const canManage = role === 'admin' || role === 'manager'
  const period = usePeriodStore()
  const [departments, setDepartments] = useState<VahtaDepartment[] | null>(null)

  const tab = parseSettingsTab(params.tab)
  // Месяц: из адреса, иначе — общий месяц табеля. Выбор в настройках двигает и
  // общий месяц: вернувшись в табель, человек видит тот же месяц.
  const fromUrl = parseMonthParam(search.get('month'))
  const year = fromUrl?.year ?? period.year
  const month = fromUrl?.month ?? period.month
  const positionParam = Number(search.get('position_id'))
  const positionId = Number.isInteger(positionParam) && positionParam > 0 ? positionParam : null

  useEffect(() => {
    getVahtaDepartments().then(setDepartments).catch(() => setDepartments([]))
  }, [])

  // Месяц из ссылки становится общим месяцем, чтобы табель открылся на нём же.
  useEffect(() => {
    if (fromUrl && (fromUrl.year !== period.year || fromUrl.month !== period.month)) {
      period.setPeriod(fromUrl.year, fromUrl.month)
    }
  }, [fromUrl, period])

  const openPosition = useCallback(
    (id: number | null) => {
      setSearch(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (id) next.set('position_id', String(id))
          else next.delete('position_id')
          return next
        },
        { replace: false },
      )
    },
    [setSearch],
  )

  // Неизвестная вкладка (в т.ч. голый `/vahta/settings`) — на первую по частоте.
  if (!tab) return <Navigate to={vahtaSettingsPath('staff', { year, month })} replace />

  const goTab = (next: VahtaSettingsTab) => navigate(vahtaSettingsPath(next, { year, month }))
  const setMonth = (y: number, m: number) => {
    period.setPeriod(y, m)
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set('month', `${y}-${String(m).padStart(2, '0')}`)
        return next
      },
      { replace: true },
    )
  }

  return (
    <div className="ds">
      <Link
        to="/vahta"
        className="mb-2 inline-flex items-center gap-1.5 text-[13px] text-ds-accent hover:underline"
      >
        <Icon name="arrowLeft" size={14} />К табелю
      </Link>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <h1 className="m-0 text-[23px] font-semibold tracking-[-0.025em]">Настройки вахты</h1>
        {MONTH_DEPENDENT_TABS.includes(tab) && (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[12.5px] text-ds-muted">
              {tab === 'staff' ? 'Пост и «официально» за' : '«Людей» за'}
            </span>
            <MonthPicker year={year} month={month} onChange={setMonth} />
          </div>
        )}
      </div>

      {departments !== null && departments.length === 0 ? (
        <div className="mt-4 rounded-ds-md border border-ds-warn-line bg-ds-warn-soft p-4 text-[13px] text-ds-warn">
          Нет подразделений охраны. Отметьте нужный отдел галочкой «подразделение охраны» в{' '}
          <Link to="/admin/org" className="underline">
            оргструктуре
          </Link>
          .
        </div>
      ) : (
        <>
          <Tabs
            label="Разделы настроек вахты"
            idPrefix="vahta-settings"
            className="mb-4 mt-3.5"
            value={tab}
            onChange={goTab}
            items={VAHTA_SETTINGS_TABS.map((id) => ({ id, label: VAHTA_SETTINGS_TAB_LABELS[id] }))}
          />
          <TabPanel id={tab} idPrefix="vahta-settings">
            {tab === 'staff' && (
              <StaffTab year={year} month={month} positionId={positionId} onOpen={openPosition} />
            )}
            {tab === 'zones' && <ZonesTab year={year} month={month} />}
            {tab === 'titles' && <JobTitlesTab canManage={canManage} />}
            {tab === 'tax' && <TaxTab editable={canManage} />}
          </TabPanel>
        </>
      )}
    </div>
  )
}
