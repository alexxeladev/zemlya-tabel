import { useEffect, useMemo, useState } from 'react'

import { listVahtaCandidates, replaceOnPost } from '../../api/vahta'
import { Button } from '../Button'
import { Modal } from '../Modal'
import { toast } from '../../store/toasts'
import type { VahtaCandidate, VahtaRow } from '../../types/api'

/**
 * «Кто сменит на посту».
 *
 * Дни с выбранного числа уходят сменщику ОТДЕЛЬНОЙ строкой на тот же пост,
 * прежний остаётся со своими. Замена с первого числа, когда прежнему не
 * остаётся ни одного дня, второй строки не создаёт — просто меняется человек.
 *
 * Список в трёх группах, как в утверждённом прототипе: из других экипажей (с
 * пометкой откуда), свободные и «оформить нового».
 */
export function ReplaceModal({
  row,
  year,
  month,
  daysInMonth,
  midDay,
  onClose,
  onDone,
}: {
  row: VahtaRow
  year: number
  month: number
  daysInMonth: number
  midDay: number
  onClose: () => void
  onDone: () => void
}) {
  const [candidates, setCandidates] = useState<VahtaCandidate[]>([])
  const [fromDay, setFromDay] = useState(midDay + 1)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    listVahtaCandidates(year, month).then(setCandidates).catch(() => setCandidates([]))
  }, [year, month])

  const lastWorked = useMemo(
    () => (row.days.length ? Math.max(...row.days) : 0),
    [row.days],
  )

  const busy = candidates.filter((c) => c.where && c.employee_id !== row.employee_id)
  const free = candidates.filter((c) => !c.where && c.employee_id !== row.employee_id)

  const pick = async (candidate: VahtaCandidate) => {
    setSaving(true)
    try {
      const result = await replaceOnPost({
        assignment_id: row.id,
        employee_id: candidate.employee_id,
        from_day: fromDay,
      })
      toast.success(
        result.successor_id
          ? 'Сменщик поставлен, дни разделены'
          : 'Человек на посту заменён',
      )
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Не удалось поставить сменщика')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title="Кто сменит на посту"
      actions={
        <div className="flex w-full items-center justify-between">
          <label className="text-sm text-gray-600">
            Сменяет с{' '}
            <input
              type="number"
              min={1}
              max={daysInMonth}
              value={fromDay}
              onChange={(e) => setFromDay(Number(e.target.value))}
              className="w-16 rounded border border-gray-300 px-2 py-1 text-center"
            />{' '}
            числа
          </label>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
        </div>
      }
    >
      <p className="mb-3 text-sm text-gray-500">
        {row.post_name} · {row.kind_label}. Сейчас{' '}
        {row.employee_name ?? 'никого нет'}
        {lastWorked > 0 ? `, отработал по ${lastWorked} число` : ''}.
      </p>

      <div className="max-h-72 overflow-auto">
        {busy.length > 0 && (
          <>
            <p className="px-1 pb-1 pt-2 text-[10px] uppercase tracking-wide text-gray-400">
              Из других экипажей
            </p>
            {busy.map((c) => (
              <button
                key={c.position_id ?? c.employee_id}
                type="button"
                disabled={saving}
                onClick={() => void pick(c)}
                className="flex w-full cursor-pointer items-baseline justify-between gap-3 rounded px-2 py-2 text-left text-sm hover:bg-gray-50"
              >
                <span className="text-slate-800">{c.full_name}</span>
                <span className="text-[11px] text-gray-400">{c.where}</span>
              </button>
            ))}
          </>
        )}

        <p className="px-1 pb-1 pt-3 text-[10px] uppercase tracking-wide text-gray-400">
          Свободны
        </p>
        {free.length === 0 && <p className="px-2 py-1 text-sm text-gray-400">Никого</p>}
        {free.map((c) => (
          <button
            key={c.position_id ?? c.employee_id}
            type="button"
            disabled={saving}
            onClick={() => void pick(c)}
            className="block w-full cursor-pointer rounded px-2 py-2 text-left text-sm text-slate-800 hover:bg-gray-50"
          >
            {c.full_name}
          </button>
        ))}

        <p className="px-1 pb-1 pt-3 text-[10px] uppercase tracking-wide text-gray-400">
          Нет в списке
        </p>
        <p className="px-2 py-1 text-xs text-gray-500">
          Оформить нового можно кнопкой «Оформить нового» внизу карточки экипажа —
          он появится и здесь, и в общем справочнике сотрудников.
        </p>
      </div>
    </Modal>
  )
}
