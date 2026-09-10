import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  deleteVahtaAssignment,
  getVahtaMonth,
  updateVahtaAssignment,
} from '../../api/vahta'
import { usePeriodStore } from '../../store/period'
import { toast } from '../../store/toasts'
import type { VahtaCard, VahtaMonth, VahtaRow, VahtaZoneCard } from '../../types/api'
import { formatMoney } from '../../utils/money'

const MONTHS = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]

/**
 * «Состав людей в экипажах» — четвёртый пункт настроек модуля.
 *
 * ЭТО ВИТРИНА, а не отдельная сущность. Постоянного списка «кто состоит в
 * экипаже» в базе нет и заводить его нельзя: состав вахты по своей природе
 * месячный (между периодами люди меняются, это норма), и второй источник правды
 * неминуемо разошёлся бы с табелем — пришлось бы решать, что делать с человеком,
 * который в списке экипажа есть, а ни на одном посту не стоит.
 *
 * Поэтому здесь показан состав ВЫБРАННОГО МЕСЯЦА — ровно то же, что в табеле,
 * но одним списком и с тем, чего в табеле нет: переходом в карточку сотрудника
 * и правкой ставки строки. Месяц общий с табелем и ведомостью (`store/period`).
 */
export function CrewRoster({ canManage }: { canManage: boolean }) {
  const { year, month, setPeriod } = usePeriodStore()
  const [data, setData] = useState<VahtaMonth | null>(null)
  const [loading, setLoading] = useState(true)
  const [editRate, setEditRate] = useState<{ id: number; value: string } | null>(null)

  const reload = useCallback(async () => {
    try {
      setData(await getVahtaMonth(year, month))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось загрузить состав')
    } finally {
      setLoading(false)
    }
  }, [year, month])

  useEffect(() => {
    setLoading(true)
    void reload()
  }, [reload])

  const zones = useMemo<VahtaZoneCard[]>(() => data?.zones ?? [], [data])

  // Считаем ЛЮДЕЙ, а не строки: у совместителя рабочих мест несколько, а
  // человек один — как в счётчике сотрудников основного табеля.
  const peopleOf = (card: VahtaCard) =>
    new Set(card.rows.filter((r) => r.employee_id).map((r) => r.employee_id)).size

  const saveRate = async (row: VahtaRow, value: string) => {
    try {
      await updateVahtaAssignment(row.id, { rate: value || '0' })
      setEditRate(null)
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить ставку')
    }
  }

  const remove = async (row: VahtaRow) => {
    if (
      !window.confirm(
        `Убрать ${row.employee_name ?? 'строку'} из состава за ${MONTHS[month - 1]}? ` +
          'Сотрудник останется в общем справочнике.',
      )
    )
      return
    try {
      await deleteVahtaAssignment(row.id)
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось убрать из состава')
    }
  }

  if (loading) return <p className="text-sm text-gray-500">Загрузка…</p>

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-3xl text-xs text-gray-500">
          Состав месячный: между периодами люди меняются, это норма. Здесь тот же
          состав, что в табеле выбранного месяца — добавляют человека на экране
          «Вахта» кнопкой «поставить человека на пост», а ФИО, доступ и кадровые
          поля правятся в карточке сотрудника (клик по имени).
        </p>
        <div className="flex items-center gap-2">
          <select
            value={month}
            onChange={(e) => setPeriod(year, Number(e.target.value))}
            className="rounded-md border border-gray-300 px-2 py-1.5 text-sm"
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
            className="w-20 rounded-md border border-gray-300 px-2 py-1.5 text-sm"
          />
          <Link
            to="/vahta"
            className="rounded-md bg-gray-100 px-3 py-1.5 text-sm text-gray-800 hover:bg-gray-200"
          >
            К табелю
          </Link>
        </div>
      </div>

      {zones.length === 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-6 text-center text-sm text-gray-400">
          За {MONTHS[month - 1]} {year} состав не заведён. Поставьте людей на посты
          на экране «Вахта» или скопируйте состав прошлого месяца.
        </div>
      )}

      {zones.map((zone) => (
        <div key={zone.zone_id} className="mb-4">
          <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-slate-400">
            Зона обслуживания · <span className="text-slate-700">{zone.zone_name}</span>
          </p>
          {zone.cards.map((card: VahtaCard) => (
        <div
          key={`${card.kind}-${card.id}`}
          className="mb-3 overflow-hidden rounded-lg border border-gray-200 bg-white"
        >
          <div className="flex items-baseline justify-between border-b border-gray-100 bg-gray-50 px-4 py-2">
            <div>
              <span
                className={`mr-2 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${
                  card.kind === 'crew'
                    ? 'bg-amber-100 text-amber-800'
                    : 'bg-slate-200 text-slate-700'
                }`}
              >
                {card.kind === 'crew' ? 'Экипаж ГБР' : 'Объект'}
              </span>
              <span className="font-semibold text-slate-800">{card.name}</span>
              <span className="ml-2 text-xs text-gray-500">
                {peopleOf(card)} чел.
                {card.rows.length !== peopleOf(card) && ` · ${card.rows.length} позиций`}
              </span>
            </div>
            {card.objects.length > 0 && (
              <span className="text-[11px] text-gray-400">
                {card.kind === 'crew' ? 'Обслуживает: ' : 'Посты: '}
                {card.objects.join(' · ')}
              </span>
            )}
          </div>

          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-gray-400">
              <tr>
                <th className="px-4 py-1.5 font-medium">Таб. №</th>
                <th className="px-4 py-1.5 font-medium">Сотрудник</th>
                <th className="px-4 py-1.5 font-medium">Место</th>
                <th className="px-4 py-1.5 font-medium">Тип</th>
                <th className="px-4 py-1.5 text-right font-medium">Ставка</th>
                <th className="px-4 py-1.5 text-right font-medium">Смен</th>
                <th className="px-4 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {card.rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-4 text-center text-gray-400">
                    В этом месяце никого
                  </td>
                </tr>
              )}
              {card.rows.map((row: VahtaRow) => {
                const editing = editRate?.id === row.id ? editRate : null
                return (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-mono text-xs text-gray-500">
                    {row.tab_number ?? '—'}
                  </td>
                  <td className="px-4 py-2">
                    {row.employee_id ? (
                      <Link
                        to={`/admin/employees?employee_id=${row.employee_id}`}
                        title="Открыть карточку сотрудника"
                        className="font-medium text-slate-800 hover:text-blue-700 hover:underline"
                      >
                        {row.employee_name}
                      </Link>
                    ) : (
                      // Пустой слот: пост есть, человека нет — законное состояние,
                      // в образце заказчика такие строки тоже есть.
                      <span className="text-gray-400">— вакансия —</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-gray-600">{row.post_name}</td>
                  <td className="px-4 py-2 text-gray-600">{row.kind_label}</td>
                  <td className="px-4 py-2 text-right">
                    {editing ? (
                      <span className="inline-flex items-center gap-1">
                        <input
                          autoFocus
                          value={editing.value}
                          onChange={(e) =>
                            setEditRate({ id: row.id, value: e.target.value })
                          }
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void saveRate(row, editing.value)
                            if (e.key === 'Escape') setEditRate(null)
                          }}
                          className="w-24 rounded border border-blue-400 px-2 py-1 text-right"
                        />
                        <button
                          type="button"
                          onClick={() => void saveRate(row, editing.value)}
                          className="cursor-pointer text-xs text-blue-700"
                        >
                          ОК
                        </button>
                      </span>
                    ) : canManage ? (
                      <button
                        type="button"
                        title="Ставка этой строки. По умолчанию от поста, но правится."
                        onClick={() =>
                          setEditRate({
                            id: row.id,
                            value: String(parseFloat(row.rate ?? '0')),
                          })
                        }
                        className="cursor-pointer rounded border border-transparent px-2 py-0.5 hover:border-gray-300 hover:bg-gray-50"
                      >
                        {formatMoney(row.rate, { showZero: true })}
                        {row.kind === 'chief' && (
                          <span className="ml-1 text-[10px] text-gray-400">/мес</span>
                        )}
                      </button>
                    ) : (
                      formatMoney(row.rate, { showZero: true })
                    )}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-600">{row.shifts}</td>
                  <td className="px-4 py-2 text-right">
                    {canManage && (
                      <button
                        type="button"
                        onClick={() => void remove(row)}
                        className="cursor-pointer text-xs text-gray-400 hover:text-rose-600"
                      >
                        Убрать
                      </button>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
          ))}
        </div>
      ))}
    </div>
  )
}
