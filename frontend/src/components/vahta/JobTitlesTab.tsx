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
import { DsButton } from '../ds/Button'
import { RowMenu, type MenuItem } from '../ds/Menu'

/**
 * Справочник должностей охраны — вкладка настроек вахты.
 *
 * Должность задаёт ОДНО: способ оплаты (ставка за смену / оклад за месяц).
 * Названий — сколько нужно заказчику: «Старший смены», «Оператор
 * видеонаблюдения» и т.п. Две должности помечены «по умолчанию» — с ними
 * встают на пост объекта и в экипаж ГБР, если должность не выбрали явно.
 *
 * Смена способа оплаты у должности, на которой уже стоят люди, пересчитывает
 * открытые месяцы — об этом спрашивается подтверждение; есть строки в ЗАКРЫТЫХ
 * месяцах — бэк отказывает (409), снапшота расчёта в системе нет.
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
    const used = t.usage_count + t.staff_count
    const question = used
      ? `На должности «${t.name}» стоят ${t.usage_count} строк табеля и ${t.staff_count} рабочих мест — удалить её нельзя, она будет снята (пропадёт из выбора, у людей и в истории останется). Снять?`
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

  if (loading) return <p className="text-[13px] text-ds-muted">Загрузка…</p>

  // Действия строки — в меню «⋯», видимом всегда (редизайн §4.3): красное
  // «Снять» в каждой строке было самым заметным элементом списка.
  const menuFor = (t: GuardJobTitle): MenuItem[] => {
    if (!canManage) return []
    const items: MenuItem[] = [{ label: 'Изменить…', onSelect: () => setEditing(t) }]
    if (t.is_active && !t.default_for_post)
      items.push({ label: 'Сделать по умолчанию для поста', onSelect: () => void setDefault(t, 'default_for_post') })
    if (t.is_active && !t.default_for_crew)
      items.push({
        label: 'Сделать по умолчанию для экипажа ГБР',
        onSelect: () => void setDefault(t, 'default_for_crew'),
      })
    if (t.is_active)
      items.push({
        label: t.usage_count + t.staff_count ? 'Снять…' : 'Удалить…',
        hint: t.usage_count + t.staff_count ? 'пропадёт из выбора, в истории останется' : undefined,
        danger: true,
        onSelect: () => void remove(t),
      })
    else items.push({ label: 'Вернуть в выбор', onSelect: () => void toggleActive(t) })
    return items
  }

  const defaults = (t: GuardJobTitle) =>
    [t.default_for_post ? 'для поста' : '', t.default_for_crew ? 'для экипажа ГБР' : '']
      .filter(Boolean)
      .join(', ')

  const th = 'border-b border-ds-line-strong bg-ds-surface-2 px-3 py-2 text-[11px] font-semibold text-ds-muted'
  const td = 'border-b border-ds-line px-3 py-2 align-middle'

  return (
    <div className="max-w-[980px]">
      {canManage && (
        <div className="mb-3">
          <DsButton variant="primary" icon="plus" onClick={() => setEditing('new')}>
            Добавить должность
          </DsButton>
        </div>
      )}
      <p className="mb-3 text-[12.5px] text-ds-muted">
        Должность задаёт способ оплаты. «По умолчанию» — какая должность подставляется, когда
        человека ставят на пост или в экипаж.
      </p>

      <table className="w-full border-separate border-spacing-0 overflow-hidden rounded-ds-lg border border-ds-line bg-ds-surface text-[13px]">
        <thead>
          <tr>
            <th className={`${th} text-left`}>Название</th>
            <th className={`${th} text-left`}>Оплата</th>
            <th className={`${th} text-left`}>По умолчанию</th>
            <th className={`${th} text-right`}>Рабочих мест</th>
            <th className={`${th} text-right`}>Строк табеля</th>
            <th className={`${th} w-12`}>
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {titles.map((t) => (
            <tr key={t.id} className={t.is_active ? '' : 'text-ds-muted'}>
              <td className={td}>
                <b className={t.is_active ? 'font-semibold' : 'font-medium'}>{t.name}</b>
                {!t.is_active && <span className="ml-2 text-[12px]">снята</span>}
              </td>
              <td className={td}>{t.pay_type_label}</td>
              <td className={td}>{defaults(t) || <span className="text-ds-muted">нет</span>}</td>
              <td className={`${td} text-right tabular-nums`}>{t.staff_count}</td>
              <td className={`${td} text-right tabular-nums`}>
                {t.usage_count}
                {t.closed_usage_count > 0 && (
                  <span
                    className="ml-1 text-[12px] text-ds-muted"
                    title="в закрытых месяцах — способ оплаты уже не сменить"
                  >
                    ({t.closed_usage_count} закр.)
                  </span>
                )}
              </td>
              <td className={`${td} text-right`}>
                <RowMenu items={menuFor(t)} label={`Действия: ${t.name}`} />
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
          'оплаты пересчитает открытые месяцы, где стоят эти люди' +
          (title.closed_usage_count > 0
            ? `; в закрытых месяцах (${title.closed_usage_count} строк) она не допускается — сохранение будет отклонено`
            : '') +
          '. Продолжить?',
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
