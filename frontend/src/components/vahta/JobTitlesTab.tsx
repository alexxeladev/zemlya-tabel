import { useCallback, useEffect, useState } from 'react'

import {
  createGuardJobTitle,
  deleteGuardJobTitle,
  listGuardJobTitles,
  updateGuardJobTitle,
} from '../../api/vahta'
import { toast } from '../../store/toasts'
import type { GuardJobTitle, GuardPayType } from '../../types/api'
import { Button } from '../Button'
import { Modal } from '../Modal'

/**
 * Справочник должностей охраны — вкладка настроек вахты.
 *
 * Должность задаёт ОДНО: способ оплаты (ставка за смену / оклад за месяц).
 * Названий — сколько нужно заказчику: «Старший смены», «Оператор
 * видеонаблюдения» и т.п. Две должности помечены «по умолчанию» — с ними
 * встают на пост объекта и в экипаж ГБР, если должность не выбрали явно.
 *
 * Смена способа оплаты у должности, на которой уже стоят люди, пересчитывает
 * все НЕЗАКРЫТЫЕ месяцы — об этом спрашивается подтверждение.
 */
export function JobTitlesTab({ canManage }: { canManage: boolean }) {
  const [titles, setTitles] = useState<GuardJobTitle[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<GuardJobTitle | 'new' | null>(null)

  const reload = useCallback(async () => {
    try {
      setTitles(await listGuardJobTitles(true))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось загрузить должности')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const setDefault = async (t: GuardJobTitle, flag: 'default_for_post' | 'default_for_crew') => {
    try {
      await updateGuardJobTitle(t.id, { [flag]: true })
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
    }
  }

  const toggleActive = async (t: GuardJobTitle) => {
    try {
      await updateGuardJobTitle(t.id, { is_active: !t.is_active })
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
    }
  }

  const remove = async (t: GuardJobTitle) => {
    const question = t.usage_count
      ? `На должности «${t.name}» стоят ${t.usage_count} строк табеля — удалить её нельзя, она будет снята (пропадёт из выбора, в истории останется). Снять?`
      : `Удалить должность «${t.name}»?`
    if (!window.confirm(question)) return
    try {
      const { result } = await deleteGuardJobTitle(t.id)
      toast.success(result === 'deleted' ? 'Должность удалена' : 'Должность снята')
      await reload()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось удалить')
    }
  }

  if (loading) return <p className="p-8 text-center text-sm text-slate-400">Загрузка…</p>

  return (
    <div className="max-w-4xl">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Должность задаёт способ оплаты: ставка за смену или оклад за месяц. С должностью
          «по умолчанию» человек встаёт на пост объекта или в экипаж ГБР, если её не выбрали.
        </p>
        {canManage && (
          <Button size="sm" onClick={() => setEditing('new')}>
            Добавить должность
          </Button>
        )}
      </div>

      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
          <tr>
            <th className="px-3 py-2">Название</th>
            <th className="px-3 py-2">Оплата</th>
            <th className="px-3 py-2">По умолчанию</th>
            <th className="px-3 py-2 text-right">Строк табеля</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {titles.map((t) => (
            <tr
              key={t.id}
              className={`border-t border-slate-100 ${t.is_active ? '' : 'text-slate-400'}`}
            >
              <td className="px-3 py-2">
                {t.name}
                {!t.is_active && <span className="ml-2 text-xs">снята</span>}
              </td>
              <td className="px-3 py-2">{t.pay_type_label}</td>
              <td className="px-3 py-2 text-xs">
                {t.default_for_post && <span className="mr-2 rounded bg-slate-100 px-1.5 py-0.5">пост</span>}
                {t.default_for_crew && <span className="rounded bg-slate-100 px-1.5 py-0.5">экипаж ГБР</span>}
                {canManage && t.is_active && !t.default_for_post && (
                  <button
                    type="button"
                    onClick={() => void setDefault(t, 'default_for_post')}
                    className="mr-2 cursor-pointer text-blue-700 hover:underline"
                  >
                    для поста
                  </button>
                )}
                {canManage && t.is_active && !t.default_for_crew && (
                  <button
                    type="button"
                    onClick={() => void setDefault(t, 'default_for_crew')}
                    className="cursor-pointer text-blue-700 hover:underline"
                  >
                    для экипажа
                  </button>
                )}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{t.usage_count}</td>
              <td className="px-3 py-2 text-right text-xs">
                {canManage && (
                  <>
                    <button
                      type="button"
                      onClick={() => setEditing(t)}
                      className="mr-3 cursor-pointer text-blue-700 hover:underline"
                    >
                      Изменить
                    </button>
                    {t.is_active ? (
                      <button
                        type="button"
                        onClick={() => void remove(t)}
                        className="cursor-pointer text-red-600 hover:underline"
                      >
                        {t.usage_count ? 'Снять' : 'Удалить'}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void toggleActive(t)}
                        className="cursor-pointer text-blue-700 hover:underline"
                      >
                        Вернуть
                      </button>
                    )}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing && (
        <JobTitleModal
          title={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null)
            void reload()
          }}
        />
      )}
    </div>
  )
}

function JobTitleModal({
  title,
  onClose,
  onDone,
}: {
  title: GuardJobTitle | null
  onClose: () => void
  onDone: () => void
}) {
  const [name, setName] = useState(title?.name ?? '')
  const [payType, setPayType] = useState<GuardPayType>(title?.pay_type ?? 'per_shift')
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (
      title &&
      payType !== title.pay_type &&
      title.usage_count > 0 &&
      !window.confirm(
        `На должности «${title.name}» стоят ${title.usage_count} строк табеля. Смена способа ` +
          'оплаты пересчитает все незакрытые месяцы, где стоят эти люди. Продолжить?',
      )
    )
      return
    setSaving(true)
    try {
      if (title) await updateGuardJobTitle(title.id, { name: name.trim(), pay_type: payType })
      else await createGuardJobTitle({ name: name.trim(), pay_type: payType })
      toast.success('Сохранено')
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={title ? `Должность: ${title.name}` : 'Новая должность'}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button onClick={save} loading={saving} disabled={name.trim().length < 2}>
            Сохранить
          </Button>
        </>
      }
    >
      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-gray-600">Название</span>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        />
      </label>
      <label className="block text-sm">
        <span className="mb-1 block text-gray-600">Способ оплаты</span>
        <select
          value={payType}
          onChange={(e) => setPayType(e.target.value as GuardPayType)}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        >
          <option value="per_shift">Ставка за смену</option>
          <option value="salary">Оклад за месяц (половина за полмесяца, пропорционально дням)</option>
        </select>
      </label>
    </Modal>
  )
}
